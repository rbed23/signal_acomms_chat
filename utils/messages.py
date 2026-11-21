"""
Signal envelope parsing, sender/length policy, and rosbridge payload encoding.

This module has no network code so it can be unit-tested directly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from utils.config import BridgeConfig


# ---------------------------------------------------------------- Signal side
def _ts(value: Any) -> float | None:
    try:
        return int(value) / 1000
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class EnvelopeMetaData:
    source_number: str | None
    source_uuid: str | None
    source_name: str | None
    timestamp: float | None

    @classmethod
    def from_envelope(cls, env: dict) -> "EnvelopeMetaData":
        number = env.get("sourceNumber")
        uuid = env.get("sourceUuid")
        legacy = env.get("source")  # number or uuid, depending on version
        if legacy and not number and str(legacy).startswith("+"):
            number = legacy
        if legacy and not uuid and not str(legacy).startswith("+"):
            uuid = legacy
        return cls(number, uuid, env.get("sourceName") or None, _ts(env.get("timestamp")))

    @property
    def label(self) -> str:
        """Human-readable sender label: profile name, else number, else uuid."""
        return self.source_name or self.source_number or self.source_uuid or "UNKNOWN"

    @property
    def identifiers(self) -> set[str]:
        return {x for x in (self.source_number, self.source_uuid) if x}

    @property
    def reply_address(self) -> str | None:
        return self.source_number or self.source_uuid


@dataclass(frozen=True)
class DataMessage:
    meta: EnvelopeMetaData
    text: str
    timestamp: float | None
    group_id: str | None

    @property
    def packaged(self) -> str:
        return f"{self.meta.label.upper()}]{self.text}"


@dataclass(frozen=True)
class TypingMessage:
    meta: EnvelopeMetaData
    action: str
    timestamp: float | None


@dataclass(frozen=True)
class ReceiptMessage:
    meta: EnvelopeMetaData
    status: str
    timestamp: float | None


@dataclass(frozen=True)
class OtherMessage:
    meta: EnvelopeMetaData
    kind: str


SignalEvent = DataMessage | TypingMessage | ReceiptMessage | OtherMessage


def parse_receive_payload(payload: Any) -> list[SignalEvent]:
    """
    Parse what /v1/receive returns. Poll mode returns a JSON list of
    ``{"envelope": {...}, "account": ...}`` objects; websocket mode sends one
    such object per frame. Both shapes (and bare envelopes) are accepted.
    """
    if payload is None:
        return []
    items = payload if isinstance(payload, list) else [payload]
    events: list[SignalEvent] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        env = item.get("envelope", item)
        if not isinstance(env, dict):
            continue
        ev = parse_envelope(env)
        if ev is not None:
            events.append(ev)
    return events


def parse_envelope(env: dict) -> SignalEvent | None:
    meta = EnvelopeMetaData.from_envelope(env)
    if isinstance(env.get("dataMessage"), dict):
        dm = env["dataMessage"]
        text = dm.get("message")
        if text:  # None for reactions, attachment-only messages, etc.
            group = (dm.get("groupInfo") or {}).get("groupId")
            return DataMessage(meta, text, _ts(dm.get("timestamp")), group)
        kind = "reaction" if dm.get("reaction") else \
            "attachment" if dm.get("attachments") else "empty dataMessage"
        return OtherMessage(meta, kind)
    if isinstance(env.get("typingMessage"), dict):
        tm = env["typingMessage"]
        return TypingMessage(meta, str(tm.get("action", "")), _ts(tm.get("timestamp")))
    if isinstance(env.get("receiptMessage"), dict):
        rm = env["receiptMessage"]
        status = "Delivered" if rm.get("isDelivery") else "Read" if rm.get("isRead") \
            else "Viewed" if rm.get("isViewed") else "Unknown"
        return ReceiptMessage(meta, status, _ts(rm.get("when")))
    for key in ("syncMessage", "callMessage", "editMessage", "storyMessage"):
        if key in env:
            return OtherMessage(meta, key)
    return None


def fmt_ts(ts: float | None) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else "unknown time"


# --------------------------------------------------------------------- policy
def sender_allowed(meta: EnvelopeMetaData, cfg: BridgeConfig) -> bool:
    if cfg.allow_all_senders:
        return True
    return bool(meta.identifiers & set(cfg.allowed_senders))


def truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if max_bytes <= 0 or len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


@dataclass(frozen=True)
class LengthDecision:
    text: str | None      # None when the message is rejected
    original_bytes: int
    changed: bool


def apply_length_policy(text: str, cfg: BridgeConfig) -> LengthDecision:
    size = len(text.encode("utf-8"))
    if cfg.max_message_bytes <= 0 or size <= cfg.max_message_bytes:
        return LengthDecision(text, size, False)
    if cfg.oversize_policy == "reject":
        return LengthDecision(None, size, True)
    return LengthDecision(truncate_utf8(text, cfg.max_message_bytes), size, True)


# ----------------------------------------------------------- rosbridge side
def encode_downlink(text: str, meta: EnvelopeMetaData, timestamp: float | None,
                    payload_format: str) -> dict:
    """Build the rosbridge ``msg`` object for a Signal -> ACOMMS message."""
    if payload_format == "json":
        body = {"text": text, "source": meta.source_number, "source_uuid": meta.source_uuid,
                "source_name": meta.source_name, "timestamp": timestamp}
        return {"data": json.dumps(body, separators=(",", ":"))}
    if payload_format == "text":
        return {"data": f"{meta.label}]{text}"}
    # legacy: what the original test implementation sent
    return {"data": text, "source": meta.source_number, "source_name": meta.source_name}


@dataclass(frozen=True)
class UplinkMessage:
    text: str
    label: str | None
    recipients: tuple[str, ...]


def _as_recipients(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return []


def decode_uplink(msg: Any, default_recipients: tuple[str, ...] = ()) -> UplinkMessage | None:
    """
    Turn a rosbridge ``msg`` from the uplink topic into text + recipients.

    Accepts, in ``msg["data"]``, either a JSON object
    (``{"text", "recipient" | "recipients", "source_name" | "source"}``) or
    plain text. Non-standard fields next to ``data`` (the legacy format:
    ``recipient-number``, ``source_name``, ``source``) are honoured too.
    Returns None when there is no text.
    """
    if not isinstance(msg, dict) or msg.get("data") in (None, ""):
        return None
    data = msg["data"]
    text: str | None = None
    label: str | None = None
    recipients: list[str] = []

    if isinstance(data, str) and data.lstrip().startswith("{"):
        try:
            body = json.loads(data)
        except json.JSONDecodeError:
            body = None
        if isinstance(body, dict) and body.get("text") not in (None, ""):
            text = str(body["text"])
            label = body.get("source_name") or body.get("source")
            recipients = _as_recipients(body.get("recipients")) + _as_recipients(body.get("recipient"))
    if text is None:
        text = str(data)

    label = label or msg.get("source_name") or msg.get("source")
    recipients += _as_recipients(msg.get("recipient-number")) + _as_recipients(msg.get("recipients"))
    if not recipients:
        recipients = list(default_recipients)
    # de-duplicate, keep order
    recipients = list(dict.fromkeys(recipients))
    return UplinkMessage(text, str(label) if label else None, tuple(recipients))


def format_for_signal(msg: UplinkMessage) -> str:
    return f"{msg.label}]{msg.text}" if msg.label else msg.text
