"""
Client for bbernhard/signal-cli-rest-api.

Receiving depends on the container's MODE:

* ``MODE=normal`` / ``MODE=native``: ``GET /v1/receive/<number>`` is a plain
  HTTP request that returns (and removes) all pending messages -> "poll".
* ``MODE=json-rpc`` / ``json-rpc-native``: the same path only accepts a
  WebSocket upgrade and streams messages as they arrive -> "websocket".
"""
from __future__ import annotations

import json
import threading
from typing import Any, Iterator

import requests
import websocket  # websocket-client

from utils.config import SignalConfig
from utils.log import LOGGER
from utils.rosbridge import Backoff


class SignalApiError(RuntimeError):
    pass


class SignalClient:
    WS_RECV_POLL_SECONDS = 1.0
    SEND_ATTEMPTS = 5

    def __init__(self, cfg: SignalConfig, stop: threading.Event,
                 backoff_initial: float = 1.0, backoff_max: float = 60.0):
        self.cfg = cfg
        self.stop = stop
        self.session = requests.Session()
        self._backoff_args = (backoff_initial, backoff_max)

    def _backoff(self) -> Backoff:
        return Backoff(*self._backoff_args, self.stop)

    @property
    def receive_path(self) -> str:
        return f"/v1/receive/{self.cfg.server_number}"

    # ---------------------------------------------------------------- about
    def about(self) -> dict:
        r = self.session.get(f"{self.cfg.http_base}/v1/about",
                             timeout=self.cfg.request_timeout_seconds)
        r.raise_for_status()
        return r.json()

    def resolve_receive_mode(self) -> str:
        """Return "websocket" or "poll", asking /v1/about when mode is "auto"."""
        if self.cfg.receive_mode != "auto":
            return self.cfg.receive_mode
        backoff = self._backoff()
        while not self.stop.is_set():
            try:
                api_mode = str(self.about().get("mode", "")).lower()
            except (requests.RequestException, ValueError) as e:
                LOGGER.warning(f"Could not reach {self.cfg.http_base}/v1/about to detect the "
                               f"API mode ({e}); retrying in {backoff.delay:.1f}s")
                if not backoff.wait():
                    break
                continue
            mode = "websocket" if api_mode.startswith("json-rpc") else "poll"
            LOGGER.info(f"signal-cli-rest-api reports MODE={api_mode or 'unknown'}; "
                        f"using {mode} receive")
            return mode
        return "poll"

    # -------------------------------------------------------------- receive
    def poll_once(self) -> list[Any]:
        """One HTTP receive call (MODE=normal/native). Returns raw items."""
        url = f"{self.cfg.http_base}{self.receive_path}"
        r = self.session.get(url, timeout=self.cfg.request_timeout_seconds)
        try:
            body = r.json()
        except ValueError:
            raise SignalApiError(f"Non-JSON receive response ({r.status_code}): {r.text[:200]}")
        if isinstance(body, dict) and "error" in body:
            hint = ""
            if "websocket" in str(body["error"]).lower() or r.status_code == 400:
                hint = (" (if the container runs MODE=json-rpc, set "
                        "signal.receive_mode to 'websocket' or 'auto')")
            raise SignalApiError(f"{str(body['error']).strip()}{hint}")
        if not r.ok:
            raise SignalApiError(f"HTTP {r.status_code}: {r.text[:200]}")
        return body if isinstance(body, list) else [body] if body else []

    def poll_forever(self) -> Iterator[list[Any]]:
        backoff = self._backoff()
        while not self.stop.is_set():
            try:
                items = self.poll_once()
                backoff.reset()
                if items:
                    yield items
            except (requests.RequestException, SignalApiError) as e:
                LOGGER.warning(f"Signal receive failed: {e}; retrying in {backoff.delay:.1f}s")
                if not backoff.wait():
                    return
                continue
            self.stop.wait(self.cfg.poll_interval_seconds)

    def stream_forever(self) -> Iterator[Any]:
        """Yield each message pushed over the receive WebSocket (MODE=json-rpc)."""
        url = f"{self.cfg.ws_base}{self.receive_path}"
        backoff = self._backoff()
        while not self.stop.is_set():
            try:
                ws = websocket.create_connection(url, timeout=self.cfg.request_timeout_seconds)
            except (OSError, websocket.WebSocketException) as e:
                hint = ""
                if isinstance(e, websocket.WebSocketBadStatusException):
                    hint = (" (the API may be in MODE=normal/native; set "
                            "signal.receive_mode to 'poll' or 'auto')")
                LOGGER.warning(f"Signal receive WebSocket {url} failed: {e}{hint}; "
                               f"retrying in {backoff.delay:.1f}s")
                if not backoff.wait():
                    return
                continue
            LOGGER.info(f"Streaming Signal messages from {url}")
            backoff.reset()
            ws.settimeout(self.WS_RECV_POLL_SECONDS)
            try:
                while not self.stop.is_set():
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not raw:
                        raise websocket.WebSocketConnectionClosedException("closed by server")
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        LOGGER.warning(f"Ignoring non-JSON frame from Signal API: {raw!r}")
            except (OSError, websocket.WebSocketException) as e:
                LOGGER.warning(f"Signal receive WebSocket dropped ({e}); reconnecting")
                if not backoff.wait():
                    return
            finally:
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass

    # ----------------------------------------------------------------- send
    def send(self, message: str, recipients: list[str] | tuple[str, ...]) -> bool:
        """POST /v2/send, retrying transient failures. True on success."""
        url = f"{self.cfg.http_base}/v2/send"
        body = {"message": message, "number": self.cfg.server_number,
                "recipients": list(recipients)}
        backoff = self._backoff()
        for attempt in range(1, self.SEND_ATTEMPTS + 1):
            try:
                r = self.session.post(url, json=body, timeout=self.cfg.request_timeout_seconds)
            except requests.RequestException as e:
                LOGGER.warning(f"Signal send attempt {attempt}/{self.SEND_ATTEMPTS} failed: {e}")
            else:
                if r.ok:
                    return True
                LOGGER.warning(f"Signal send returned HTTP {r.status_code}: {r.text[:300]}")
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    return False  # bad request / unknown recipient: retrying won't help
            if attempt < self.SEND_ATTEMPTS and not backoff.wait():
                return False
        return False
