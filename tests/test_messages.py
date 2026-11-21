import json

from fake_signal_api import envelope
from utils.config import BridgeConfig
from utils.messages import (
    DataMessage, OtherMessage, ReceiptMessage, TypingMessage, apply_length_policy,
    decode_uplink, encode_downlink, format_for_signal, parse_receive_payload,
    sender_allowed, truncate_utf8,
)


def bridge_cfg(**kw):
    base = dict(allowed_senders=("+15550001111",), allow_all_senders=False, max_message_bytes=10,
                oversize_policy="truncate", notify_sender_on_oversize=True, default_recipients=())
    base.update(kw)
    return BridgeConfig(**base)


def test_parse_list_and_single():
    evs = parse_receive_payload([envelope("hi"), envelope(kind="typing"), envelope(kind="receipt")])
    assert [type(e) for e in evs] == [DataMessage, TypingMessage, ReceiptMessage]
    assert evs[0].text == "hi" and evs[0].meta.label == "alice"
    assert evs[2].status == "Delivered"
    assert isinstance(parse_receive_payload(envelope("x"))[0], DataMessage)  # websocket shape


def test_parse_tolerates_missing_fields():
    evs = parse_receive_payload([{"envelope": {"source": "+1999", "dataMessage": {"message": "yo"}}},
                                 {"envelope": {"dataMessage": {"reaction": {"emoji": "x"}}}},
                                 "garbage", None])
    assert evs[0].meta.source_number == "+1999" and evs[0].meta.label == "+1999"
    assert isinstance(evs[1], OtherMessage) and evs[1].kind == "reaction"


def test_sender_allow_list():
    ev = parse_receive_payload(envelope("hi"))[0]
    assert sender_allowed(ev.meta, bridge_cfg())
    assert sender_allowed(ev.meta, bridge_cfg(allowed_senders=("uuid-alice",)))
    assert not sender_allowed(ev.meta, bridge_cfg(allowed_senders=()))
    assert sender_allowed(ev.meta, bridge_cfg(allowed_senders=(), allow_all_senders=True))


def test_length_policy():
    assert truncate_utf8("héllo", 2) == "h"  # never splits a multi-byte character
    d = apply_length_policy("0123456789abc", bridge_cfg())
    assert d.text == "0123456789" and d.changed and d.original_bytes == 13
    assert apply_length_policy("0123456789abc", bridge_cfg(oversize_policy="reject")).text is None
    assert apply_length_policy("short", bridge_cfg()).changed is False
    assert apply_length_policy("x" * 999, bridge_cfg(max_message_bytes=0)).changed is False


def test_encode_downlink_formats():
    meta = parse_receive_payload(envelope("hi"))[0].meta
    body = json.loads(encode_downlink("hi", meta, 1.5, "json")["data"])
    assert body == {"text": "hi", "source": "+15550001111", "source_uuid": "uuid-alice",
                    "source_name": "alice", "timestamp": 1.5}
    assert encode_downlink("hi", meta, None, "text") == {"data": "alice]hi"}
    assert encode_downlink("hi", meta, None, "legacy") == {
        "data": "hi", "source": "+15550001111", "source_name": "alice"}


def test_decode_uplink_variants():
    j = decode_uplink({"data": json.dumps({"text": "hey", "recipients": ["+1a", "+1a"], "source_name": "auv1"})})
    assert (j.text, j.label, j.recipients) == ("hey", "auv1", ("+1a",))
    assert format_for_signal(j) == "auv1]hey"
    single = decode_uplink({"data": json.dumps({"text": "hey", "recipient": "+1b"})})
    assert single.recipients == ("+1b",) and format_for_signal(single) == "hey"
    legacy = decode_uplink({"data": "hey", "recipient-number": "+1c", "source_name": "auv2"})
    assert (legacy.text, legacy.label, legacy.recipients) == ("hey", "auv2", ("+1c",))
    plain = decode_uplink({"data": "hey"}, default_recipients=("+1d",))
    assert plain.recipients == ("+1d",)
    assert decode_uplink({"data": "{not json"}).text == "{not json"
    assert decode_uplink({"data": ""}) is None and decode_uplink(None) is None
