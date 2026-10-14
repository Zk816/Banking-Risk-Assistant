import re
import time
from typing import Any

from fin_assistant.config import settings
from fin_assistant.logging_setup import get_logger
from fin_assistant.retrieval.filters import SearchFilter
from fin_assistant.storage.db import connect

log = get_logger(__name__)

_TERM_RE = re.compile(r"\w[\w-]*", re.UNICODE)

_QUERY_CONFIG = "russian"

KEYWORD_SQL = """
SELECT
    c.id,
    c.document_id,
    c.ordinal,
    c.text,
    c.article_ref,
    d.title   AS document_title,
    d.url     AS document_url,
    d.edition AS document_edition,
    d.source  AS document_source,
    d.published_at AS document_date,
    ts_rank_cd(c.tsv, q) AS rank
FROM chunks c
JOIN documents d ON d.id = c.document_id,
     to_tsquery(%s, %s) q
WHERE c.tsv @@ q{filter}
ORDER BY rank DESC, c.id
LIMIT %s
"""


def build_tsquery(query: str) -> str:
    terms = _TERM_RE.findall(query.lower())
    return " | ".join(terms)


def search_keyword(
    query: str, *, top_k: int | None = None, filters: SearchFilter | None = None
) -> list[dict[str, Any]]:
    k = top_k if top_k is not None else settings.top_k
    if k <= 0:
        raise ValueError(f"top_k must be positive, got {k}")

    tsquery = build_tsquery(query)
    if not tsquery:
        log.info("keyword skipped: query has no searchable terms")
        return []

    started = time.perf_counter()
    with connect() as conn, conn.cursor() as cur:
        where, params = (filters or SearchFilter()).sql()
        cur.execute(KEYWORD_SQL.format(filter=where), (_QUERY_CONFIG, tsquery, *params, k))
        rows = cur.fetchall()

    log.info(
        "keyword terms=%d hits=%d top_rank=%.4f %.3fs",
        tsquery.count("|") + 1,
        len(rows),
        float(rows[0]["rank"]) if rows else 0.0,
        time.perf_counter() - started,
    )
    return rows
