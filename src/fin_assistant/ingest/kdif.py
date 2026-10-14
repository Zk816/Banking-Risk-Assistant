import html
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SOURCE = "bank_ratings"
URL = "https://kdif.kz/ru/banks-rating"
AGENCIES = ("Moody's", "S&P", "Fitch")
TIMEOUT_S = 60

_TABLE = re.compile(r"<table.*?</table>", re.S)
_ROW = re.compile(r"<tr.*?</tr>", re.S)
_CELL = re.compile(r"<t[dh].*?</t[dh]>", re.S)
_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


class KdifError(Exception):
    pass


@dataclass(frozen=True)
class Rating:
    bank: str
    agency: str
    rating: str
    previous: str | None
    outlook: str | None
    on_date: date | None


def fetch_page() -> str:
    r = subprocess.run(
        ["curl", "-sL", "-m", str(TIMEOUT_S), "-A", "fin-assistant research", URL],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S + 10,
    )
    if r.returncode != 0 or "<table" not in r.stdout:
        raise KdifError(f"{URL}: curl exit {r.returncode}")
    return r.stdout


def _text(cell: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()


def parse_ratings(page: str) -> list[Rating]:
    out: list[Rating] = []
    for agency, table in zip(AGENCIES, _TABLE.findall(page)):
        for row in _ROW.findall(table)[1:]:
            cells = [_text(c) for c in _CELL.findall(row)]
            if len(cells) < 4 or not cells[1]:
                continue
            m = re.match(r"([A-Za-z0-9+\-]+)\s*(?:\(([A-Za-z0-9+\-]+)\))?", cells[1].replace("*", ""))
            if not m:
                continue
            d = _DATE.search(cells[3])
            out.append(
                Rating(
                    cells[0],
                    agency,
                    m.group(1),
                    m.group(2),
                    cells[2] or None,
                    date(int(d.group(3)), int(d.group(2)), int(d.group(1))) if d else None,
                )
            )
    return out


DOC_SECTIONS: dict[str, str] = {
    "deposit-market-analysis": "Quarterly reviews of the household deposit market",
    "monitoring-of-the-rates-of-participating-banks": "Monthly deposit rates of every participating bank",
    "sustainability-of-the-guarantee-system": "Stability of the deposit guarantee system",
    "reporting": "KDIF annual reports and audited statements",
}
_DOC_LINK = re.compile(
    r'href="(https://kdif\.kz/ru/[a-z0-9-]+/file/(\d+))".*?<p[^>]*>\s*([^<]{5,250}?)\s*</p>', re.S
)


@dataclass(frozen=True)
class KdifDoc:
    section: str
    file_id: str
    title: str
    url: str


def list_docs(section: str) -> list[KdifDoc]:
    r = subprocess.run(
        [
            "curl",
            "-sL",
            "-m",
            str(TIMEOUT_S),
            "-A",
            "fin-assistant research",
            f"https://kdif.kz/ru/{section}",
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S + 10,
    )
    if r.returncode != 0:
        raise KdifError(f"{section}: curl exit {r.returncode}")
    docs = {fid: KdifDoc(section, fid, _text(title), url) for url, fid, title in _DOC_LINK.findall(r.stdout)}
    return list(docs.values())


def download_doc(doc: KdifDoc, raw_dir) -> Path:

    folder = Path(raw_dir) / doc.section
    existing = list(folder.glob(f"{doc.file_id}.*"))
    if existing:
        return existing[0]
    folder.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["curl", "-sL", "-m", "120", "-A", "fin-assistant research", doc.url],
        capture_output=True,
        timeout=130,
    )
    if r.returncode != 0 or not r.stdout:
        raise KdifError(f"{doc.url}: download failed")
    ext = "pdf" if r.stdout[:4] == b"%PDF" else "zip" if r.stdout[:2] == b"PK" else "bin"
    if ext == "zip":
        ext = "docx" if b"word/" in r.stdout[:3000] else "xlsx"
    target = folder / f"{doc.file_id}.{ext}"
    target.write_bytes(r.stdout)
    with (folder / "titles.tsv").open("a", encoding="utf-8") as f:
        f.write(f"{target.name}\t{doc.title}\n")
    time.sleep(1.5)
    return target
