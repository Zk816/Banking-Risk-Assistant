"""Re-score retrieved candidates with a cross-encoder.

The difference from the embedding model is what it *sees*.

bge-m3 is a **bi-encoder**: query and chunk are encoded separately, never meet,
and similarity is a dot product between two vectors computed in isolation. That
is what makes it fast enough to run over the whole corpus once, offline — but it
also means the chunk's vector was fixed long before anyone asked this question.

bge-reranker-v2-m3 is a **cross-encoder**: query and chunk go through the network
*together*, attending to each other. It can notice that a chunk matched on common
words rather than on meaning. It cannot be precomputed, so it only ever runs on
the handful of candidates retrieval already shortlisted.

Two stages, same shape as a detector proposing regions and a second head scoring
them: recall first, precision second.
"""

import time
from dataclasses import replace
from functools import lru_cache

from sentence_transformers import CrossEncoder

from kzbank.config import settings
from kzbank.logging_setup import get_logger
from kzbank.retrieval.hybrid import FusedHit

log = get_logger(__name__)

# Query plus one chunk must fit. Chunks are ~450 chars after Step 2, so 512
# tokens is comfortable; longer inputs are truncated by the tokenizer.
MAX_LENGTH = 512


@lru_cache(maxsize=1)
def get_reranker() -> CrossEncoder:
    """Load the cross-encoder once per process.

    Weights come from ~/.cache/huggingface; nothing is downloaded at runtime.
    """
    return CrossEncoder(
        settings.rerank_model,
        device=settings.embed_device,
        max_length=MAX_LENGTH,
    )


def rerank(query: str, candidates: list[FusedHit], *, top_k: int) -> list[FusedHit]:
    """Re-score candidates against the query and return the best `top_k`.

    Returns them ordered by cross-encoder score, each carrying that score so the
    decision stays inspectable. An empty candidate list comes back empty — the
    model is not called.

    Raises:
        ValueError: if top_k is not positive.
    """
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
        len(candidates), len(kept),
        kept[0].rerank_score or 0.0,
        kept[-1].rerank_score or 0.0,
        time.perf_counter() - started,
    )
    return kept
