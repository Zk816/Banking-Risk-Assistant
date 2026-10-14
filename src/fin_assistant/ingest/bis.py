import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SOURCE = "basel_standards"
BASE = "https://www.bis.org"
DELAY_S = 2.0
TIMEOUT_S = 120

PUBLICATIONS: dict[str, str] = {
    "d424": "Basel III: finalising post-crisis reforms (credit risk, output floor)",
    "bcbs189": "Basel III: global regulatory framework for more resilient banks (capital definition, buffers)",
    "bcbs238": "Basel III: liquidity coverage ratio (LCR)",
    "d295": "Basel III: net stable funding ratio (NSFR)",
    "bcbs270": "Basel III leverage ratio framework",
    "d403": "Prudential treatment of problem assets: non-performing exposures and forbearance",
    "d401": "Regulatory treatment of accounting provisions (IFRS 9 expected credit loss)",
    "d368": "Interest rate risk in the banking book (IRRBB)",
    "d283": "Supervisory framework for measuring and controlling large exposures",
    "d400": "Pillar 3 disclosure requirements",
    "d573": "Core Principles for effective banking supervision (2024)",
    "bcbs239": "Principles for effective risk data aggregation and risk reporting",
    "d457": "Minimum capital requirements for market risk",
    "bcbs128": "Basel II: international convergence of capital measurement",
    "d532": "Principles for the effective management and supervision of climate-related financial risks",
    "d515": "Principles for operational resilience",
}

_PDF_LINK = re.compile(r'href="([^"]+\.pdf)"')


class BisError(Exception):
    pass


@dataclass(frozen=True)
class Publication:
    code: str
    title: str
    pdf_url: str


def _curl(url: str, out: Path | None = None) -> str:
    cmd = ["curl", "-sL", "-m", str(TIMEOUT_S), "-A", "fin-assistant research", url]
    if out:
        cmd += ["-o", str(out)]
    for attempt in range(1, 4):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S + 10)
        if r.returncode == 0:
            return r.stdout
        time.sleep(DELAY_S * 3 * attempt)
    raise BisError(f"{url}: curl exit {r.returncode}")


def find_pdf(code: str) -> Publication:
    links: list[str] = []
    for page_url in (f"{BASE}/bcbs/publ/{code}.htm", f"{BASE}/publ/{code}.htm"):
        page = _curl(page_url)
        links = [
            l
            for l in _PDF_LINK.findall(page)
            if "summary" not in l.lower() and "/press/" not in l and "publications/" in l
        ]
        if links:
            break
    if not links:
        raise BisError(f"{code}: no PDF link on the landing page")
    link = links[0] if links[0].startswith("http") else BASE + links[0]
    return Publication(code, PUBLICATIONS[code], link)


def download(pub: Publication, raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / f"{pub.code}.pdf"
    if target.exists() and target.read_bytes()[:4] == b"%PDF":
        return target
    _curl(pub.pdf_url, target)
    if (
        not target.exists()
        or target.read_bytes()[:4] != b"%PDF"
        or b"%%EOF" not in target.read_bytes()[-2048:]
    ):
        target.unlink(missing_ok=True)
        raise BisError(f"{pub.code}: {pub.pdf_url} is not a PDF")
    time.sleep(DELAY_S)
    return target
