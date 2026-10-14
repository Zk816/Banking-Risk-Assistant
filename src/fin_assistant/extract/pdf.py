import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from fin_assistant.extract.chunking import Chunk, normalize_whitespace, split_on_boundaries

MIN_PAGE_CHARS = 200
MIN_READABLE_SHARE = 0.85
MAX_DOUBLED_SHARE = 0.25

_LETTER = re.compile(r"[^\W\d_]")
_READABLE = re.compile(r"[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]")


class PdfTextError(Exception):
    pass


@dataclass(frozen=True)
class PdfText:
    pages: list[tuple[int, str]]
    total_pages: int
    content_hash: str


def _readable(text: str) -> bool:
    letters = _LETTER.findall(text)
    if len(text) < MIN_PAGE_CHARS or not letters:
        return False
    readable = len(_READABLE.findall(text)) / len(letters)
    doubled = sum(1 for a, b in zip(text, text[1:]) if a == b and a.isalpha()) / len(letters)
    return readable >= MIN_READABLE_SHARE and doubled <= MAX_DOUBLED_SHARE


def read_pdf(path: Path) -> PdfText:
    try:
        with pymupdf.open(path) as doc:
            raw_pages = [page.get_text() or "" for page in doc]
    except Exception as e:
        raise PdfTextError(f"{path.name}: cannot read PDF: {e}") from e
    pages = []
    for number, raw in enumerate(raw_pages, 1):
        text = normalize_whitespace(raw.replace("\t", " ").replace("\x00", ""))
        if _readable(text):
            pages.append((number, text))
    if not pages:
        raise PdfTextError(f"{path.name}: no readable text in {len(raw_pages)} pages (scan or broken fonts)")
    digest = hashlib.sha256("\n".join(t for _, t in pages).encode("utf-8")).hexdigest()
    return PdfText(pages, len(raw_pages), digest)


def chunk_pages(pdf: PdfText, *, size: int, overlap: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for number, text in pdf.pages:
        for part in split_on_boundaries(text, size=size, overlap=overlap):
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    text=part.text,
                    char_start=part.char_start,
                    char_end=part.char_end,
                    article_ref=f"стр. {number}",
                )
            )
    return chunks
