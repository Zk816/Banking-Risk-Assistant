"""Postgres connection helpers.

Thin on purpose: open a connection, register the pgvector type, hand back a
cursor. Everything above this layer writes its own SQL.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from kzbank.config import settings


@contextmanager
def connect(*, with_vector: bool = True) -> Iterator[psycopg.Connection]:
    """Open a connection with dict-shaped rows.

    Commits on clean exit, rolls back if the block raises.

    `with_vector=False` skips registering the pgvector type adapter. Needed only
    during bootstrap: registering it requires the `vector` type to already exist,
    and the statement that creates it lives in schema.sql.
    """
    with psycopg.connect(str(settings.database_url), row_factory=dict_row) as conn:
        if with_vector:
            register_vector(conn)
        yield conn


def fetch_all(sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    """Run a query and return every row."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(sql: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
    """Run a query and return the first row, or None."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


INSERT_DOCUMENT_SQL = """
INSERT INTO documents (source, title, url, language, edition, published_at,
                       content_hash, raw_path)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source, content_hash) DO NOTHING
RETURNING id
"""

INSERT_CHUNK_SQL = """
INSERT INTO chunks (document_id, ordinal, text, article_ref, char_start, char_end,
                    embedding, tsv, text_hash)
VALUES (%s, %s, %s, %s, %s, %s, %s, to_tsvector(%s, %s), %s)
ON CONFLICT (text_hash) DO NOTHING
RETURNING id
"""

INSERT_METRIC_SQL = """
INSERT INTO metrics (bin, metric, period, value, unit, source_doc, published_at)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (bin, metric, period, source_doc) DO UPDATE
SET value = EXCLUDED.value, retrieved_at = now()
"""

# Postgres ships no Kazakh stemmer, so Kazakh falls back to 'simple' (lowercase
# and split, no stemming). Noted here because it will hurt recall in Step 3.
_FTS_CONFIG = {"ru": "russian", "en": "english"}


def fts_config(language: str | None) -> str:
    """Postgres text-search configuration for a document language."""
    return _FTS_CONFIG.get(language or "", "simple")


def table_counts() -> dict[str, int]:
    """Row count per table. Used by init_db.py to show the DB is really there."""
    counts: dict[str, int] = {}
    with connect() as conn, conn.cursor() as cur:
        for table in ("entities", "documents", "chunks", "metrics"):
            # Table names are a fixed literal tuple above, never user input.
            cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608
            row = cur.fetchone()
            counts[table] = int(row["n"]) if row else 0
    return counts
