import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SOURCE = "worldbank_reports"
API = "https://search.worldbank.org/api/v2/wds"
DELAY_S = 1.5
TIMEOUT_S = 120

QUERIES = (
    "Kazakhstan financial sector assessment",
    "Kazakhstan banking sector",
    "Kazakhstan economic update",
    "Kazakhstan financial stability",
    "Kazakhstan credit risk non-performing loans",
    "Kazakhstan country economic memorandum",
)
SKIP_TYPES = re.compile(
    r"procurement|environmental|safeguard|agreement|audit|resettlement|disclosure|"
    r"implementation status|isr|letter|minutes|memorandum & recommendation",
    re.I,
)
OFF_TOPIC = re.compile(
    r"road|transport|electricity|transmission|health|justice|forestry|agricultur|farm|youth|"
    r"education|energy efficiency|innovation project|tax administration|statistical master|"
    r"migration|opinion survey|market implementation|announcement",
    re.I,
)
MAX_PER_QUERY = 60


class WbDocsError(Exception):
    pass


@dataclass(frozen=True)
class WbDoc:
    doc_id: str
    title: str
    doc_type: str
    date: str
    pdf_url: str


def search(query: str) -> list[WbDoc]:
    url = (
        f"{API}?format=json&rows={MAX_PER_QUERY}&lang_exact=English&count_exact=Kazakhstan"
        f"&fl=display_title,docdt,pdfurl,docty&sort=docdt&order=desc&qterm={query.replace(' ', '%20')}"
    )
    r = subprocess.run(
        ["curl", "-s", "-m", str(TIMEOUT_S), url], capture_output=True, text=True, timeout=TIMEOUT_S + 10
    )
    try:
        docs = json.loads(r.stdout).get("documents", {})
    except json.JSONDecodeError as e:
        raise WbDocsError(f"{query}: not JSON") from e
    out = []
    for key, d in docs.items():
        if not isinstance(d, dict) or not d.get("pdfurl"):
            continue
        doc_type = d.get("docty") or ""
        if SKIP_TYPES.search(doc_type):
            continue
        title = re.sub(r"\s+", " ", d.get("display_title") or "").strip()
        if OFF_TOPIC.search(title) or OFF_TOPIC.search(doc_type):
            continue
        out.append(
            WbDoc(
                key, title, doc_type, (d.get("docdt") or "")[:10], d["pdfurl"].replace("http://", "https://")
            )
        )
    return out


def download(doc: WbDoc, raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / f"{doc.date}_{doc.doc_id}.pdf"
    if target.exists():
        return target
    r = subprocess.run(
        ["curl", "-sL", "-m", str(TIMEOUT_S), doc.pdf_url], capture_output=True, timeout=TIMEOUT_S + 10
    )
    if r.stdout[:4] != b"%PDF" or b"%%EOF" not in r.stdout[-2048:]:
        raise WbDocsError(f"{doc.pdf_url}: not a complete PDF")
    target.write_bytes(r.stdout)
    with (raw_dir / "titles.tsv").open("a", encoding="utf-8") as f:
        f.write(f"{target.name}\t{doc.doc_type}\t{doc.title}\n")
    time.sleep(DELAY_S)
    return target
