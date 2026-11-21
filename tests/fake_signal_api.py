"""
In-process stand-in for bbernhard/signal-cli-rest-api, used by the tests.

Mimics the parts the bridge uses, including the MODE difference:
in "json-rpc" mode /v1/receive/<number> only accepts WebSocket upgrades;
in "normal"/"native" mode it is a plain HTTP GET returning a JSON list.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

from aiohttp import WSMsgType, web


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def envelope(text: str | None = None, number: str = "+15550001111", name: str | None = "alice",
             uuid: str = "uuid-alice", kind: str = "data") -> dict:
    now = int(time.time() * 1000)
    env = {"source": number, "sourceNumber": number, "sourceUuid": uuid, "sourceName": name,
           "sourceDevice": 1, "timestamp": now, "serverReceivedTimestamp": now,
           "serverDeliveredTimestamp": now}
    if kind == "data":
        env["dataMessage"] = {"timestamp": now, "message": text, "expiresInSeconds": 0}
    elif kind == "typing":
        env["typingMessage"] = {"action": "STARTED", "timestamp": now}
    elif kind == "receipt":
        env["receiptMessage"] = {"when": now, "isDelivery": True, "timestamps": [now]}
    return {"envelope": env, "account": "+15550000000"}


class FakeSignalApi:
    def __init__(self, mode: str = "json-rpc"):
        self.mode = mode
        self.port = free_port()
        self.sent: list[dict] = []
        self.pending: list[dict] = []
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    # ---------------------------------------------------------------- server
    async def _about(self, request):
        return web.json_response({"versions": ["v1", "v2"], "mode": self.mode, "version": "fake"})

    async def _receive(self, request):
        wants_ws = request.headers.get("Upgrade", "").lower() == "websocket"
        if self.mode.startswith("json-rpc"):
            if not wants_ws:
                return web.json_response({"error": "Use a websocket connection"}, status=400)
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            self._ws_clients.add(ws)
            await self._flush()
            async for msg in ws:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
            self._ws_clients.discard(ws)
            return ws
        if wants_ws:
            return web.Response(status=400, text="websocket not supported in this mode")
        items, self.pending = self.pending, []
        return web.json_response(items)

    async def _send(self, request):
        body = await request.json()
        self.sent.append(body)
        if not body.get("recipients") or not all(body["recipients"]):
            return web.json_response({"error": "invalid recipient"}, status=400)
        return web.json_response({"timestamp": str(int(time.time() * 1000))}, status=201)

    async def _flush(self):
        if not self._ws_clients:
            return
        items, self.pending = self.pending, []
        for item in items:
            for ws in list(self._ws_clients):
                await ws.send_str(json.dumps(item))

    def _run(self):
        self._loop = asyncio.new_event_loop()
        app = web.Application()
        app.add_routes([web.get("/v1/about", self._about),
                        web.get("/v1/receive/{number}", self._receive),
                        web.post("/v2/send", self._send)])
        self._runner = web.AppRunner(app)
        self._loop.run_until_complete(self._runner.setup())
        self._loop.run_until_complete(web.TCPSite(self._runner, "127.0.0.1", self.port).start())
        self._ready.set()
        self._loop.run_forever()

    # ------------------------------------------------------------------- api
    def start(self) -> "FakeSignalApi":
        self._thread.start()
        self._ready.wait(5)
        return self

    def stop(self) -> None:
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._runner.cleanup(), self._loop).result(5)
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(5)

    def deliver(self, *items: dict) -> None:
        """Queue incoming Signal messages (pushed immediately in websocket mode)."""
        def _add():
            self.pending.extend(items)
            return self._flush()
        asyncio.run_coroutine_threadsafe(_add(), self._loop).result(5)
