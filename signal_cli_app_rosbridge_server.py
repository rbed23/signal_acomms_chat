"""
UPLINK: ACOMMS chat network -> Signal app.

Subscribes to the rosbridge uplink topic and sends each chat message to its
Signal recipient(s) through signal-cli-rest-api (POST /v2/send).

Usage:  python signal_cli_app_rosbridge_server.py [-c config.toml] [--log-level DEBUG]
"""
from __future__ import annotations

import threading

from utils.config import Config
from utils.log import LOGGER
from utils.messages import decode_uplink, format_for_signal
from utils.rosbridge import RosbridgeClient
from utils.runtime import install_stop_handlers, start
from utils.signal_api import SignalClient


class UplinkBridge:
    def __init__(self, cfg: Config, stop: threading.Event,
                 signal_client: SignalClient | None = None,
                 ros_client: RosbridgeClient | None = None):
        self.cfg = cfg
        self.stop = stop
        r = cfg.rosbridge
        self.signal = signal_client or SignalClient(
            cfg.signal, stop, r.reconnect_initial_seconds, r.reconnect_max_seconds)
        self.ros = ros_client or RosbridgeClient(r, stop, name="uplink")
        self.ros.subscribe(r.uplink_topic, r.message_type)

    def handle(self, frame: dict) -> bool:
        """Process one rosbridge frame. True if a Signal message was sent."""
        if frame.get("op") != "publish" or frame.get("topic") != self.cfg.rosbridge.uplink_topic:
            LOGGER.debug(f"Ignoring rosbridge frame op={frame.get('op')} topic={frame.get('topic')}")
            return False
        up = decode_uplink(frame.get("msg"), self.cfg.bridge.default_recipients)
        if up is None:
            LOGGER.warning(f"No message text in uplink frame: {frame}")
            return False
        if not up.recipients:
            LOGGER.warning(f"DROPPED uplink message with no recipient (and no "
                           f"bridge.default_recipients): {up.text!r}")
            return False
        text = format_for_signal(up)
        if self.signal.send(text, up.recipients):
            LOGGER.info(f"UPLINK '{text}' -> Signal {list(up.recipients)}")
            return True
        LOGGER.error(f"FAILED to send uplink message to {list(up.recipients)}: {text!r}")
        return False

    def run(self) -> None:
        LOGGER.info(f"Subscribing to {self.cfg.rosbridge.uplink_topic} "
                    f"({self.cfg.rosbridge.message_type}) on {self.cfg.rosbridge.url}")
        if not self.cfg.bridge.default_recipients:
            LOGGER.info("No bridge.default_recipients set: uplink messages must name a recipient")
        try:
            for frame in self.ros.messages():
                self.handle(frame)
        finally:
            self.ros.close()


def main() -> None:
    cfg = start("ACOMMS -> Signal uplink handler", "signal_cli_app_rosbridge.log")
    stop = threading.Event()
    install_stop_handlers(stop)
    LOGGER.info("ROSBRIDGE HANDLER starting...")
    try:
        UplinkBridge(cfg, stop).run()
    except Exception:  # noqa: BLE001
        LOGGER.exception("Unexpected error in uplink handler")
        raise SystemExit(1)
    finally:
        LOGGER.info("ROSBRIDGE HANDLER exited.")


if __name__ == "__main__":
    main()
