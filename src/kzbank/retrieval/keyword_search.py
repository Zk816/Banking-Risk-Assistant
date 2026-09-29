"""Full-text search over chunks, using Postgres' own index.

This exists because the encoder cannot see exact tokens: cos("норматив k2",
"норматив k1-2") = +0.83, though they are different ratios with different
thresholds. Keyword search matches "k2" as a literal string and has no opinion
about meaning.

OR, not AND. `plainto_tsquery('russian', 'норматив k2')` builds `'нормат' & 'k2'`
and matches nothing here — Статья 43 carries the value but never says "норматив".
Requiring every term is how keyword search returns an empty list on a perfectly
good question. We OR the terms and let the ranking sort it out.
"""

import re
import time
from typing import Any

from kzbank.config import settings
from kzbank.logging_setup import get_logger
from kzbank.storage.db import connect

log = get_logger(__name__)

# Terms are rebuilt from scratch rather than passed through, so nothing the user
# types can reach to_tsquery as operator syntax.
_TERM_RE = re.compile(r"\w[\w-]*", re.UNICODE)

# Postgres has no Kazakh stemmer. Russian is the best default for our corpus;
# Kazakh text still matches on exact tokens, just without stemming.
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
    ts_rank_cd(c.tsv, q) AS rank
FROM chunks c
JOIN documents d ON d.id = c.document_id,
     to_tsquery(%s, %s) q
WHERE c.tsv @@ q
ORDER BY rank DESC, c.id
LIMIT %s
"""


def build_tsquery(query: str) -> str:
    """Turn free text into an OR-joined tsquery string.

    Returns an empty string when the query has no usable terms, which callers
    must treat as "no keyword results" rather than running the query.
    """
    terms = _TERM_RE.findall(query.lower())
    return " | ".join(terms)


def search_keyword(query: str, *, top_k: int | None = None) -> list[dict[str, Any]]:
    """Rank chunks by full-text relevance. Returns raw rows, newest caller wraps them.

    Raises:
        ValueError: if top_k is not positive.
    """
    k = top_k if top_k is not None else settings.top_k
    if k <= 0:
        raise ValueError(f"top_k must be positive, got {k}")

    tsquery = build_tsquery(query)
    if not tsquery:
        log.info("keyword skipped: query has no searchable terms")
        return []

    started = time.perf_counter()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(KEYWORD_SQL, (_QUERY_CONFIG, tsquery, k))
        rows = cur.fetchall()

    log.info(
        "keyword terms=%d hits=%d top_rank=%.4f %.3fs",
        tsquery.count("|") + 1, len(rows),
        float(rows[0]["rank"]) if rows else 0.0,
        time.perf_counter() - started,
    )
    return rows
