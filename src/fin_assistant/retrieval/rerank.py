import time
from dataclasses import replace
from functools import lru_cache

from sentence_transformers import CrossEncoder

from fin_assistant.config import settings
from fin_assistant.logging_setup import get_logger
from fin_assistant.retrieval.hybrid import FusedHit

log = get_logger(__name__)

MAX_LENGTH = 512


@lru_cache(maxsize=1)
def get_reranker() -> CrossEncoder:
    return CrossEncoder(
        settings.rerank_model,
        device=settings.embed_device,
        max_length=MAX_LENGTH,
    )


def rerank(query: str, candidates: list[FusedHit], *, top_k: int) -> list[FusedHit]:
    if top_k <= 0:
        raise ValueError(f"top_k must be positive, got {top_k}")
    if not candidates:
        return []

    started = time.perf_counter()
    pairs = [(query, c.hit.text) for c in candidates]
    scores = get_reranker().predict(
        pairs,
        batch_size=settings.rerank_batch_size,
        show_progress_bar=False,
    )

    scored = [
        replace(candidate, rerank_score=float(score))
        for candidate, score in zip(candidates, scores, strict=True)
    ]
    scored.sort(key=lambda f: (-(f.rerank_score or 0.0), f.hit.chunk_id))
    kept = scored[:top_k]

    log.info(
        "rerank candidates=%d kept=%d top=%.3f worst_kept=%.3f %.2fs",
        len(candidates),
        len(kept),
        kept[0].rerank_score or 0.0,
        kept[-1].rerank_score or 0.0,
        time.perf_counter() - started,
    )
    return kept
