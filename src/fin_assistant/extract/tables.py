from dataclasses import dataclass
from pathlib import Path

from fin_assistant.extract.chunking import Chunk
from fin_assistant.ingest.nbk_stats import _sheets

HEADER_ROWS = 4
MAX_CELL_CHARS = 80
MAX_ROWS_PER_SHEET = 2000


class TableTextError(Exception):
    pass


@dataclass(frozen=True)
class SheetText:
    name: str
    header: list[str]
    rows: list[str]


def _cell(c: object) -> str:
    if c is None:
        return ""
    if isinstance(c, float):
        return f"{c:.4f}".rstrip("0").rstrip(".") if abs(c) < 1e15 else str(c)
    return " ".join(str(c).split())[:MAX_CELL_CHARS]


def read_sheets(path: Path) -> list[SheetText]:
    try:
        sheets = _sheets(path)
    except Exception as e:
        raise TableTextError(f"{path.name}: {e}") from e
    out = []
    for name, rows in sheets:
        lines = []
        for r in rows[:MAX_ROWS_PER_SHEET]:
            cells = [_cell(c) for c in r]
            while cells and not cells[-1]:
                cells.pop()
            if any(cells):
                lines.append(" | ".join(cells))
        if lines:
            out.append(SheetText(str(name).strip(), lines[:HEADER_ROWS], lines[HEADER_ROWS:]))
    if not out:
        raise TableTextError(f"{path.name}: no text in any sheet")
    return out


def chunk_sheets(title: str, sheets: list[SheetText], *, size: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for sheet in sheets:
        prefix = f"{title}\nЛист: {sheet.name}\n" + "\n".join(sheet.header) + "\n"
        budget = max(size - len(prefix), size // 3)
        block: list[str] = []
        for line in sheet.rows + [None]:
            if line is None or (block and sum(len(x) + 1 for x in block) + len(line) > budget):
                if block or not sheet.rows:
                    text = prefix + "\n".join(block)
                    chunks.append(
                        Chunk(
                            ordinal=len(chunks),
                            text=text[: size * 2],
                            char_start=0,
                            char_end=len(text),
                            article_ref=f"лист {sheet.name}",
                        )
                    )
                block = []
            if line is not None:
                block.append(line)
    return chunks
