import html
import re
import time
from dataclasses import dataclass
from pathlib import Path

from fin_assistant.ingest.nbk_stats import BASE, DELAY_S, _get, download_bytes

SOURCE = "nbk_publications"

SECTIONS: dict[str, str] = {
    "obzor-inflyacii": "Inflation reviews (quarterly)",
    "ekonomicheskoe-obozrenie": "Economic reviews",
    "godovoy-otchet-nbk": "National Bank annual reports",
    "analitika-metodologiya": "Analytics and methodology notes",
    "razyasneniya-i-rekomendacii": "Clarifications and recommendations",
    "banking-sector-loans-to-economy-analytics": "Bank loans to the economy: analytics",
    "kredity-bankovskogo-sektora-ekonomike": "Bank loans to the economy: data",
    "statisticheskiy-byulleten": "Statistical bulletin",
    "inflyacionnye-tendencii": "Inflation trends",
    "macrosurvey": "Macroeconomic survey",
    "loans-to-economy-from-second-tier-banks": "Bank loans by industry, region, consumer, mortgage, SME, with rates",
    "group-performance": "Banking conglomerates and insurance groups: key figures and ratios",
    "organizations-performance": "Mortgage and agro-lending organisations: balance sheets and ratios",
    "pension-system-performance": "Pension fund (ENPF): savings, payouts, investment portfolio",
}

_FILE = re.compile(r'href="/file/download/(\d+)"[^>]*>\s*([^<]{3,250})')
_MAGIC = {b"%PDF": "pdf", b"PK\x03\x04": "zipx", b"\xd0\xcf\x11\xe0": "ole"}


@dataclass(frozen=True)
class PubFile:
    section: str
    file_id: str
    title: str


def list_section(slug: str) -> list[PubFile]:
    index = f"{BASE}/ru/news/{slug}"
    first = _get(index)
    rubrics = sorted(set(re.findall(rf"{re.escape(slug)}/rubrics/(\d+)", first)))
    out: dict[str, PubFile] = {}
    for page in [first] + [None] * len(rubrics):
        if page is None:
            page = _get(f"{index}/rubrics/{rubrics.pop(0)}")
            time.sleep(DELAY_S)
        for file_id, title in _FILE.findall(page):
            title = html.unescape(title).strip()
            if title:
                out.setdefault(file_id, PubFile(slug, file_id, title))
    return list(out.values())


def extension(head: bytes, title: str) -> str:
    kind = next((v for k, v in _MAGIC.items() if head.startswith(k)), "bin")
    if kind == "zipx":
        return "docx" if re.search(r"\b(docx?|word)\b", title, re.IGNORECASE) else "xlsx"
    if kind == "ole":
        return "xls"
    return kind


def download(f: PubFile, raw_dir: Path) -> Path:
    folder = raw_dir / f.section
    existing = list(folder.glob(f"{f.file_id}.*"))
    if existing:
        return existing[0]
    folder.mkdir(parents=True, exist_ok=True)
    data = download_bytes(f.file_id)
    target = folder / f"{f.file_id}.{extension(data[:4], f.title)}"
    target.write_bytes(data)
    (folder / "titles.tsv").open("a", encoding="utf-8").write(f"{target.name}\t{f.title}\n")
    return target
