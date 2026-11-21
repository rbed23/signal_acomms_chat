"""
Central configuration for the Signal <-> ACOMMS chat bridge.

Settings are resolved in this order (later wins):

1. Built-in defaults (``DEFAULTS`` below)
2. A TOML file: ``$SACOMMS_CONFIG`` if set, otherwise ``config.toml`` in the
   repository root. Copy ``docs/config.example.toml`` to get started.
3. Environment variables named ``SACOMMS_<SECTION>_<KEY>``, for example
   ``SACOMMS_SIGNAL_SERVER_NUMBER=+15555550100`` or
   ``SACOMMS_ROSBRIDGE_HOST=10.0.0.5``. List values are comma-separated.
"""
from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.toml"
ENV_PREFIX = "SACOMMS"

DEFAULTS: dict[str, dict[str, Any]] = {
    "signal": {
        "api_host": "localhost",
        "api_port": 8080,
        "api_scheme": "http",
        # The bridge's own Signal number (E.164). Required; no default.
        "server_number": "",
        # "auto": ask /v1/about which MODE signal-cli-rest-api runs in
        # "websocket": MODE=json-rpc (or json-rpc-native), streaming receive
        # "poll": MODE=normal or native, HTTP GET receive every poll interval
        "receive_mode": "auto",
        "poll_interval_seconds": 5.0,
        "request_timeout_seconds": 30.0,
    },
    "rosbridge": {
        "host": "localhost",
        "port": 9090,
        "downlink_topic": "/chat_input",
        "uplink_topic": "/chat_output",
        "message_type": "std_msgs/String",
        # How chat metadata travels inside std_msgs/String "data":
        # "json":   data = '{"text": ..., "source": ..., "source_name": ...}'
        # "text":   data = 'NAME]text'
        # "legacy": data = text, plus non-standard source/source_name fields
        #           (original test implementation; not valid for real ROS)
        "payload_format": "json",
        "reconnect_initial_seconds": 1.0,
        "reconnect_max_seconds": 60.0,
    },
    "bridge": {
        # Signal numbers or UUIDs allowed to send into the acoustic network.
        "allowed_senders": [],
        # Set true to relay messages from anyone (not recommended).
        "allow_all_senders": False,
        # Max UTF-8 bytes of chat text sent into the acoustic network.
        # 0 disables the limit. Encoding overhead (JSON keys, prefix) is extra.
        "max_message_bytes": 200,
        # "truncate" or "reject" messages longer than max_message_bytes
        "oversize_policy": "truncate",
        # Tell an allowed sender via Signal when their message was cut/rejected
        "notify_sender_on_oversize": True,
        # Uplink recipients used when a /chat_output message names none
        "default_recipients": [],
    },
    "logging": {
        "level": "INFO",
        # Relative paths are resolved against the repository root
        "dir": "logs",
    },
}


class ConfigError(ValueError):
    """Raised when the configuration is missing or invalid."""


@dataclass(frozen=True)
class SignalConfig:
    api_host: str
    api_port: int
    api_scheme: str
    server_number: str
    receive_mode: str
    poll_interval_seconds: float
    request_timeout_seconds: float

    @property
    def http_base(self) -> str:
        return f"{self.api_scheme}://{self.api_host}:{self.api_port}"

    @property
    def ws_base(self) -> str:
        scheme = "wss" if self.api_scheme == "https" else "ws"
        return f"{scheme}://{self.api_host}:{self.api_port}"


@dataclass(frozen=True)
class RosbridgeConfig:
    host: str
    port: int
    downlink_topic: str
    uplink_topic: str
    message_type: str
    payload_format: str
    reconnect_initial_seconds: float
    reconnect_max_seconds: float

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}"


@dataclass(frozen=True)
class BridgeConfig:
    allowed_senders: tuple[str, ...]
    allow_all_senders: bool
    max_message_bytes: int
    oversize_policy: str
    notify_sender_on_oversize: bool
    default_recipients: tuple[str, ...]


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    dir: Path


@dataclass(frozen=True)
class Config:
    signal: SignalConfig
    rosbridge: RosbridgeConfig
    bridge: BridgeConfig
    logging: LoggingConfig
    source: str = field(default="defaults")


def _coerce(value: Any, like: Any, name: str) -> Any:
    """Convert ``value`` to the type of the default ``like``."""
    try:
        if isinstance(like, bool):
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in ("1", "true", "yes", "on"):
                return True
            if text in ("0", "false", "no", "off"):
                return False
            raise ValueError(value)
        if isinstance(like, int):
            return int(value)
        if isinstance(like, float):
            return float(value)
        if isinstance(like, list):
            if isinstance(value, str):
                return [v.strip() for v in value.split(",") if v.strip()]
            return [str(v) for v in value]
        return str(value)
    except (TypeError, ValueError):
        raise ConfigError(f"Invalid value for {name}: {value!r}") from None


def _merge(base: dict, override: dict, origin: str) -> None:
    for section, values in override.items():
        if section not in base:
            raise ConfigError(f"Unknown config section [{section}] in {origin}")
        if not isinstance(values, dict):
            raise ConfigError(f"[{section}] in {origin} must be a table")
        for key, value in values.items():
            if key not in base[section]:
                raise ConfigError(f"Unknown setting {section}.{key} in {origin}")
            base[section][key] = _coerce(value, DEFAULTS[section][key], f"{section}.{key}")


def load_config(path: str | os.PathLike | None = None,
                env: dict[str, str] | None = None,
                require_server_number: bool = True) -> Config:
    env = os.environ if env is None else env
    data = copy.deepcopy(DEFAULTS)
    sources = ["defaults"]

    if path is None:
        path = env.get(f"{ENV_PREFIX}_CONFIG") or DEFAULT_CONFIG_PATH
        explicit = f"{ENV_PREFIX}_CONFIG" in env
    else:
        explicit = True
    path = Path(path)
    if path.is_file():
        with path.open("rb") as fh:
            try:
                _merge(data, tomllib.load(fh), str(path))
            except tomllib.TOMLDecodeError as e:
                raise ConfigError(f"Could not parse {path}: {e}") from None
        sources.append(str(path))
    elif explicit:
        raise ConfigError(f"Config file not found: {path}")

    for section, values in DEFAULTS.items():
        for key, default in values.items():
            env_name = f"{ENV_PREFIX}_{section}_{key}".upper()
            if env_name in env:
                data[section][key] = _coerce(env[env_name], default, env_name)
                if "environment" not in sources:
                    sources.append("environment")

    cfg = _build(data, ", ".join(sources))
    _validate(cfg, require_server_number)
    return cfg


def _build(d: dict, source: str) -> Config:
    log_dir = Path(d["logging"]["dir"])
    if not log_dir.is_absolute():
        log_dir = REPO_ROOT / log_dir
    b = d["bridge"]
    return Config(
        signal=SignalConfig(**d["signal"]),
        rosbridge=RosbridgeConfig(**d["rosbridge"]),
        bridge=BridgeConfig(
            allowed_senders=tuple(b["allowed_senders"]),
            allow_all_senders=b["allow_all_senders"],
            max_message_bytes=b["max_message_bytes"],
            oversize_policy=b["oversize_policy"],
            notify_sender_on_oversize=b["notify_sender_on_oversize"],
            default_recipients=tuple(b["default_recipients"]),
        ),
        logging=LoggingConfig(level=d["logging"]["level"].upper(), dir=log_dir),
        source=source,
    )


def _validate(cfg: Config, require_server_number: bool) -> None:
    s, r, b = cfg.signal, cfg.rosbridge, cfg.bridge
    if require_server_number and not s.server_number:
        raise ConfigError(
            "signal.server_number is not set. Copy docs/config.example.toml to "
            "config.toml and fill it in, or set SACOMMS_SIGNAL_SERVER_NUMBER.")
    if s.server_number and not s.server_number.startswith("+"):
        raise ConfigError("signal.server_number must be in E.164 format, e.g. +15555550100")
    if s.receive_mode not in ("auto", "websocket", "poll"):
        raise ConfigError("signal.receive_mode must be 'auto', 'websocket' or 'poll'")
    if s.api_scheme not in ("http", "https"):
        raise ConfigError("signal.api_scheme must be 'http' or 'https'")
    if s.poll_interval_seconds <= 0:
        raise ConfigError("signal.poll_interval_seconds must be > 0")
    if r.payload_format not in ("json", "text", "legacy"):
        raise ConfigError("rosbridge.payload_format must be 'json', 'text' or 'legacy'")
    if r.reconnect_initial_seconds <= 0 or r.reconnect_max_seconds < r.reconnect_initial_seconds:
        raise ConfigError("rosbridge reconnect settings must satisfy 0 < initial <= max")
    if b.oversize_policy not in ("truncate", "reject"):
        raise ConfigError("bridge.oversize_policy must be 'truncate' or 'reject'")
    if b.max_message_bytes < 0:
        raise ConfigError("bridge.max_message_bytes must be >= 0")
    if cfg.logging.level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ConfigError("logging.level must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
