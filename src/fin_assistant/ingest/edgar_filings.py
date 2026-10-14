import html
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from fin_assistant.ingest.edgar import DATA_URL, USER_AGENT, EdgarError, normalise_cik
from fin_assistant.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "edgar_10k"
ARCHIVES_URL = "https://www.sec.gov"
TIMEOUT_S = 180.0
DELAY_S = 2.0

MIN_USABLE_CHARS = 50_000

_TAGS = re.compile(r"<[^>]+>")
_SCRIPTS = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")


class FilingError(Exception):
    pass


@dataclass(frozen=True)
class Filing:
    cik: str
    company: str
    form: str
    filed: date
    period: date | None
    accession: str
    document: str
    text: str

    @property
    def url(self) -> str:
        digits = str(int(self.cik.removeprefix("CIK")))
        return (
            f"{ARCHIVES_URL}/Archives/edgar/data/{digits}/{self.accession.replace('-', '')}/{self.document}"
        )


def _client(base: str) -> httpx.Client:
    return httpx.Client(
        base_url=base,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        timeout=TIMEOUT_S,
        follow_redirects=True,
    )


def list_filings(cik: str | int, *, form: str = "10-K", limit: int = 2) -> list[dict[str, Any]]:
    key = normalise_cik(cik)
    try:
        with _client(DATA_URL) as client:
            response = client.get(f"/submissions/{key}.json")
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as e:
        raise EdgarError(f"Cannot list filings for {key}: {e}") from e

    try:
        company = payload["name"]
        recent = payload["filings"]["recent"]
    except KeyError as e:
        raise EdgarError(f"{key}: unexpected submissions shape, missing {e}") from e

    found = []
    for i, kind in enumerate(recent["form"]):
        if kind != form:
            continue
        found.append(
            {
                "cik": key,
                "company": company,
                "form": kind,
                "accession": recent["accessionNumber"][i],
                "document": recent["primaryDocument"][i],
                "filed": recent["filingDate"][i],
                "period": recent.get("reportDate", [None] * len(recent["form"]))[i],
            }
        )
        if len(found) >= limit:
            break
    return found


def fetch_filing(meta: dict[str, Any]) -> Filing:
    digits = str(int(meta["cik"].removeprefix("CIK")))
    path = f"/Archives/edgar/data/{digits}/{meta['accession'].replace('-', '')}/{meta['document']}"

    try:
        with _client(ARCHIVES_URL) as client:
            response = client.get(path)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise FilingError(f"{meta['accession']}: cannot fetch: {e}") from e

    text = html_to_text(response.text)
    if len(text) < MIN_USABLE_CHARS:
        raise FilingError(
            f"{meta['accession']}: only {len(text)} characters — an exhibit rather than the filing?"
        )

    log.info("edgar filing %s %s -> %d chars", meta["company"][:30], meta["accession"], len(text))

    return Filing(
        cik=meta["cik"],
        company=meta["company"],
        form=meta["form"],
        filed=date.fromisoformat(meta["filed"]),
        period=date.fromisoformat(meta["period"]) if meta.get("period") else None,
        accession=meta["accession"],
        document=meta["document"],
        text=text,
    )


def html_to_text(raw: str) -> str:
    without_scripts = _SCRIPTS.sub(" ", raw)
    with_breaks = re.sub(r"(?i)</(p|div|tr|h[1-6]|li)>", "\n", without_scripts)
    text = html.unescape(_TAGS.sub(" ", with_breaks))

    text = text.replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_many(
    ciks: list[str | int], *, form: str = "10-K", per_company: int = 1
) -> tuple[list[Filing], list[str]]:
    filings: list[Filing] = []
    problems: list[str] = []

    for i, cik in enumerate(ciks):
        if i:
            time.sleep(DELAY_S)
        try:
            for meta in list_filings(cik, form=form, limit=per_company):
                time.sleep(DELAY_S)
                filings.append(fetch_filing(meta))
        except (EdgarError, FilingError) as e:
            problems.append(str(e))

    return filings, problems
