# Signal ↔ ACOMMS Chat Bridge

A Python bridge that lets people chat with an underwater **acoustic communications (ACOMMS) chat network** from the **Signal** app on their phones.

- **Downlink:** Signal users message a dedicated "server" Signal number. Messages from approved senders are published **down** into the acoustic network on a ROS topic.
- **Uplink:** chat messages coming **up** from the acoustic network on another ROS topic are sent to Signal users.

The bridge is built on two existing projects:

- **[signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api)**, a Docker-packaged REST/WebSocket wrapper around `signal-cli` that sends and receives messages for a registered number.
- **[rosbridge](http://wiki.ros.org/rosbridge_suite)**, which exposes ROS topics over a WebSocket using the rosbridge v2 JSON protocol.

> **Status: test implementation.** The bridge is covered by automated tests against a fake rosbridge (`ws_test_server.py`) and a fake Signal API. It has **not** yet run against a live ROS system or an acoustic modem. The JSON payload format below is a proposal that the ACOMMS chat node needs to adopt (see [Payload formats](#payload-formats)).

---

## Contents

1. [How it works](#how-it-works)
2. [Repository layout](#repository-layout)
3. [Quick start (local test, no ROS)](#quick-start-local-test-no-ros)
4. [Setting up Signal](#setting-up-signal)
5. [Configuration](#configuration)
6. [Payload formats](#payload-formats)
7. [Running in production](#running-in-production)
8. [Logging](#logging)
9. [Tests](#tests)
10. [Security and sensitive files](#security-and-sensitive-files)
11. [Known limitations](#known-limitations)
12. [Upgrading from the original test implementation](#upgrading-from-the-original-test-implementation)

---

## How it works

```
                       DOWNLINK  (Signal → acoustic network)
 ┌────────────┐  WebSocket stream (json-rpc)  ┌──────────────────────────────┐  publish /chat_input  ┌──────────────────────┐
 │ Signal app │ ─► signal-cli-rest-api ─────► │ signal_cli_app_listener_     │ ────────────────────► │ rosbridge  (ws :9090)│
 │  (phones)  │    (:8080)  or HTTP poll      │ daemon.py                    │                       │   └─ ACOMMS chat node│
 │            │                               └──────────────────────────────┘                       │                      │
 │            │ ◄── POST /v2/send ──────────  ┌──────────────────────────────┐ ◄─ subscribe ──────── │                      │
 └────────────┘                               │ signal_cli_app_rosbridge_    │    /chat_output       └──────────────────────┘
                       UPLINK                 │ server.py                    │
                       (acoustic → Signal)    └──────────────────────────────┘
```

| Process | Direction | What it does |
|---|---|---|
| `signal_cli_app_listener_daemon.py` | **Downlink** | Connects to rosbridge and advertises the downlink topic, then receives Signal messages for the server number. It streams them over WebSocket when signal-cli-rest-api runs in `MODE=json-rpc`, and polls over HTTP in `normal`/`native` mode; the mode is detected automatically. Each text message from an **allowed sender** is checked against the **length limit** and published to `/chat_input`. Typing indicators, receipts, reactions and attachments are logged and not forwarded. |
| `signal_cli_app_rosbridge_server.py` | **Uplink** | Subscribes to `/chat_output`. It decodes each message's text, sender label and recipients and sends it with `POST /v2/send`. |

Both processes:

- keep **one persistent WebSocket** to rosbridge;
- **reconnect automatically** with exponential backoff, re-sending their `advertise`/`subscribe` after a rosbridge restart;
- retry Signal API calls the same way;
- shut down cleanly on **Ctrl+C or SIGTERM**, usually within about a second.

The listener only fetches from Signal while it is connected to rosbridge. Messages are therefore not lost while rosbridge is down: they wait in Signal until the bridge can deliver them.

---

## Repository layout

```
signal_acomms_chat/
├── signal_cli_app_listener_daemon.py   # Downlink: Signal → rosbridge
├── signal_cli_app_rosbridge_server.py  # Uplink:   rosbridge → Signal
├── ws_test_server.py                   # Fake rosbridge (topic-aware) for local testing
├── signal_cli_app_tool.py              # DEPRECATED original prototype, kept for reference
├── config.toml                         # YOUR local settings (git-ignored; template in docs/)
├── docker-compose.yml                  # signal-api + bridge services (+ fake rosbridge profile)
├── Dockerfile                          # Image for the bridge processes
├── requirements.txt                    # Runtime dependencies
├── requirements-dev.txt                # + pytest, aiohttp for the tests
├── utils/
│   ├── config.py                       # Loads defaults → config.toml → SACOMMS_* env vars
│   ├── messages.py                     # Envelope parsing, allow-list, length policy, payloads
│   ├── rosbridge.py                    # rosbridge client with reconnect/backoff
│   ├── signal_api.py                   # signal-cli-rest-api client (stream / poll / send)
│   ├── runtime.py                      # CLI args, config errors, Ctrl+C/SIGTERM handling
│   └── log.py                          # stdout + per-process log file, UTC timestamps
├── tools/
│   └── acomms_sim.py                   # Pretend to be the acoustic network (listen / send)
├── tests/                              # Unit + end-to-end tests (fake Signal API included)
├── docs/
│   ├── config.example.toml             # Template for config.toml
│   ├── env.example                     # Template for .env (docker compose)
│   ├── signal-daemon-start.example.txt # Signal setup cheat sheet (template for your notes)
│   └── systemd/                        # Example systemd units for both processes
└── logs/                               # Log output (git-ignored)
```

---

## Quick start (local test, no ROS)

**Requirements:** Python **3.11+** (uses the built-in `tomllib`) and a running signal-cli-rest-api with a registered number (see [Setting up Signal](#setting-up-signal)).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp docs/config.example.toml config.toml     # skip if config.toml already exists
#   edit config.toml: signal.server_number and bridge.allowed_senders (your phone)
```

Open four terminals in the repo with the virtualenv active. The order doesn't matter, because every process waits and reconnects.

```bash
python ws_test_server.py                     # 1: fake rosbridge on :9090
python signal_cli_app_rosbridge_server.py    # 2: uplink
python signal_cli_app_listener_daemon.py     # 3: downlink
python tools/acomms_sim.py listen            # 4: shows what reaches the "acoustic network"
```

**Downlink test:** send a Signal message from your phone to the server number. Terminal 4 prints it at once in json-rpc mode, or within `poll_interval_seconds` in poll mode:

```
/chat_input: {'data': '{"text":"hello","source":"+1…","source_uuid":"…","source_name":"you","timestamp":…}'}
```

**Uplink test:** act as a vehicle and send to your phone:

```bash
python tools/acomms_sim.py send --to +1YOURPHONE --name auv1 "hello from the vehicle"
```

Your phone receives **`auv1]hello from the vehicle`**.

Stop each process with **Ctrl+C**. A second Ctrl+C exits immediately.

All scripts accept `-c path/to/config.toml`, and the bridge scripts also accept `--log-level DEBUG`. They can be started from any directory.

---

## Setting up Signal

A cheat sheet with every command is in [`docs/signal-daemon-start.example.txt`](docs/signal-daemon-start.example.txt).

### 1. Start signal-cli-rest-api

The simplest way is with Compose. Its account data goes to `./signal-cli-config/`, which is git-ignored.

```bash
docker compose up -d signal-api
```

Or with plain Docker:

```bash
docker run -d --name signal-api --restart=always -p 127.0.0.1:8080:8080 \
  -v $PWD/signal-cli-config:/home/.local/share/signal-cli \
  -e MODE=json-rpc bbernhard/signal-cli-rest-api
```

#### Which `MODE`?

The bridge works with every mode, and `receive_mode = "auto"` picks the right method by checking `GET /v1/about`.

| `MODE` | How the bridge receives | Notes |
|---|---|---|
| `json-rpc` / `json-rpc-native` (**recommended**) | WebSocket stream on `/v1/receive/<number>` | Messages arrive instantly. Uses more memory because signal-cli keeps running. |
| `normal` / `native` | HTTP `GET /v1/receive/<number>` every `poll_interval_seconds` | Slower: signal-cli starts on every request. |

`/v1/receive/<number>` behaves differently depending on the mode. In json-rpc mode it **only accepts a WebSocket connection**, and a plain HTTP GET fails. In normal/native mode it is a plain HTTP request that returns and clears pending messages. The original test code only did the HTTP GET, so it could not receive in json-rpc mode. The bridge now supports both. If you force `receive_mode` to the wrong value, the log tells you to switch.

### 2. Register or link the number (once)

- **Link** as a secondary device of an existing account: open `http://localhost:8080/v1/qrcodelink?device_name=acomms-bridge` and scan it under *Signal → Settings → Linked devices*.
- **Register** a new number with `/v1/register/{number}` and `/v1/register/{number}/verify/{token}` (see the [API docs](https://bbernhard.github.io/signal-cli-rest-api/)).

### 3. Set a profile name

Without a profile name, recipients see **"Unknown User"**:

```bash
curl -X PUT -H "Content-Type: application/json" \
  'http://localhost:8080/v1/profiles/+1SERVERNUMBER' \
  -d '{"name": "acomms-bridge", "about": "Interface between Signal and AcommsChat"}'
```

A `204` response means it worked.

---

## Configuration

Settings are resolved in this order, with later sources winning:

1. built-in defaults (`utils/config.py`)
2. a TOML file: `$SACOMMS_CONFIG` if set, otherwise **`config.toml`** in the repo root
3. environment variables **`SACOMMS_<SECTION>_<KEY>`**, for example `SACOMMS_SIGNAL_SERVER_NUMBER=+15555550100` or `SACOMMS_BRIDGE_ALLOWED_SENDERS=+1555…,+1555…` (lists are comma-separated)

Unknown keys, bad values and a missing `server_number` stop the program at startup with a clear message (exit code 2). The annotated template is [`docs/config.example.toml`](docs/config.example.toml).

### `[signal]`

| Key | Default | Meaning |
|---|---|---|
| `api_host` / `api_port` / `api_scheme` | `localhost` / `8080` / `http` | Where signal-cli-rest-api is reachable |
| `server_number` | *(required)* | The bridge's own Signal number, in E.164 format |
| `receive_mode` | `auto` | `auto`, `websocket` (json-rpc) or `poll` (normal/native) |
| `poll_interval_seconds` | `5` | Poll period (poll mode only) |
| `request_timeout_seconds` | `30` | HTTP/WebSocket timeout for Signal API calls |

### `[rosbridge]`

| Key | Default | Meaning |
|---|---|---|
| `host` / `port` | `localhost` / `9090` | rosbridge WebSocket server |
| `downlink_topic` | `/chat_input` | The bridge **publishes** Signal messages here |
| `uplink_topic` | `/chat_output` | The bridge **subscribes** here for messages to Signal |
| `message_type` | `std_msgs/String` | ROS type used in `advertise`/`subscribe` |
| `payload_format` | `json` | `json`, `text` or `legacy` (see [Payload formats](#payload-formats)) |
| `reconnect_initial_seconds` / `reconnect_max_seconds` | `1` / `60` | Backoff for rosbridge **and** Signal API retries |

### `[bridge]`

| Key | Default | Meaning |
|---|---|---|
| `allowed_senders` | `[]` | Signal numbers or UUIDs allowed into the acoustic network. **Empty means nobody**, and a warning is logged at startup. |
| `allow_all_senders` | `false` | Accept anyone. Not recommended. |
| `max_message_bytes` | `200` | Limit on the UTF-8 chat text. `0` disables it. Encoding overhead is extra. |
| `oversize_policy` | `truncate` | `truncate` (cut at a character boundary) or `reject` |
| `notify_sender_on_oversize` | `true` | Reply on Signal when a message is cut or rejected |
| `default_recipients` | `[]` | Uplink recipients for messages that name none |

### `[logging]`

| Key | Default | Meaning |
|---|---|---|
| `level` | `INFO` | `DEBUG` also logs raw frames and full message content |
| `dir` | `logs` | Log directory, relative to the repo root |

---

## Payload formats

`std_msgs/String` has only one field, `data`, so real rosbridge drops any other field. Sender and recipient information therefore has to be carried **inside** `data`. Pick a format with `rosbridge.payload_format`. The uplink **accepts all three** formats whatever the setting.

### `json` (default)

Downlink, published on `/chat_input`:

```json
{"op": "publish", "topic": "/chat_input",
 "msg": {"data": "{\"text\":\"hello\",\"source\":\"+15550001111\",\"source_uuid\":\"…\",\"source_name\":\"alice\",\"timestamp\":1795244264.653}"}}
```

Uplink, expected on `/chat_output`. The `data` value is a JSON string:

```json
{"text": "hello from the vehicle", "recipients": ["+15550001111"], "source_name": "auv1"}
```

| Uplink field | Required | Notes |
|---|---|---|
| `text` | yes | Message text |
| `recipients` (list) or `recipient` (string) | yes, unless `default_recipients` is set | Signal numbers. Use the downlink's `source` to reply to a sender. |
| `source_name` or `source` | no | Shown as a prefix: `auv1]hello…` |

### `text`

Downlink `data` is `alice]hello`. Uplink `data` is plain text, delivered to `bridge.default_recipients`. Use this when the acoustic node can only handle plain strings.

### `legacy`

This is the original test implementation's format: `data` is the bare text, with non-standard `source`/`source_name` (downlink) and `recipient-number` (uplink) fields alongside it. It works with `ws_test_server.py` **but not with a real rosbridge**, which removes the extra fields. It is kept only for compatibility.

---

## Running in production

### Docker Compose

```bash
cp docs/env.example .env          # set server number, allowed senders, rosbridge host
docker compose up -d signal-api   # then register/link the number once (see above)
docker compose up -d              # signal-api + listener + uplink
```

The bridge containers reach rosbridge on the host through `host.docker.internal`. Change `SACOMMS_ROSBRIDGE_HOST` in `.env` if rosbridge runs elsewhere. For a fully local test stack:

```bash
SACOMMS_ROSBRIDGE_HOST=fake-rosbridge docker compose --profile test up -d
```

The Signal API port is bound to `127.0.0.1` only. Don't expose it: anyone who can reach it can send messages as your number.

### systemd (without Docker for the bridge)

Example units are in [`docs/systemd/`](docs/systemd/). Adjust `User`, `WorkingDirectory` and the venv path, then:

```bash
sudo cp docs/systemd/signal-acomms-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now signal-acomms-listener signal-acomms-uplink
```

---

## Logging

Every process logs to stdout and to a file in `logging.dir`. Timestamps are in UTC.

| Process | Log file |
|---|---|
| listener (downlink) | `signal_cli_app_listener.log` |
| uplink | `signal_cli_app_rosbridge.log` |
| fake rosbridge | `signal_cli_app_ws_server.log` |
| legacy prototype | `signal_cli_app.log` |

Key lines to watch:

- `DOWNLINK (…) 'NAME]text' … -> /chat_input`
- `UPLINK 'label]text' -> Signal [...]`
- `DROPPED message from non-allowed sender`
- `TRUNCATED` / `REJECTED`
- `reconnecting`

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

There are 22 tests:

- **Unit tests** for config loading and validation, envelope parsing, the allow-list, the UTF-8 length policy, and payload encoding and decoding.
- **End-to-end tests** that run the real bridge classes against `ws_test_server.py` and an in-process fake signal-cli-rest-api that behaves like **both** json-rpc and native modes. They cover:
  - downlink in each mode, including allow-list drops, truncation and the sender notice;
  - uplink with JSON and plain payloads;
  - recovery after a rosbridge restart;
  - no message loss while rosbridge is down;
  - prompt shutdown.

---

## Security and sensitive files

These files hold phone numbers, Signal account keys or message content. They are **git-ignored**, and each has a committed template in `docs/`:

| Local file (ignored) | Template |
|---|---|
| `config.toml` | `docs/config.example.toml` |
| `.env` | `docs/env.example` |
| `signal-daemon-start.txt` | `docs/signal-daemon-start.example.txt` |
| `signal-cli-config/` (Signal account keys) | none; created by signal-cli-rest-api |
| `logs/*` | none |

> If `config.toml` or `signal-daemon-start.txt` was committed before this change, `.gitignore` alone won't stop git tracking it. Run `git rm --cached signal-daemon-start.txt config.toml`. Phone numbers already in the git history stay there unless the history is rewritten.

Only senders in `allowed_senders` can reach the acoustic network. Senders are matched on **number or UUID, never on the profile name**, because anyone can set any profile name.

---

## Known limitations

- **ACOMMS integration is unverified.** The real chat node has to read and write the chosen payload format (`json` recommended). This needs testing against a live `rosbridge_server` and modem.
- **Text only.** Attachments, reactions, edits and deletions aren't relayed, by design, given acoustic bandwidth. Group messages from allowed senders are relayed, but uplink replies go to individual numbers, not the group.
- **Encoding overhead.** `max_message_bytes` limits the chat text only. JSON keys (about 100 bytes) or the `name]` prefix come on top. Use `payload_format = "text"` if every byte counts.
- **Half-open links.** If the network to rosbridge silently dies, a publish can appear to succeed before the bridge notices and reconnects, so that message is lost.
- **Untested Docker image.** The Dockerfile and Compose file are checked for syntax (`docker compose config`), but the image hasn't been built in CI yet.
- **Deprecated prototype.** `signal_cli_app_tool.py` is the original prototype. It now reads `config.toml` but otherwise has none of the fixes above. Don't use it.

---

## Upgrading from the original test implementation

### What changed

| Before | Now |
|---|---|
| Settings hard-coded in `utils/configs_signal_cli.py`, `utils/configs_rosbridge_server.py` | One `config.toml` (template in `docs/config.example.toml`) plus `SACOMMS_*` environment overrides |
| `utils/configs_logger.py` | `utils/log.py` (log directory set in `[logging]`) |
| Receive only via HTTP polling, so `MODE=json-rpc` could not receive | `receive_mode = "auto"`: WebSocket stream for json-rpc, polling for normal/native |
| Metadata in extra fields next to `data` | Carried inside `data` (`payload_format = "json"` by default). Set `payload_format = "legacy"` to keep the old shape with `ws_test_server.py`. |
| Anyone could message the acoustic network | Only `bridge.allowed_senders` (nobody by default) |
| No reconnects; a new WebSocket per downlink message | One persistent connection per process, with automatic reconnects |
| Scripts had to be run from the repo root | Run from any directory. Use `-c` to choose a config file. |
| `signal-daemon-start.txt` committed with real numbers | Git-ignored. The template is `docs/signal-daemon-start.example.txt`. |

The script names and how you start them are unchanged. Anything outside this repo that imported the removed `utils/configs_*.py` modules has to switch to `utils.config.load_config()`.

### Replacing an existing clone

Replace the contents on a branch so history is kept and removed files show up as deletions:

```bash
cd path/to/signal_acomms_chat
git checkout main && git pull
git checkout -b bridge-v2
unzip ~/Downloads/signal_acomms_chat-main.zip -d /tmp/sac_new
git rm -r -q .                                # removes tracked files only (.venv, .idea stay)
cp -a /tmp/sac_new/signal_acomms_chat-main/. .
git add -A
git status                                    # config.toml must NOT be listed
git check-ignore -v config.toml signal-daemon-start.txt   # both should match .gitignore
git commit -m "Rework bridge: json-rpc receive, reconnects, allow-list, central config, tests"
git push -u origin bridge-v2                  # then open a PR and merge
```

After the merge, everyone else runs `git pull` and then `cp docs/config.example.toml config.toml`, and fills in their own numbers.

Phone numbers committed earlier stay in the git history. To remove them, rewrite the history, for example with `git filter-repo --path signal-daemon-start.txt --invert-paths`. Everyone then has to re-clone.
