"""Turn text into vectors with bge-m3.

A frozen pretrained encoder in eval mode. Nothing here is trained; we only run
forward passes and keep the output features.
"""

from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer

from kzbank.config import settings

# bge-m3 was trained with an 8192-token window, but chunks are ~1000 characters,
# so a much smaller limit saves memory with no loss.
MAX_SEQ_LENGTH = 1024


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """Load bge-m3 once per process.

    The weights come from ~/.cache/huggingface; nothing is downloaded at runtime.
    Loading takes a few seconds, so callers should not do this per request.
    """
    model = SentenceTransformer(settings.embed_model, device=settings.embed_device)
    model.max_seq_length = MAX_SEQ_LENGTH
    model.eval()
    return model


def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed a batch of texts. Returns an (n, EMBED_DIM) float32 array.

    Vectors are L2-normalised, which makes cosine similarity a plain dot product
    and matches what the pgvector cosine index expects.

    Raises:
        ValueError: if the model's output dimension disagrees with EMBED_DIM.
    """
    if not texts:
        return np.empty((0, settings.embed_dim), dtype=np.float32)

    vectors = get_model().encode(
        texts,
        batch_size=settings.embed_batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    vectors = np.asarray(vectors, dtype=np.float32)

    if vectors.shape[1] != settings.embed_dim:
        raise ValueError(
            f"{settings.embed_model} produced {vectors.shape[1]}-d vectors but "
            f"EMBED_DIM is {settings.embed_dim} and schema.sql declares "
            f"vector({settings.embed_dim}). Fix .env and the schema together."
        )
    return vectors


def embed_query(text: str) -> np.ndarray:
    """Embed a single query. Returns a 1-D array of length EMBED_DIM.

    Uses the same encoder as the chunks — a query and a document must land in
    the same space or the distances are meaningless.
    """
    return embed_texts([text])[0]
