import logging
import sys
from logging.handlers import RotatingFileHandler

from fin_assistant.config import settings

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(*, verbose: bool = False) -> None:
    global _configured
    if _configured:
        return

    settings.log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else settings.log_level)

    file_handler = RotatingFileHandler(
        settings.log_dir / "fin_assistant.log",
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

    for noisy in ("sentence_transformers", "httpx", "httpx2", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_budget_bytes() -> int:
    return settings.log_max_mb * 1024 * 1024 * (settings.log_backup_count + 1)
