"""Read legal acts saved from the Adilet web UI.

Why files and not a fetcher: adilet.zan.kz serves a JavaScript-only shell to
non-browser clients. The act text arrives from an endpoint their robots.txt
disallows for every agent, and their own page source says the omission is
deliberate ("цель — чтобы нас цитировали, а не выкачивали корпус"). So the
corpus is saved by hand from the browser and parsed here.

Supported inputs: .html / .htm (browser "Save page as") and .txt (copy-paste).
PDF is Step 9.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

SOURCE = "adilet"
SUPPORTED_SUFFIXES = frozenset({".html", ".htm", ".txt"})

# Tags that carry layout, not content.
_NOISE_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "svg", "form")

# Adilet document URLs look like https://adilet.zan.kz/rus/docs/Z950002444_
_URL_RE = re.compile(r"https?://adilet\.zan\.kz/\S+", re.IGNORECASE)
# "от 31 августа 1995 года" / "от 31.08.1995"
_DATE_DOTTED_RE = re.compile(r"от\s+(\d{2})\.(\d{2})\.(\d{4})")
_DATE_WORDS_RE = re.compile(r"от\s+(\d{1,2})\s+([а-яё]+)\s+(\d{4})\s*года", re.IGNORECASE)
# "с изменениями и дополнениями по состоянию на 01.01.2026"
_EDITION_RE = re.compile(r"по состоянию на\s+(\d{2}\.\d{2}\.\d{4})")

_RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# A saved page that captured only the SPA shell yields almost no text. Below
# this many characters we assume the save failed rather than ingest a stub.
MIN_USABLE_CHARS = 500


@dataclass(frozen=True)
class ParsedDocument:
    """One legal act, ready to be stored."""

    source: str
    title: str
    text: str
    content_hash: str
    raw_path: str
    url: str | None = None
    language: str = "ru"
    edition: str | None = None
    published_at: date | None = None


class UnusableDocumentError(Exception):
    """Raised when a file contains no usable act text."""


def _strip_noise(soup: BeautifulSoup) -> None:
    for tag in soup(_NOISE_TAGS):
        tag.decompose()


def _extract_title(soup: BeautifulSoup, fallback: str) -> str:
    for selector in ("h1", "title"):
        node = soup.find(selector)
        if node:
            text = node.get_text(" ", strip=True)
            # Adilet's SPA shell has a generic <title>; it tells us nothing.
            if text and text != "НЦПС «Әділет»":
                return text
    return fallback


def _extract_published_at(text: str) -> date | None:
    m = _DATE_DOTTED_RE.search(text)
    if m:
        day, month, year = (int(g) for g in m.groups())
        return date(year, month, day)

    m = _DATE_WORDS_RE.search(text)
    if m:
        day, month_name, year = m.group(1), m.group(2).lower(), m.group(3)
        month = _RU_MONTHS.get(month_name)
        if month:
            return date(int(year), month, int(day))
    return None


def _detect_language(text: str) -> str:
    """Crude Russian/Kazakh split on characters unique to the Kazakh alphabet."""
    kazakh_only = sum(text.count(ch) for ch in "әғқңөұүhі")
    return "kk" if kazakh_only > len(text) * 0.005 else "ru"


def parse_file(path: Path) -> ParsedDocument:
    """Parse one saved act.

    Raises:
        UnusableDocumentError: if the file type is unsupported, or the file holds
            too little text to be a real act (usually a saved SPA shell).
    """
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnusableDocumentError(
            f"{path.name}: unsupported type '{suffix}'. "
            f"Expected one of {sorted(SUPPORTED_SUFFIXES)}."
        )

    raw = path.read_text(encoding="utf-8", errors="replace")

    if suffix == ".txt":
        title, text = path.stem, raw
    else:
        soup = BeautifulSoup(raw, "lxml")
        _strip_noise(soup)
        title = _extract_title(soup, fallback=path.stem)
        text = soup.get_text("\n", strip=True)

    if len(text) < MIN_USABLE_CHARS:
        raise UnusableDocumentError(
            f"{path.name}: only {len(text)} characters of text. "
            f"If this came from adilet.zan.kz, the save captured the empty page "
            f"shell — open the act in the browser, wait for the text to render, "
            f"then save (or copy the text into a .txt file)."
        )

    url_match = _URL_RE.search(raw)
    edition_match = _EDITION_RE.search(text)

    return ParsedDocument(
        source=SOURCE,
        title=title,
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        raw_path=str(path),
        url=url_match.group(0) if url_match else None,
        language=_detect_language(text),
        edition=edition_match.group(1) if edition_match else None,
        published_at=_extract_published_at(text),
    )


def parse_directory(directory: Path) -> tuple[list[ParsedDocument], list[str]]:
    """Parse every supported file in `directory`.

    Returns (documents, problems). Problems are reported, never swallowed — a
    file that failed to parse is something you need to know about.
    """
    documents: list[ParsedDocument] = []
    problems: list[str] = []

    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            documents.append(parse_file(path))
        except UnusableDocumentError as e:
            problems.append(str(e))

    return documents, problems
