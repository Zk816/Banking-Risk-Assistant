import json
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from fin_assistant.ingest.kase import _DOC_JSON, BASE, DELAY_S, KaseError, _client
from fin_assistant.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "company_reports"

KINDS: dict[str, re.Pattern[str]] = {
    "audit": re.compile(r"аудиторск\w* отчет", re.IGNORECASE),
    "review": re.compile(r"отчет по обзору", re.IGNORECASE),
    "fs": re.compile(r"^финансовая отчетность за", re.IGNORECASE),
    "rating": re.compile(r"рейтинг", re.IGNORECASE),
}


@dataclass(frozen=True)
class KaseDoc:
    code: str
    kind: str
    name: str
    url: str
    published: date


def list_documents(code: str, since: date) -> list[KaseDoc]:
    try:
        with _client() as c:
            page = c.get(f"{BASE}/ru/issuers/{code}/")
            page.raise_for_status()
    except Exception as e:
        raise KaseError(f"issuer page {code}: {e}") from e
    docs: dict[str, KaseDoc] = {}
    for m in _DOC_JSON.finditer(page.text):
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        link, name = d.get("link") or "", d.get("name") or ""
        if not link.lower().endswith(".pdf") or d.get("language") not in (None, "ru"):
            continue
        stamp = d.get("date0") or d.get("date1")
        if not stamp:
            continue
        published = date.fromisoformat(stamp[:10])
        kind = next((k for k, p in KINDS.items() if p.search(name)), None)
        if kind and published >= since:
            docs.setdefault(link, KaseDoc(code, kind, name.strip(), BASE + link, published))
    return sorted(docs.values(), key=lambda x: x.published)


def download(doc: KaseDoc, raw_dir: Path) -> Path:
    folder = raw_dir / doc.code
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{doc.kind}_{doc.published}_{doc.url.rsplit('/', 1)[-1]}"
    if target.exists():
        return target
    try:
        with _client() as c:
            r = c.get(doc.url)
            r.raise_for_status()
    except Exception as e:
        raise KaseError(f"{doc.url}: {e}") from e
    if not r.content.startswith(b"%PDF"):
        raise KaseError(f"{doc.url}: not a PDF")
    target.write_bytes(r.content)
    time.sleep(DELAY_S)
    return target


@dataclass(frozen=True)
class Issuer:
    code: str
    name: str
    is_financial: bool


_ISSUER_JSON = re.compile(r'\{"code":"[A-Z0-9_]{2,10}".*?\}(?=,\{"code"|\])')


def list_issuers() -> list[Issuer]:
    try:
        with _client() as c:
            page = c.get(f"{BASE}/ru/issuers/")
            page.raise_for_status()
    except Exception as e:
        raise KaseError(f"issuers page: {e}") from e
    out: dict[str, Issuer] = {}
    for m in _ISSUER_JSON.finditer(page.text):
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if d["code"].endswith("_KZ"):
            continue
        out[d["code"]] = Issuer(d["code"], d.get("short_name_ru") or d["code"], bool(d.get("is_financial")))
    return sorted(out.values(), key=lambda i: i.code)
