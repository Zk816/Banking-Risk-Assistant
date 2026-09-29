"""Read saved documents, chunk them, embed them, store them.

This is the preprocessing pass: raw files in, feature cache out. Re-running it
is safe — documents are keyed by content hash, so unchanged files are skipped.

Run: uv run python scripts/ingest.py --source adilet
"""

import argparse
import hashlib
import sys
from pathlib import Path

from kzbank.config import settings
from kzbank.logging_setup import get_logger, setup_logging
from kzbank.extract.chunking import chunk_document
from kzbank.ingest import nbk_acts
from kzbank.ingest.adilet import ParsedDocument
from kzbank.ingest.adilet import parse_directory as parse_adilet
from kzbank.retrieval.embed import embed_texts
from kzbank.storage.db import (
    INSERT_CHUNK_SQL,
    INSERT_DOCUMENT_SQL,
    connect,
    fts_config,
    table_counts,
)

log = get_logger(__name__)


def store(document: ParsedDocument) -> tuple[int, int]:
    """Chunk, embed and store one document.

    Returns (document_id, chunks_written). chunks_written is 0 when the document
    was already present with identical content.
    """
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
        if row is None:  # ON CONFLICT DO NOTHING — same content already stored
            return -1, 0
        document_id = int(row["id"])

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
                    hashlib.md5(chunk.text.encode("utf-8")).hexdigest(),  # noqa: S324
                ),
            )
            # ON CONFLICT (text_hash) DO NOTHING: this exact text is already
            # stored from another act. 21% of the real corpus is such repeats —
            # boilerplate carried into every amending resolution.
            if cur.fetchone() is not None:
                written += 1

    return document_id, written


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest saved documents.")
    parser.add_argument("--source", default="adilet", choices=["adilet", "nbk"],
                        help="adilet: saved .html/.txt. nbk: .docx resolutions.")
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Override the input directory (default: RAW_DATA_DIR/<source>).",
    )
    args = parser.parse_args()
    setup_logging(verbose=getattr(args, "debug", False))

    directory = args.dir or (settings.raw_dir / args.source)
    if not directory.is_dir():
        raise SystemExit(
            f"No such directory: {directory}\n"
            f"Save acts from the Adilet web UI into it, then re-run."
        )

    # Two sources, two document shapes: Adilet acts are saved web pages, NBK
    # resolutions are Word files. Both produce the same record for storage.
    if args.source == "nbk":
        acts, problems = nbk_acts.parse_directory(directory)
        documents = [
            ParsedDocument(
                source=a.source,
                title=a.title,
                text=a.text,
                content_hash=a.content_hash,
                raw_path=a.raw_path,
                url=None,
                language=a.language,
                edition=a.number,
                published_at=a.published_at,
            )
            for a in acts
        ]
    else:
        documents, problems = parse_adilet(directory)

    for problem in problems:
        log.warning("skipped %s", problem)
        print(f"  skipped  {problem}", file=sys.stderr)

    if not documents:
        raise SystemExit(f"No usable documents in {directory}")

    print(f"\ningesting {len(documents)} document(s) from {directory}\n")
    total_chunks = 0
    for document in documents:
        document_id, n = store(document)
        if document_id < 0:
            print(f"  unchanged  {document.title[:60]}")
            continue
        total_chunks += n
        log.info(
            "ingested doc_id=%s lang=%s chunks=%d hash=%s",
            document_id, document.language, n, document.content_hash[:12],
        )
        meta = document.published_at or "no date"
        print(f"  +{n:>4} chunks  [{document.language}] {meta}  {document.title[:60]}")

    print(f"\n  {total_chunks} chunks written")
    for table, count in table_counts().items():
        if count:
            print(f"  {table:<12} {count:>6} rows")
    print()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
