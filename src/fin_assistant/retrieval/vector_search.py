import time
from dataclasses import dataclass

from fin_assistant.config import settings
from fin_assistant.logging_setup import get_logger
from fin_assistant.retrieval.embed import embed_query
from fin_assistant.retrieval.filters import SearchFilter
from fin_assistant.storage.db import connect

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
    d.source      AS document_source,
    d.published_at AS document_date,
    1 - (c.embedding <=> %s) AS similarity
FROM chunks c
JOIN documents d ON d.id = c.document_id
WHERE c.embedding IS NOT NULL{filter}
ORDER BY c.embedding <=> %s
LIMIT %s
"""


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    document_id: int
    document_title: str
    document_url: str | None
    document_edition: str | None
    ordinal: int
    article_ref: str | None
    text: str
    similarity: float
    source: str | None = None
    published_at: object | None = None

    @property
    def citation(self) -> str:
        parts = [self.document_title]
        if self.article_ref:
            parts.append(self.article_ref)
        else:
            parts.append(f"фрагмент {self.ordinal}")
        return ", ".join(parts)


def search(query: str, *, top_k: int | None = None, filters: SearchFilter | None = None) -> list[Hit]:
    k = top_k if top_k is not None else settings.top_k
    if k <= 0:
        raise ValueError(f"top_k must be positive, got {k}")

    started = time.perf_counter()
    vector = embed_query(query)

    where, params = (filters or SearchFilter()).sql()
    with connect() as conn, conn.cursor() as cur:
        if where:
            cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            cur.execute("SET LOCAL hnsw.ef_search = 200")
        cur.execute(SEARCH_SQL.format(filter=where), (vector, *params, vector, k))
        rows = cur.fetchall()

    log.info(
        "search chars=%d k=%d hits=%d top_sim=%.3f %.2fs",
        len(query),
        k,
        len(rows),
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
            source=r["document_source"],
            published_at=r["document_date"],
        )
        for r in rows
    ]
