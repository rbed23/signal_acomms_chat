"""
DOWNLINK: Signal app -> ACOMMS chat network.

Receives messages for the bridge's Signal number from signal-cli-rest-api
(streaming WebSocket in MODE=json-rpc, HTTP polling in MODE=normal/native)
and publishes allowed text messages to the rosbridge downlink topic.

Usage:  python signal_cli_app_listener_daemon.py [-c config.toml] [--log-level DEBUG]
"""
from __future__ import annotations

import threading

from utils.config import Config
from utils.log import LOGGER
from utils.messages import (
    DataMessage, OtherMessage, ReceiptMessage, TypingMessage,
    apply_length_policy, encode_downlink, fmt_ts, parse_receive_payload, sender_allowed,
)
from utils.rosbridge import RosbridgeClient
from utils.runtime import install_stop_handlers, start
from utils.signal_api import SignalClient


class DownlinkBridge:
    def __init__(self, cfg: Config, stop: threading.Event,
                 signal_client: SignalClient | None = None,
                 ros_client: RosbridgeClient | None = None):
        self.cfg = cfg
        self.stop = stop
        r = cfg.rosbridge
        self.signal = signal_client or SignalClient(
            cfg.signal, stop, r.reconnect_initial_seconds, r.reconnect_max_seconds)
        self.ros = ros_client or RosbridgeClient(r, stop, name="downlink")
        self.ros.advertise(r.downlink_topic, r.message_type)

    def warn_about_policy(self) -> None:
        b = self.cfg.bridge
        if b.allow_all_senders:
            LOGGER.warning("bridge.allow_all_senders is true: ANY Signal user can send "
                           "into the acoustic network.")
        elif not b.allowed_senders:
            LOGGER.warning("bridge.allowed_senders is empty: every incoming message will be "
                           "dropped. Add numbers/UUIDs to allowed_senders.")
        else:
            LOGGER.info(f"{len(b.allowed_senders)} sender(s) allowed into the acoustic network")

    def _notify(self, address: str | None, text: str) -> None:
        if address and self.cfg.bridge.notify_sender_on_oversize:
            self.signal.send(text, [address])

    def handle(self, payload) -> int:
        """Process one receive payload. Returns the number of messages published."""
        published = 0
        b, r = self.cfg.bridge, self.cfg.rosbridge
        for ev in parse_receive_payload(payload):
            who = (ev.meta.source_number or ev.meta.source_uuid, ev.meta.source_name)
            if isinstance(ev, DataMessage):
                if not sender_allowed(ev.meta, b):
                    LOGGER.warning(f"DROPPED message from non-allowed sender {who}")
                    continue
                decision = apply_length_policy(ev.text, b)
                limit = b.max_message_bytes
                if decision.text is None:
                    LOGGER.warning(f"REJECTED {decision.original_bytes}-byte message from {who} "
                                   f"(limit {limit} bytes)")
                    self._notify(ev.meta.reply_address,
                                 f"Not sent: message is {decision.original_bytes} bytes; "
                                 f"the acoustic link limit is {limit} bytes.")
                    continue
                if decision.changed:
                    LOGGER.warning(f"TRUNCATED message from {who} from "
                                   f"{decision.original_bytes} to {limit} bytes")
                    self._notify(ev.meta.reply_address,
                                 f"Your message was cut to {limit} bytes for the acoustic link.")
                msg = encode_downlink(decision.text, ev.meta, ev.timestamp, r.payload_format)
                if not self.ros.publish(r.downlink_topic, msg):
                    LOGGER.error(f"Shutdown before message from {who} could be published")
                    return published
                published += 1
                LOGGER.info(f"DOWNLINK {who} '{ev.meta.label.upper()}]{decision.text}' "
                            f"sent at {fmt_ts(ev.timestamp)} -> {r.downlink_topic}")
            elif isinstance(ev, ReceiptMessage):
                LOGGER.debug(f"Receipt from {who}: {ev.status} at {fmt_ts(ev.timestamp)}")
            elif isinstance(ev, TypingMessage):
                LOGGER.debug(f"Typing from {who}: {ev.action} at {fmt_ts(ev.timestamp)}")
            elif isinstance(ev, OtherMessage):
                LOGGER.info(f"Not forwarded ({ev.kind}) from {who}")
        return published

    def run(self) -> None:
        self.warn_about_policy()
        # Connect to rosbridge first: in poll mode Signal deletes messages once
        # fetched, so we should not fetch until we can deliver them.
        if not self.ros.connect():
            return
        mode = self.signal.resolve_receive_mode()
        if mode == "websocket":
            stream = self.signal.stream_forever()
        else:
            LOGGER.info(f"Polling {self.cfg.signal.http_base}{self.signal.receive_path} every "
                        f"{self.cfg.signal.poll_interval_seconds:g}s")
            stream = self.signal.poll_forever()
        try:
            for payload in stream:
                LOGGER.debug(f"SIGNAL RX: {payload}")
                self.handle(payload)
        finally:
            self.ros.close()


def main() -> None:
    cfg = start("Signal -> ACOMMS downlink listener", "signal_cli_app_listener.log")
    stop = threading.Event()
    install_stop_handlers(stop)
    LOGGER.info("SIGNAL CLI LISTENER starting...")
    try:
        DownlinkBridge(cfg, stop).run()
    except Exception:  # noqa: BLE001 - log anything unexpected before exiting
        LOGGER.exception("Unexpected error in listener")
        raise SystemExit(1)
    finally:
        LOGGER.info("SIGNAL CLI LISTENER exited.")


if __name__ == "__main__":
    main()
