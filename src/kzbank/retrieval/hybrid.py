"""Fuse the vector ranking and the keyword ranking into one list.

Reciprocal Rank Fusion. The two scores are not comparable — cosine similarity
sits in [0,1] with a high floor, ts_rank_cd is unbounded and depends on term
frequency — so normalising and adding them means inventing a weight you cannot
justify. Same reason you don't add a Dice loss to a cross-entropy loss without
having tuned the coefficient.

RRF throws the scores away and keeps only position: each document collects
1 / (RRF_K + rank) from every list it appears in, and those are summed. Voting
instead of averaging. Cheap, and hard to beat.
"""

from dataclasses import dataclass, replace

from kzbank.config import settings
from kzbank.logging_setup import get_logger
from kzbank.retrieval.keyword_search import search_keyword
from kzbank.retrieval.vector_search import Hit, search as search_vector

log = get_logger(__name__)

# 60 is the value from the original RRF paper (Cormack et al., 2009). It damps
# the difference between the top ranks: rank 1 scores 1/61, rank 2 scores 1/62,
# so a document has to do well in *both* lists to climb, rather than winning on
# one list alone.
RRF_K = 60


@dataclass(frozen=True)
class FusedHit:
    """A hit plus where each retriever placed it. Keeps the fusion debuggable."""

    hit: Hit
    rrf_score: float
    vector_rank: int | None
    keyword_rank: int | None
    # Filled in by the cross-encoder in Step 4; None means it never ran.
    rerank_score: float | None = None

    @property
    def found_by(self) -> str:
        """Which retrievers returned this chunk — 'both', 'vector' or 'keyword'."""
        if self.vector_rank is not None and self.keyword_rank is not None:
            return "both"
        return "vector" if self.vector_rank is not None else "keyword"


def _row_to_hit(row: dict) -> Hit:
    """Keyword rows carry no similarity, so it is recorded as 0.0."""
    return Hit(
        chunk_id=row["id"],
        document_id=row["document_id"],
        document_title=row["document_title"],
        document_url=row["document_url"],
        document_edition=row["document_edition"],
        ordinal=row["ordinal"],
        article_ref=row["article_ref"],
        text=row["text"],
        similarity=0.0,
    )


def search_hybrid(
    query: str,
    *,
    top_k: int | None = None,
    candidates: int | None = None,
) -> list[FusedHit]:
    """Run both retrievers and fuse their rankings.

    `candidates` is how deep each retriever goes before fusion; it defaults to
    3x top_k so a chunk ranked poorly by one retriever can still be rescued by
    the other. Returns at most `top_k` results.

    Raises:
        ValueError: if top_k is not positive.
    """
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
        # Prefer the vector hit — it carries a real similarity score.
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
        len(vector_hits), len(keyword_rows), len(fused),
        sum(1 for f in fused if f.found_by == "both"),
    )
    return fused


def gather_candidates(
    query: str,
    *,
    depth: int | None = None,
) -> list[FusedHit]:
    """Union both retrievers' results, unranked, for the reranker to judge.

    This replaced RRF on the answer path, and the reason is measured. On the real
    corpus:

        vector only               R@1 0.947   MRR 0.965   0 misses
        RRF -> reranker           R@1 0.947   MRR 0.947   1 MISS
        union -> reranker         R@1 1.000   MRR 1.000   0 misses

    RRF was doing a job the reranker does better, and doing it *first* — fusing
    to twenty and discarding everything else, so a chunk RRF demoted never
    reached the model that could have rescued it. On "Что такое банк второго
    уровня?" vector ranked the right chunk first at 0.645, keyword flooded the
    list with NBK points scoring 1.9 (every line of a banking regulation says
    "банк"), and RRF pushed the correct chunk out of the top five entirely.

    Ordering here is meaningless by design: every hit carries `rrf_score=0` and
    no ranks, because nothing has ranked them yet. `search_hybrid` is kept for
    the eval's ablation, which still needs RRF as a comparison point.

    Raises:
        ValueError: if depth is not positive.
    """
    k = depth if depth is not None else settings.rerank_candidates
    if k <= 0:
        raise ValueError(f"depth must be positive, got {k}")

    seen: set[int] = set()
    pool: list[FusedHit] = []

    for hit in search_vector(query, top_k=k):
        seen.add(hit.chunk_id)
        pool.append(FusedHit(hit=hit, rrf_score=0.0, vector_rank=None, keyword_rank=None))

    for row in search_keyword(query, top_k=k):
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        pool.append(
            FusedHit(hit=_row_to_hit(row), rrf_score=0.0,
                     vector_rank=None, keyword_rank=None)
        )

    log.info("candidates pooled=%d (depth=%d per retriever)", len(pool), k)
    return pool


def to_hits(fused: list[FusedHit]) -> list[Hit]:
    """Unwrap fused results for callers that only need the chunks."""
    return [replace(f.hit) for f in fused]
