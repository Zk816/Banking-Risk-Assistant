import pytest
from pydantic import ValidationError

from fin_assistant.config import Settings

BASE = {"database_url": "postgresql://u:p@localhost:5435/db"}


def test_loads_with_defaults() -> None:
    s = Settings(**BASE)
    assert s.embed_dim == 1024
    assert s.chunk_overlap < s.chunk_size


def test_overlap_not_smaller_than_chunk_is_rejected() -> None:
    with pytest.raises(ValueError, match="chunk_overlap"):
        Settings(**BASE, chunk_size=500, chunk_overlap=500)


def test_garbage_database_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="not-a-dsn")


def test_nonpositive_top_k_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**BASE, top_k=0)
