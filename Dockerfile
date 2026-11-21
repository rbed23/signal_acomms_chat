# Image for the bridge processes (listener, uplink, fake rosbridge).
# Settings come from SACOMMS_* environment variables (see docs/env.example)
# or a config file mounted at /app/config.toml.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY utils/ utils/
COPY tools/ tools/
COPY signal_cli_app_listener_daemon.py signal_cli_app_rosbridge_server.py ws_test_server.py ./

RUN useradd --system --uid 10001 bridge && mkdir -p logs && chown bridge logs
USER bridge

CMD ["python", "signal_cli_app_listener_daemon.py"]
