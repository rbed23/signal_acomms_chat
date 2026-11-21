import pytest

from utils.config import ConfigError, load_config

MIN = '[signal]\nserver_number = "+15555550100"\n'


def write(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text(text)
    return p


def test_defaults_and_file(tmp_path):
    cfg = load_config(write(tmp_path, MIN), env={})
    assert cfg.signal.server_number == "+15555550100"
    assert cfg.signal.http_base == "http://localhost:8080"
    assert cfg.signal.ws_base == "ws://localhost:8080"
    assert cfg.rosbridge.url == "ws://localhost:9090"
    assert cfg.bridge.allow_all_senders is False
    assert cfg.logging.dir.is_absolute()


def test_env_overrides_file(tmp_path):
    env = {"SACOMMS_ROSBRIDGE_PORT": "9999",
           "SACOMMS_BRIDGE_ALLOWED_SENDERS": "+1555, uuid-x",
           "SACOMMS_BRIDGE_ALLOW_ALL_SENDERS": "yes"}
    cfg = load_config(write(tmp_path, MIN), env=env)
    assert cfg.rosbridge.port == 9999
    assert cfg.bridge.allowed_senders == ("+1555", "uuid-x")
    assert cfg.bridge.allow_all_senders is True
    assert "environment" in cfg.source


def test_config_path_from_env(tmp_path):
    p = write(tmp_path, MIN)
    assert load_config(env={"SACOMMS_CONFIG": str(p)}).signal.server_number == "+15555550100"
    with pytest.raises(ConfigError, match="not found"):
        load_config(env={"SACOMMS_CONFIG": str(tmp_path / "missing.toml")})


@pytest.mark.parametrize("text, match", [
    ("[signal]\n", "server_number is not set"),
    ('[signal]\nserver_number = "5555"\n', "E.164"),
    (MIN + 'receive_mode = "push"\n', "receive_mode"),
    (MIN + '[rosbridge]\npayload_format = "xml"\n', "payload_format"),
    (MIN + '[bridge]\noversize_policy = "drop"\n', "oversize_policy"),
    (MIN + '[bridge]\nnope = 1\n', "Unknown setting"),
    (MIN + '[nope]\n', "Unknown config section"),
    (MIN + '[rosbridge]\nport = "abc"\n', "Invalid value"),
])
def test_validation(tmp_path, text, match):
    with pytest.raises(ConfigError, match=match):
        load_config(write(tmp_path, text), env={})


def test_example_config_is_valid():
    from utils.config import REPO_ROOT
    cfg = load_config(REPO_ROOT / "docs" / "config.example.toml", env={})
    assert cfg.signal.receive_mode == "auto"
