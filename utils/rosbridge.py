"""
Minimal rosbridge v2 protocol client with automatic reconnection.

One persistent WebSocket is kept open. After every (re)connect the client
re-sends its ``advertise`` and ``subscribe`` operations so a restarted
rosbridge picks up where it left off.
"""
from __future__ import annotations

import json
import threading
from typing import Iterator

import websocket  # websocket-client

from utils.config import RosbridgeConfig
from utils.log import LOGGER


class Backoff:
    """Exponential backoff that waits on a stop event (so Ctrl+C is instant)."""

    def __init__(self, initial: float, maximum: float, stop: threading.Event):
        self.initial, self.maximum, self.stop = initial, maximum, stop
        self.delay = initial

    def reset(self) -> None:
        self.delay = self.initial

    def wait(self) -> bool:
        """Sleep for the current delay; return False if stop was requested."""
        stopped = self.stop.wait(self.delay)
        self.delay = min(self.delay * 2, self.maximum)
        return not stopped


class RosbridgeClient:
    RECV_POLL_SECONDS = 1.0

    def __init__(self, cfg: RosbridgeConfig, stop: threading.Event, name: str = "bridge"):
        self.cfg = cfg
        self.stop = stop
        self.name = name
        self._ws: websocket.WebSocket | None = None
        self._advertised: dict[str, str] = {}
        self._subscribed: dict[str, str] = {}
        self._backoff = Backoff(cfg.reconnect_initial_seconds, cfg.reconnect_max_seconds, stop)

    # ------------------------------------------------------------ lifecycle
    @property
    def connected(self) -> bool:
        return self._ws is not None and self._ws.connected

    def connect(self) -> bool:
        """Block until connected (retrying with backoff). False if stopping."""
        while not self.stop.is_set():
            try:
                ws = websocket.create_connection(self.cfg.url, timeout=10)
                ws.settimeout(self.RECV_POLL_SECONDS)
                self._ws = ws
                for topic, type_ in self._advertised.items():
                    self._send_raw({"op": "advertise", "topic": topic, "type": type_})
                for topic, type_ in self._subscribed.items():
                    self._send_raw({"op": "subscribe", "topic": topic, "type": type_})
                LOGGER.info(f"Connected to rosbridge at {self.cfg.url}")
                self._backoff.reset()
                return True
            except (OSError, websocket.WebSocketException) as e:
                self._drop()
                LOGGER.warning(f"rosbridge connect to {self.cfg.url} failed ({e}); "
                               f"retrying in {self._backoff.delay:.1f}s")
                if not self._backoff.wait():
                    break
        return False

    def close(self) -> None:
        if self._ws is not None:
            for topic in self._subscribed:
                self._try_send({"op": "unsubscribe", "topic": topic})
            for topic in self._advertised:
                self._try_send({"op": "unadvertise", "topic": topic})
        self._drop(graceful=True)

    def _drop(self, graceful: bool = False) -> None:
        """Forget the socket. graceful=True performs a close handshake first."""
        if self._ws is not None:
            try:
                if graceful:
                    self._ws.close(timeout=1)
                else:
                    # peer already closed or the link is broken: no handshake
                    self._ws.shutdown()
            except Exception:  # noqa: BLE001 - closing a broken socket
                pass
        self._ws = None

    # ------------------------------------------------------------ operations
    def _send_raw(self, obj: dict) -> None:
        assert self._ws is not None
        self._ws.send(json.dumps(obj))

    def _try_send(self, obj: dict) -> None:
        try:
            self._send_raw(obj)
        except Exception:  # noqa: BLE001 - best effort during shutdown
            pass

    def advertise(self, topic: str, type_: str) -> None:
        self._advertised[topic] = type_
        if self.connected:
            self._send_raw({"op": "advertise", "topic": topic, "type": type_})

    def subscribe(self, topic: str, type_: str) -> None:
        self._subscribed[topic] = type_
        if self.connected:
            self._send_raw({"op": "subscribe", "topic": topic, "type": type_})

    def publish(self, topic: str, msg: dict) -> bool:
        """Publish, reconnecting as needed. Returns False only when stopping."""
        frame = {"op": "publish", "topic": topic, "msg": msg}
        while not self.stop.is_set():
            if not self.connected and not self.connect():
                return False
            try:
                self._send_raw(frame)
                return True
            except (OSError, websocket.WebSocketException) as e:
                LOGGER.warning(f"rosbridge publish failed ({e}); reconnecting")
                self._drop()
        return False

    def messages(self) -> Iterator[dict]:
        """Yield decoded JSON frames forever, reconnecting on errors."""
        while not self.stop.is_set():
            if not self.connected and not self.connect():
                return
            try:
                raw = self._ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except (OSError, websocket.WebSocketException) as e:
                LOGGER.warning(f"rosbridge connection lost ({e}); reconnecting")
                self._drop()
                if not self._backoff.wait():
                    return
                continue
            if not raw:
                # empty frame / close
                LOGGER.warning("rosbridge closed the connection; reconnecting")
                self._drop()
                if not self._backoff.wait():
                    return
                continue
            LOGGER.debug(f"WEBSOCKET RX: {raw}")
            try:
                obj = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                LOGGER.warning(f"Ignoring non-JSON frame from rosbridge: {raw!r}")
                continue
            if isinstance(obj, dict):
                yield obj
