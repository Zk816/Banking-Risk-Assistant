import hashlib

from fin_assistant.extract.chunking import Chunk, chunk_document
from fin_assistant.ingest.adilet import ParsedDocument
from fin_assistant.retrieval.embed import embed_texts
from fin_assistant.storage.db import INSERT_CHUNK_SQL, INSERT_DOCUMENT_SQL, connect, fts_config


def store_document(document: ParsedDocument, chunks: list[Chunk] | None = None) -> tuple[int, int]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            INSERT_DOCUMENT_SQL,
            (
                document.source,
                document.title,
                document.url,
                document.language,
                document.edition,
                document.published_at,
                document.content_hash,
                document.raw_path,
            ),
        )
        row = cur.fetchone()
        if row is None:
            return -1, 0
        document_id = int(row["id"])

        if chunks is None:
            chunks = chunk_document(document.text)
        if not chunks:
            return document_id, 0

        vectors = embed_texts([c.text for c in chunks])
        config = fts_config(document.language)

        written = 0
        for chunk, vector in zip(chunks, vectors, strict=True):
            cur.execute(
                INSERT_CHUNK_SQL,
                (
                    document_id,
                    chunk.ordinal,
                    chunk.text,
                    chunk.article_ref,
                    chunk.char_start,
                    chunk.char_end,
                    vector,
                    config,
                    chunk.text,
                    hashlib.md5(chunk.text.encode("utf-8")).hexdigest(),
                ),
            )
            if cur.fetchone() is not None:
                written += 1

    return document_id, written
