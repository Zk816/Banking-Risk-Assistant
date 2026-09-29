"""Logging for the whole project. Call `setup_logging()` once, at the top of a script.

Two destinations, on purpose:
  - stderr  — what you watch while a command runs
  - file    — what you read afterwards, size-capped so it can never fill the disk

Rotation is not optional. A loop that fails once per iteration writes a log line
per iteration, and an unbounded file is how you lose a disk overnight.

Note this is diagnostics, not program output. Scripts still `print()` the report
a human reads; the logger records what happened for later.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler

from kzbank.config import settings

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Set once, so importing a module twice does not attach two handlers.
_configured = False


def setup_logging(*, verbose: bool = False) -> None:
    """Attach a rotating file handler and a stderr handler to the root logger.

    Safe to call more than once; later calls do nothing.

    Raises:
        OSError: if LOG_DIR cannot be created.
    """
    global _configured
    if _configured:
        return

    settings.log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else settings.log_level)

    file_handler = RotatingFileHandler(
        settings.log_dir / "kzbank.log",
        maxBytes=settings.log_max_mb * 1024 * 1024,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    stream_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root.addHandler(stream_handler)

    # sentence-transformers and httpx are chatty at INFO and say nothing useful.
    for noisy in ("sentence_transformers", "httpx", "httpx2", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Logger for a module. Use `get_logger(__name__)`."""
    return logging.getLogger(name)


def log_budget_bytes() -> int:
    """Worst-case bytes this configuration can occupy on disk."""
    return settings.log_max_mb * 1024 * 1024 * (settings.log_backup_count + 1)
