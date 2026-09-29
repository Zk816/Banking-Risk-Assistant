"""Split a document into chunks.

`chunk_document` is what ingestion uses: it cuts on article boundaries so one chunk
holds one complete rule. `chunk_text` is the naive fixed-size windower from Step 1 —
still used as the fallback for an article too long to fit in one chunk, and kept
visible so the two can be compared.
"""

from dataclasses import dataclass

from kzbank.config import settings
from kzbank.extract.structure import split_articles


@dataclass(frozen=True)
class Chunk:
    """One retrievable piece of a document.

    char_start/char_end point back into the original text, so a chunk can always
    be traced to where it came from.
    """

    ordinal: int
    text: str
    char_start: int
    char_end: int
    # Which article this came from, e.g. "Глава 3, Статья 43". None when the
    # naive windower produced it and structure was never consulted.
    article_ref: str | None = None


def normalize_whitespace(text: str) -> str:
    """Collapse runs of blank lines and strip trailing spaces.

    Saved web pages are full of layout whitespace that would otherwise eat into
    the chunk budget.
    """
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
    """Cut text into overlapping windows of `chunk_size` characters.

    Overlap exists so a sentence split across a boundary still appears whole in
    one of the two neighbours. It does not fix the boundary problem, it only
    makes it less likely to hurt.

    Raises:
        ValueError: if overlap >= chunk_size (the window would never advance).
    """
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


# How far back from the target cut we are willing to look for a clean boundary.
# Beyond this the piece gets too short to be worth the tidier edge.
_SNAP_WINDOW = 200


def _snap_back(text: str, target: int) -> int:
    """Move a cut point back to the nearest natural boundary at or before `target`.

    Tried in order of how much context each preserves: paragraph break, sentence
    end, then any whitespace. Falls back to the raw position when the text has no
    boundary within _SNAP_WINDOW — a long unbroken run has to be cut somewhere.
    """
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
    """Window `text` like chunk_text, but cut on word and sentence boundaries.

    Used inside an over-long article. chunk_text stays as it is — it is the Step 1
    baseline the eval compares against, and rewriting it would erase the
    comparison.

    Raises:
        ValueError: if overlap >= size.
    """
    if overlap >= size:
        raise ValueError(f"overlap ({overlap}) must be smaller than size ({size})")

    chunks: list[Chunk] = []
    start = 0
    ordinal = 0

    while start < len(text):
        end = _snap_back(text, start + size)
        # A boundary at or before `start` would loop forever; cut hard instead.
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
    """Split a legal text on article boundaries.

    One chunk = one Статья, so a chunk holds one complete rule and carries the
    article number it came from. An article longer than `chunk_size` is split
    further with the naive window, and every piece keeps the article reference.

    Falls back to plain windowing when the text has no article headings at all.

    Raises:
        ValueError: if overlap >= chunk_size.
    """
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
            # Article too long for one chunk. Window it — but a later piece of a
            # long article is the old problem in a new place: "3. Совет директоров
            # утверждает указанную политику" says nothing on its own, because the
            # subject was stated in the heading, in the piece before.
            #
            # So every piece after the first gets the article's heading prepended.
            # It costs a line of text and makes the piece self-contained for both
            # the encoder and keyword search.
            #
            # Trade-off: the prepended pieces no longer satisfy
            # source[char_start:char_end] == text. Offsets still point at the real
            # span the body came from, which is what provenance needs.
            # Skip the heading when it would cost more than a third of the
            # chunk: at that point the repeated prefix crowds out the body it is
            # supposed to give context to.
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
