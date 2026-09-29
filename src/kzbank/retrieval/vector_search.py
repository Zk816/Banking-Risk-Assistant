"""Nearest-neighbour search over chunk embeddings.

Query and chunks go through the same encoder, so distances are comparable.
pgvector's `<=>` is cosine *distance*: 0 = identical direction, 2 = opposite.
We report similarity (1 - distance) because "higher is better" reads easier.
"""

import time
from dataclasses import dataclass

from kzbank.config import settings
from kzbank.logging_setup import get_logger
from kzbank.retrieval.embed import embed_query
from kzbank.storage.db import connect

log = get_logger(__name__)

SEARCH_SQL = """
SELECT
    c.id,
    c.document_id,
    c.ordinal,
    c.text,
    c.article_ref,
    d.title       AS document_title,
    d.url         AS document_url,
    d.edition     AS document_edition,
    1 - (c.embedding <=> %s) AS similarity
FROM chunks c
JOIN documents d ON d.id = c.document_id
WHERE c.embedding IS NOT NULL
ORDER BY c.embedding <=> %s
LIMIT %s
"""


@dataclass(frozen=True)
class Hit:
    """One retrieved chunk plus enough provenance to cite it."""

    chunk_id: int
    document_id: int
    document_title: str
    document_url: str | None
    document_edition: str | None
    ordinal: int
    article_ref: str | None
    text: str
    similarity: float

    @property
    def citation(self) -> str:
        """Short human-readable source label, e.g. 'Закон о банках, Статья 42'."""
        parts = [self.document_title]
        if self.article_ref:
            parts.append(self.article_ref)
        else:
            parts.append(f"фрагмент {self.ordinal}")
        return ", ".join(parts)


def search(query: str, *, top_k: int | None = None) -> list[Hit]:
    """Return the `top_k` chunks closest to `query` in embedding space.

    Raises:
        ValueError: if top_k is not positive.
    """
    k = top_k if top_k is not None else settings.top_k
    if k <= 0:
        raise ValueError(f"top_k must be positive, got {k}")

    started = time.perf_counter()
    vector = embed_query(query)

    with connect() as conn, conn.cursor() as cur:
        cur.execute(SEARCH_SQL, (vector, vector, k))
        rows = cur.fetchall()

    # The query text itself is not logged: a user question may carry PHI-like
    # detail. Length and result shape are enough to diagnose retrieval.
    log.info(
        "search chars=%d k=%d hits=%d top_sim=%.3f %.2fs",
        len(query), k, len(rows),
        float(rows[0]["similarity"]) if rows else 0.0,
        time.perf_counter() - started,
    )

    return [
        Hit(
            chunk_id=r["id"],
            document_id=r["document_id"],
            document_title=r["document_title"],
            document_url=r["document_url"],
            document_edition=r["document_edition"],
            ordinal=r["ordinal"],
            article_ref=r["article_ref"],
            text=r["text"],
            similarity=float(r["similarity"]),
        )
        for r in rows
    ]
