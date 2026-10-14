from dataclasses import dataclass, replace

from fin_assistant.config import settings
from fin_assistant.logging_setup import get_logger
from fin_assistant.retrieval.filters import SearchFilter
from fin_assistant.retrieval.keyword_search import search_keyword
from fin_assistant.retrieval.vector_search import Hit
from fin_assistant.retrieval.vector_search import search as search_vector

log = get_logger(__name__)

RRF_K = 60


@dataclass(frozen=True)
class FusedHit:
    hit: Hit
    rrf_score: float
    vector_rank: int | None
    keyword_rank: int | None
    rerank_score: float | None = None

    @property
    def found_by(self) -> str:
        if self.vector_rank is not None and self.keyword_rank is not None:
            return "both"
        return "vector" if self.vector_rank is not None else "keyword"


def _row_to_hit(row: dict) -> Hit:
    return Hit(
        chunk_id=row["id"],
        document_id=row["document_id"],
        document_title=row["document_title"],
        document_url=row["document_url"],
        document_edition=row["document_edition"],
        ordinal=row["ordinal"],
        article_ref=row["article_ref"],
        source=row.get("document_source"),
        published_at=row.get("document_date"),
        text=row["text"],
        similarity=0.0,
    )


def search_hybrid(
    query: str,
    *,
    top_k: int | None = None,
    candidates: int | None = None,
) -> list[FusedHit]:
    k = top_k if top_k is not None else settings.top_k
    if k <= 0:
        raise ValueError(f"top_k must be positive, got {k}")
    depth = candidates if candidates is not None else k * 3

    vector_hits = search_vector(query, top_k=depth)
    keyword_rows = search_keyword(query, top_k=depth)

    scores: dict[int, float] = {}
    hits: dict[int, Hit] = {}
    vector_rank: dict[int, int] = {}
    keyword_rank: dict[int, int] = {}

    for rank, hit in enumerate(vector_hits, start=1):
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        hits[hit.chunk_id] = hit
        vector_rank[hit.chunk_id] = rank

    for rank, row in enumerate(keyword_rows, start=1):
        chunk_id = row["id"]
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        hits.setdefault(chunk_id, _row_to_hit(row))
        keyword_rank[chunk_id] = rank

    fused = [
        FusedHit(
            hit=hits[chunk_id],
            rrf_score=score,
            vector_rank=vector_rank.get(chunk_id),
            keyword_rank=keyword_rank.get(chunk_id),
        )
        for chunk_id, score in scores.items()
    ]
    fused.sort(key=lambda f: (-f.rrf_score, f.hit.chunk_id))
    fused = fused[:k]

    log.info(
        "hybrid vector=%d keyword=%d fused=%d both=%d",
        len(vector_hits),
        len(keyword_rows),
        len(fused),
        sum(1 for f in fused if f.found_by == "both"),
    )
    return fused


def gather_candidates(
    query: str,
    *,
    depth: int | None = None,
    filters: SearchFilter | None = None,
) -> list[FusedHit]:
    k = depth if depth is not None else settings.rerank_candidates
    if k <= 0:
        raise ValueError(f"depth must be positive, got {k}")

    seen: set[int] = set()
    pool: list[FusedHit] = []

    for hit in search_vector(query, top_k=k, filters=filters):
        seen.add(hit.chunk_id)
        pool.append(FusedHit(hit=hit, rrf_score=0.0, vector_rank=None, keyword_rank=None))

    for row in search_keyword(query, top_k=k, filters=filters):
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        pool.append(FusedHit(hit=_row_to_hit(row), rrf_score=0.0, vector_rank=None, keyword_rank=None))

    log.info("candidates pooled=%d (depth=%d per retriever)", len(pool), k)
    return pool


def to_hits(fused: list[FusedHit]) -> list[Hit]:
    return [replace(f.hit) for f in fused]
