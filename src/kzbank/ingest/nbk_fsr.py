"""The National Bank's Financial Stability Reports, as text for retrieval.

nationalbank.kz/ru/news/otchet-o-finansovoy-stabilnosti has one page per year
with the report as a PDF. The reports are the regulator's own analysis of
risks in the banking sector (loan quality, unsecured consumer lending,
liquidity, dollarisation), so they answer "why" questions that tables cannot.
The PDFs are born-digital: pypdf extracts the text directly, no OCR.
"""

import hashlib
import html
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from kzbank.ingest.nbk_stats import BASE, DELAY_S, TIMEOUT_S, USER_AGENT, _get
from kzbank.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "nbk_fsr"
INDEX = f"{BASE}/ru/news/otchet-o-finansovoy-stabilnosti"
_RUBRIC = re.compile(r"otchet-o-finansovoy-stabilnosti/rubrics/(\d+)")
_FILE = re.compile(r'href="/file/download/(\d+)"[^>]*>\s*([^<]{3,200})')
_YEAR = re.compile(r"(20\d{2})")


class FsrError(Exception):
    """A report could not be listed, downloaded or read."""


@dataclass(frozen=True)
class FsrFile:
    file_id: str
    title: str


def list_reports() -> list[FsrFile]:
    """Every report PDF on every year page (Russian-language titles only)."""
    ids = sorted(set(_RUBRIC.findall(_get(INDEX))))
    out: dict[str, FsrFile] = {}
    for rubric in [None, *ids]:
        page = _get(INDEX if rubric is None else f"{INDEX}/rubrics/{rubric}")
        for file_id, title in _FILE.findall(page):
            title = html.unescape(title).strip()
            if title and "финансовой стабильности" in title.lower():
                out.setdefault(file_id, FsrFile(file_id, title))
        time.sleep(DELAY_S)
    return list(out.values())


def download(f: FsrFile, raw_dir: Path) -> Path:
    """Save one report as raw_dir/fsr_<id>.pdf.

    Raises:
        FsrError: if the download fails or is not a PDF.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / f"fsr_{f.file_id}.pdf"
    if target.exists() and target.read_bytes()[:4] == b"%PDF":
        return target
    for attempt in range(1, 4):
        subprocess.run(["curl", "-sL", "-m", str(TIMEOUT_S * 3), "-A", USER_AGENT, "-o", str(target),
                        f"{BASE}/file/download/{f.file_id}"], timeout=TIMEOUT_S * 3 + 10)
        if target.exists() and target.read_bytes()[:4] == b"%PDF":
            time.sleep(DELAY_S)
            return target
        time.sleep(DELAY_S * 3 * attempt)
    target.unlink(missing_ok=True)
    raise FsrError(f"{f.file_id}: download failed or not a PDF")


@dataclass(frozen=True)
class FsrReport:
    """Shaped like the other ingest sources, so scripts/ingest.py can store it."""

    source: str
    title: str
    text: str
    content_hash: str
    raw_path: str
    language: str
    number: str | None
    published_at: date | None


def read_report(path: Path, title: str) -> FsrReport:
    """PDF -> cleaned text.

    The layout puts tabs between words ("РИСКИ\\tБАНКОВСКОГО"), and pages end
    with running headers; both are normalised so chunks read as plain prose.

    Raises:
        FsrError: if no text can be extracted.
    """
    try:
        reader = PdfReader(path)
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception as e:  # noqa: BLE001  pypdf raises many types on a damaged file
        raise FsrError(f"{path.name}: cannot read PDF: {e}") from e
    text = "\n".join(pages).replace("\t", " ")
    text = re.sub(r"[  ]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 1000:
        raise FsrError(f"{path.name}: almost no text ({len(text)} chars), probably scanned")
    m = _YEAR.search(title)
    return FsrReport(
        source=SOURCE, title=title, text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        raw_path=str(path), language="ru", number=m.group(1) if m else None,
        published_at=date(int(m.group(1)), 12, 31) if m else None,
    )
