"""Shared logging setup: stdout + one file per process, UTC timestamps."""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

LOGGER = logging.getLogger("signal_acomms")
FORMATTER = logging.Formatter("%(asctime)s - %(funcName)s - %(levelname)s - %(message)s")
FORMATTER.converter = time.gmtime


def setup_logging(log_file: str, level: str = "INFO", log_dir: Path | None = None) -> logging.Logger:
    """Configure ``LOGGER`` once per process. Safe to call more than once."""
    LOGGER.setLevel(level)
    LOGGER.propagate = False
    for h in list(LOGGER.handlers):
        LOGGER.removeHandler(h)
        h.close()

    sh = logging.StreamHandler(stream=sys.stdout)
    sh.setFormatter(FORMATTER)
    LOGGER.addHandler(sh)

    if log_dir is not None and log_file:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / log_file)
        fh.setFormatter(FORMATTER)
        LOGGER.addHandler(fh)
    return LOGGER
