"""
Pretend to be the acoustic network when testing the bridge.

  python tools/acomms_sim.py listen
      Subscribe to the downlink topic and print what Signal users send.

  python tools/acomms_sim.py send --to +15555550123 --name auv1 "hello from the vehicle"
      Publish one chat message on the uplink topic, encoded in the
      configured rosbridge.payload_format.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websocket  # noqa: E402  websocket-client

from utils.config import ConfigError, load_config  # noqa: E402


def build_uplink(text: str, to: list[str], name: str | None, fmt: str) -> dict:
    if fmt == "json":
        body = {"text": text, "recipients": to}
        if name:
            body["source_name"] = name
        return {"data": json.dumps(body, separators=(",", ":"))}
    if fmt == "text":  # plain text, recipients come from bridge.default_recipients
        return {"data": text}
    msg = {"data": text, "recipient-number": to[0] if to else None}
    if name:
        msg["source_name"] = name
    return msg


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-c", "--config")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listen")
    s = sub.add_parser("send")
    s.add_argument("--to", action="append", default=[], help="recipient number (repeatable)")
    s.add_argument("--name", help="sender label shown in Signal, e.g. auv1")
    s.add_argument("--format", choices=["json", "text", "legacy"], help="override payload_format")
    s.add_argument("text")
    args = p.parse_args()

    try:
        cfg = load_config(args.config, require_server_number=False)
    except ConfigError as e:
        sys.exit(f"Configuration error: {e}")
    r = cfg.rosbridge
    ws = websocket.create_connection(r.url, timeout=10)

    if args.cmd == "send":
        msg = build_uplink(args.text, args.to, args.name, args.format or r.payload_format)
        ws.send(json.dumps({"op": "advertise", "topic": r.uplink_topic, "type": r.message_type}))
        ws.send(json.dumps({"op": "publish", "topic": r.uplink_topic, "msg": msg}))
        print(f"published on {r.uplink_topic}: {msg}")
        ws.close()
        return

    ws.send(json.dumps({"op": "subscribe", "topic": r.downlink_topic, "type": r.message_type}))
    ws.settimeout(None)
    print(f"listening on {r.downlink_topic} at {r.url} (Ctrl+C to stop)")
    try:
        while True:
            frame = json.loads(ws.recv())
            if frame.get("op") == "publish":
                print(f"{frame['topic']}: {frame.get('msg')}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        ws.close()


if __name__ == "__main__":
    main()
