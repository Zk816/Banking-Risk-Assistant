from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from fin_assistant.config import settings
from fin_assistant.logging_setup import get_logger

log = get_logger(__name__)


class _NoOp:
    def update(self, **_: Any) -> None:
        return None


@lru_cache(maxsize=1)
def _client():
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception:
        log.exception("langfuse client could not be created; tracing off")
        return None


@contextmanager
def observe(name: str, as_type: str = "span", **fields: Any) -> Iterator[Any]:
    client = _client()
    if client is None:
        yield _NoOp()
        return
    with client.start_as_current_observation(name=name, as_type=as_type, **fields) as obs:
        yield obs


def flush() -> None:
    client = _client()
    if client is not None:
        client.flush()
