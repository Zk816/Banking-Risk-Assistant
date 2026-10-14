from dataclasses import dataclass

from fin_assistant.config import settings
from fin_assistant.extract.structure import split_articles


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    char_start: int
    char_end: int
    article_ref: str | None = None


def normalize_whitespace(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if line:
            blank_run = 0
            out.append(line)
        else:
            blank_run += 1
            if blank_run <= 1:
                out.append("")
    return "\n".join(out).strip()


def chunk_text(
    text: str,
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[Chunk]:
    size = chunk_size if chunk_size is not None else settings.chunk_size
    lap = overlap if overlap is not None else settings.chunk_overlap

    if size <= 0:
        raise ValueError(f"chunk_size must be positive, got {size}")
    if lap >= size:
        raise ValueError(f"overlap ({lap}) must be smaller than chunk_size ({size})")

    text = normalize_whitespace(text)
    if not text:
        return []

    stride = size - lap
    chunks: list[Chunk] = []
    start = 0
    ordinal = 0

    while start < len(text):
        end = min(start + size, len(text))
        piece = text[start:end].strip()
        if piece:
            chunks.append(Chunk(ordinal=ordinal, text=piece, char_start=start, char_end=end))
            ordinal += 1
        if end == len(text):
            break
        start += stride

    return chunks


_SNAP_WINDOW = 200


def _snap_back(text: str, target: int) -> int:
    if target >= len(text):
        return len(text)

    floor = max(0, target - _SNAP_WINDOW)
    window = text[floor:target]

    for marker in ("\n\n", "\n", ". ", "; "):
        found = window.rfind(marker)
        if found != -1:
            return floor + found + len(marker)

    found = window.rfind(" ")
    return floor + found + 1 if found != -1 else target


def split_on_boundaries(text: str, *, size: int, overlap: int) -> list[Chunk]:
    if overlap >= size:
        raise ValueError(f"overlap ({overlap}) must be smaller than size ({size})")

    chunks: list[Chunk] = []
    start = 0
    ordinal = 0

    while start < len(text):
        end = _snap_back(text, start + size)
        if end <= start:
            end = min(start + size, len(text))

        piece = text[start:end].strip()
        if piece:
            chunks.append(Chunk(ordinal=ordinal, text=piece, char_start=start, char_end=end))
            ordinal += 1

        if end >= len(text):
            break
        start = max(_snap_back(text, end - overlap), start + 1)

    return chunks


def chunk_document(
    text: str,
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[Chunk]:
    size = chunk_size if chunk_size is not None else settings.chunk_size
    lap = overlap if overlap is not None else settings.chunk_overlap

    text = normalize_whitespace(text)
    if not text:
        return []

    chunks: list[Chunk] = []
    ordinal = 0

    for section in split_articles(text):
        label = section.ref
        if section.chapter:
            label = f"{section.chapter}, {section.ref}"

        if len(section.text) <= size:
            pieces = [(section.text, section.char_start, section.char_end)]
        else:
            heading = section.heading
            if len(heading) > size // 3:
                heading = ""
            budget = size - len(heading) - 2 if heading else size
            parts = split_on_boundaries(section.text, size=budget, overlap=lap)
            pieces = []
            for i, part in enumerate(parts):
                body = part.text if i == 0 or not heading else f"{heading}\n\n{part.text}"
                pieces.append(
                    (
                        body,
                        section.char_start + part.char_start,
                        section.char_start + part.char_end,
                    )
                )

        for body, start, end in pieces:
            chunks.append(
                Chunk(
                    ordinal=ordinal,
                    text=body,
                    char_start=start,
                    char_end=end,
                    article_ref=label,
                )
            )
            ordinal += 1

    return chunks
