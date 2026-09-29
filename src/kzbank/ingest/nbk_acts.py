"""National Bank resolutions downloaded as Word documents.

Separate from `ingest/nbk.py`, which handles the exchange-rate feed: same
publisher, entirely different shape — one is a daily XML feed of numbers, this
is a pile of 600 KB legal documents.

The metadata comes from the act's own first page, which is a bilingual header:

    НАЦИОНАЛЬНЫЙ БАНК РЕСПУБЛИКИ КАЗАХСТАН
    ПОСТАНОВЛЕНИЕ ПРАВЛЕНИЯ
    22 июня 2026 года    № 60    город Астана
    О внесении изменений …
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from kzbank.extract.docx import DocxError, extract_text
from kzbank.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "nbk_npa"
SUPPORTED_SUFFIXES = frozenset({".docx"})

_NUMBER_RE = re.compile(r"№\s*(\d+(?:-\d+)?)")
_DATE_RE = re.compile(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})\s*года", re.IGNORECASE)
# The title is the sentence after the header block, starting with О/Об/О внесении.
_TITLE_RE = re.compile(r"^(Об?\s+.{15,400}?)(?=\n\n|\nВ соответствии|\nПравление)", re.DOTALL | re.MULTILINE)

_RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# Below this a "resolution" is a cover note or a scan placeholder, not an act.
MIN_USABLE_CHARS = 2000


class ActError(Exception):
    """The file is not a usable resolution."""


@dataclass(frozen=True)
class Act:
    """One resolution, ready to store."""

    source: str
    title: str
    text: str
    content_hash: str
    raw_path: str
    number: str | None = None
    published_at: date | None = None
    language: str = "ru"


def parse_act(path: Path) -> Act:
    """Read one .docx resolution.

    Raises:
        ActError: if the file cannot be read or holds too little text.
    """
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ActError(f"{path.name}: {path.suffix} не поддерживается")

    try:
        text = extract_text(path).text
    except DocxError as e:
        raise ActError(str(e)) from e

    if len(text) < MIN_USABLE_CHARS:
        raise ActError(f"{path.name}: только {len(text)} символов — не похоже на акт")

    number = _NUMBER_RE.search(text[:2000])
    return Act(
        source=SOURCE,
        title=_extract_title(text, fallback=path.stem),
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        raw_path=str(path),
        number=number.group(1) if number else None,
        published_at=_extract_date(text[:2000]),
        language="ru",
    )


def _extract_title(text: str, *, fallback: str) -> str:
    """The act's subject line, or the filename if the header is unusual."""
    match = _TITLE_RE.search(text[:4000])
    if match:
        return " ".join(match.group(1).split())[:300]
    return fallback


def _extract_date(head: str) -> date | None:
    match = _DATE_RE.search(head)
    if not match:
        return None
    month = _RU_MONTHS.get(match.group(2).lower())
    if not month:
        return None
    return date(int(match.group(3)), month, int(match.group(1)))


def parse_directory(directory: Path) -> tuple[list[Act], list[str]]:
    """Parse every .docx in `directory`. Returns (acts, problems).

    Problems are returned, never swallowed: a file that failed to parse is
    something the operator needs to see, and the .doc ones will fail every run
    until they are converted.
    """
    acts: list[Act] = []
    problems: list[str] = []

    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".docx", ".doc"}:
            continue
        try:
            acts.append(parse_act(path))
        except ActError as e:
            problems.append(str(e))

    log.info("nbk acts parsed=%d problems=%d", len(acts), len(problems))
    return acts, problems
