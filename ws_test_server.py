"""
Fake rosbridge server for local testing (no ROS needed).

Implements the subset of the rosbridge v2 protocol the bridge uses:
``advertise``, ``unadvertise``, ``subscribe``, ``unsubscribe`` and
``publish``. Published messages are delivered only to clients subscribed to
that topic, as real rosbridge does. Every publish on the chat topics is
logged as a FAKE ROSBRIDGE SERVER ACTION.

Usage:  python ws_test_server.py [-c config.toml] [--host 0.0.0.0]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from collections import defaultdict

import websockets
from websockets.exceptions import ConnectionClosed

from utils.config import ConfigError, load_config
from utils.log import LOGGER, setup_logging
from utils.messages import decode_uplink

SUBSCRIPTIONS: dict[str, set] = defaultdict(set)


def describe_downlink(msg: dict) -> str:
    data = msg.get("data")
    try:
        body = json.loads(data)
        if isinstance(body, dict) and "text" in body:
            who = body.get("source_name") or body.get("source") or body.get("source_uuid")
            return f"'{str(who).upper()}]{body['text']}' (json payload)"
    except (TypeError, ValueError):
        pass
    if "source" in msg or "source_name" in msg:
        who = msg.get("source_name") or msg.get("source")
        return f"'{str(who).upper()}]{data}' (legacy payload)"
    return f"'{data}' (text payload)"


async def handler(ws, downlink_topic: str, uplink_topic: str) -> None:
    peer = ws.request.headers.get("Origin") or ws.request.headers.get("Host")
    LOGGER.info(f"Client connected: {ws.id} ({peer}, {ws.remote_address})")
    try:
        async for raw in ws:
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                LOGGER.warning(f"Non-JSON frame from {ws.id}: {raw!r}")
                continue
            op, topic = frame.get("op"), frame.get("topic")
            if op == "subscribe":
                SUBSCRIPTIONS[topic].add(ws)
                LOGGER.info(f"{ws.id} subscribed to {topic} ({frame.get('type')})")
            elif op == "unsubscribe":
                SUBSCRIPTIONS[topic].discard(ws)
                LOGGER.info(f"{ws.id} unsubscribed from {topic}")
            elif op in ("advertise", "unadvertise"):
                LOGGER.info(f"{ws.id} {op}d {topic} ({frame.get('type', '')})")
            elif op == "publish":
                msg = frame.get("msg") or {}
                if topic == downlink_topic:
                    LOGGER.info(f"FAKE ROSBRIDGE SERVER ACTION: PUBLISH {describe_downlink(msg)} "
                                f"on '{topic}' to ACOUSTIC NETWORK")
                elif topic == uplink_topic:
                    up = decode_uplink(msg)
                    text = up.text if up else msg.get("data")
                    to = list(up.recipients) if up else []
                    LOGGER.info(f"FAKE ROSBRIDGE SERVER ACTION: PUBLISH '{text}' on '{topic}' "
                                f"to SIGNAL APP (recipients: {to or 'default'})")
                else:
                    LOGGER.info(f"PUBLISH on {topic}: {msg}")
                out = json.dumps({"op": "publish", "topic": topic, "msg": msg})
                websockets.broadcast(set(SUBSCRIPTIONS.get(topic, ())), out)
            else:
                LOGGER.warning(f"Unsupported op {op!r} from {ws.id}")
    except ConnectionClosed:
        pass
    finally:
        LOGGER.info(f"Client disconnected: {ws.id} (close code {ws.close_code})")
        for subs in SUBSCRIPTIONS.values():
            subs.discard(ws)


async def serve(host: str, port: int, downlink: str, uplink: str) -> None:
    stop = asyncio.get_running_loop().create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, lambda: stop.done() or stop.set_result(None))
    async with websockets.serve(lambda ws: handler(ws, downlink, uplink), host, port,
                                open_timeout=2, close_timeout=1):
        LOGGER.info(f"Fake rosbridge listening on ws://{host}:{port} "
                    f"(downlink {downlink}, uplink {uplink})")
        await stop
    LOGGER.info("Fake rosbridge stopped.")


def main() -> None:
    p = argparse.ArgumentParser(description="Fake rosbridge server for testing")
    p.add_argument("-c", "--config")
    p.add_argument("--host", default=None, help="bind address (default: rosbridge.host)")
    p.add_argument("--port", type=int, default=None, help="port (default: rosbridge.port)")
    args = p.parse_args()
    try:
        cfg = load_config(args.config, require_server_number=False)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(2)
    setup_logging("signal_cli_app_ws_server.log", cfg.logging.level, cfg.logging.dir)
    r = cfg.rosbridge
    asyncio.run(serve(args.host or r.host, args.port or r.port, r.downlink_topic, r.uplink_topic))


if __name__ == "__main__":
    main()
