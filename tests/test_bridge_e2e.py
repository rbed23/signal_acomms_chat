"""
End-to-end tests: fake rosbridge (ws_test_server.py, as a subprocess) +
fake Signal API (in-process) + the real downlink/uplink bridge classes.
"""
import json
import os
import subprocess
import sys
import threading
import time

import pytest
import websocket

from fake_signal_api import FakeSignalApi, envelope, free_port
from utils.config import REPO_ROOT, load_config
from utils.log import setup_logging
from signal_cli_app_listener_daemon import DownlinkBridge
from signal_cli_app_rosbridge_server import UplinkBridge

ALLOWED = "+15550001111"


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class FakeRosbridge:
    def __init__(self, cfg_path, port):
        self.cfg_path, self.port, self.proc = cfg_path, port, None

    def start(self):
        env = dict(os.environ, SACOMMS_CONFIG=str(self.cfg_path))
        self.proc = subprocess.Popen([sys.executable, str(REPO_ROOT / "ws_test_server.py")],
                                     cwd=REPO_ROOT, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert wait_for(self._up), "fake rosbridge did not start"
        return self

    def _up(self):
        try:
            websocket.create_connection(f"ws://127.0.0.1:{self.port}", timeout=1).close()
            return True
        except Exception:
            return False

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(10)


class Acoustic:
    """Plays the acoustic chat node: subscribes to downlink, publishes uplink."""

    def __init__(self, port):
        self.ws = websocket.create_connection(f"ws://127.0.0.1:{port}", timeout=5)
        self.ws.send(json.dumps({"op": "subscribe", "topic": "/chat_input", "type": "std_msgs/String"}))
        time.sleep(0.2)

    def recv(self, timeout=10):
        self.ws.settimeout(timeout)
        return json.loads(self.ws.recv())

    def publish_uplink(self, msg):
        self.ws.send(json.dumps({"op": "publish", "topic": "/chat_output", "msg": msg}))

    def close(self):
        self.ws.close()


@pytest.fixture
def env(tmp_path, request):
    mode = getattr(request, "param", "json-rpc")
    signal_api = FakeSignalApi(mode).start()
    ros_port = free_port()
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f"""
[signal]
api_host = "127.0.0.1"
api_port = {signal_api.port}
server_number = "+15550000000"
receive_mode = "auto"
poll_interval_seconds = 0.2
request_timeout_seconds = 5
[rosbridge]
host = "127.0.0.1"
port = {ros_port}
reconnect_initial_seconds = 0.2
reconnect_max_seconds = 1
[bridge]
allowed_senders = ["{ALLOWED}"]
max_message_bytes = 20
default_recipients = ["+15559999999"]
[logging]
level = "DEBUG"
dir = "{tmp_path / 'logs'}"
""")
    cfg = load_config(cfg_path, env={})
    setup_logging("test.log", "DEBUG", cfg.logging.dir)
    ros = FakeRosbridge(cfg_path, ros_port).start()
    stop = threading.Event()
    threads = []

    def run(bridge_cls):
        b = bridge_cls(cfg, stop)
        t = threading.Thread(target=b.run, daemon=True)
        t.start()
        threads.append(t)
        return b

    yield type("Env", (), dict(cfg=cfg, signal=signal_api, ros=ros, stop=stop, run=run, port=ros_port))
    stop.set()
    for t in threads:
        t.join(10)
        assert not t.is_alive(), "bridge thread did not stop on the stop event"
    ros.stop()
    signal_api.stop()


@pytest.mark.parametrize("env", ["json-rpc", "native"], indirect=True)
def test_downlink(env):
    acoustic = Acoustic(env.port)
    env.run(DownlinkBridge)
    time.sleep(1.0)  # let the listener connect and pick its receive mode
    env.signal.deliver(envelope("ignored", number="+15550002222", uuid="u2", name="mallory"),
                       envelope(kind="typing"),
                       envelope("hello acoustic"),
                       envelope("this message is far too long for the modem"))

    first = json.loads(acoustic.recv()["msg"]["data"])
    assert first["text"] == "hello acoustic" and first["source"] == ALLOWED
    assert first["source_name"] == "alice"
    second = json.loads(acoustic.recv()["msg"]["data"])
    assert second["text"] == "this message is far " and len(second["text"].encode()) == 20
    # oversize notice goes back to the sender; nothing from the non-allowed sender
    assert wait_for(lambda: len(env.signal.sent) == 1)
    assert env.signal.sent[0]["recipients"] == [ALLOWED]
    assert "cut to 20 bytes" in env.signal.sent[0]["message"]
    acoustic.close()


def test_uplink_and_reconnect(env):
    env.run(UplinkBridge)
    time.sleep(0.8)
    acoustic = Acoustic(env.port)
    acoustic.publish_uplink({"data": json.dumps({"text": "hi from auv", "recipient": "+15551234567",
                                                 "source_name": "auv1"})})
    acoustic.publish_uplink({"data": "plain text"})  # -> default_recipients
    assert wait_for(lambda: len(env.signal.sent) == 2)
    assert env.signal.sent[0] == {"message": "auv1]hi from auv", "number": "+15550000000",
                                  "recipients": ["+15551234567"]}
    assert env.signal.sent[1]["recipients"] == ["+15559999999"]
    acoustic.close()

    # rosbridge restarts: the uplink must reconnect and re-subscribe by itself
    env.ros.stop()
    time.sleep(0.5)
    env.ros.start()
    time.sleep(2.0)
    acoustic = Acoustic(env.port)
    acoustic.publish_uplink({"data": json.dumps({"text": "after restart", "recipients": ["+15551234567"]})})
    assert wait_for(lambda: len(env.signal.sent) == 3), "uplink did not recover after rosbridge restart"
    assert env.signal.sent[2]["message"] == "after restart"
    acoustic.close()


def test_listener_waits_for_rosbridge_then_delivers(env):
    """Messages are not fetched from Signal (and lost) while rosbridge is down."""
    env.ros.stop()
    env.run(DownlinkBridge)
    env.signal.deliver(envelope("queued while down"))
    time.sleep(1.0)
    assert env.signal.pending, "listener fetched from Signal before rosbridge was reachable"
    env.ros.start()
    log = env.cfg.logging.dir / "signal_cli_app_ws_server.log"
    assert wait_for(lambda: log.exists() and "QUEUED WHILE DOWN" not in "" and
                    "queued while down" in log.read_text()), "message never reached rosbridge"
    assert not env.signal.pending
