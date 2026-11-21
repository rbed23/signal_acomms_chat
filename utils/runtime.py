"""Process helpers: config loading for scripts and clean Ctrl+C / SIGTERM shutdown."""
from __future__ import annotations

import argparse
import signal
import sys
import threading

from utils.config import Config, ConfigError, load_config
from utils.log import LOGGER, setup_logging


def install_stop_handlers(stop: threading.Event) -> None:
    def _handler(signum, _frame):
        if stop.is_set():
            LOGGER.warning("Second interrupt: exiting immediately.")
            sys.exit(130)
        LOGGER.info(f"Received {signal.Signals(signum).name}; shutting down...")
        stop.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def start(description: str, log_file: str, require_server_number: bool = True) -> Config:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("-c", "--config", help="path to a TOML config file "
                        "(default: $SACOMMS_CONFIG or ./config.toml in the repo root)")
    parser.add_argument("--log-level", help="override logging.level")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config, require_server_number=require_server_number)
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(2)
    setup_logging(log_file, (args.log_level or cfg.logging.level).upper(), cfg.logging.dir)
    LOGGER.info(f"Configuration loaded from: {cfg.source}")
    return cfg
