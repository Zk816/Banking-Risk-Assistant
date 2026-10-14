from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from fin_assistant.config import settings


@contextmanager
def connect(*, with_vector: bool = True) -> Iterator[psycopg.Connection]:
    with psycopg.connect(str(settings.database_url), row_factory=dict_row) as conn:
        if with_vector:
            register_vector(conn)
        yield conn


def fetch_all(sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(sql: str, params: tuple[Any, ...] | None = None) -> dict[str, Any] | None:
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

_FTS_CONFIG = {"ru": "russian", "en": "english"}


def fts_config(language: str | None) -> str:
    return _FTS_CONFIG.get(language or "", "simple")


def table_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    with connect() as conn, conn.cursor() as cur:
        for table in ("entities", "documents", "chunks", "metrics"):
            cur.execute(f"SELECT count(*) AS n FROM {table}")
            row = cur.fetchone()
            counts[table] = int(row["n"]) if row else 0
    return counts


UPSERT_METRIC_INFO_SQL = """
INSERT INTO metric_info (metric, label_ru, label_en, unit, source, description)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (metric) DO UPDATE
SET label_ru = COALESCE(EXCLUDED.label_ru, metric_info.label_ru),
    label_en = COALESCE(EXCLUDED.label_en, metric_info.label_en),
    unit = EXCLUDED.unit, source = EXCLUDED.source,
    description = COALESCE(EXCLUDED.description, metric_info.description)
"""
