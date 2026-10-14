from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer

from fin_assistant.config import settings

MAX_SEQ_LENGTH = 1024


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    model = SentenceTransformer(settings.embed_model, device=settings.embed_device)
    model.max_seq_length = MAX_SEQ_LENGTH
    model.eval()
    return model


def embed_texts(texts: list[str]) -> np.ndarray:
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
    return embed_texts([text])[0]
