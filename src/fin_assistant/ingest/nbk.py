import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from fin_assistant.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "exchange_rates"
FEED_URL = "https://nationalbank.kz/rss/rates_all.xml"
ARCHIVE_URL = "https://nationalbank.kz/rss/get_rates.cfm?fdate={:%d.%m.%Y}"
FETCH_TIMEOUT_S = 30.0

_CODE_LENGTH = 3


class FeedError(Exception):
    pass


@dataclass(frozen=True)
class Rate:
    code: str
    tenge_per_unit: float
    quoted_per: int
    quoted_value: float
    on_date: date

    @property
    def metric(self) -> str:
        return f"fx_rate_{self.code.lower()}"

    @property
    def unit(self) -> str:
        return f"KZT per 1 {self.code}"


def fetch_feed(on_date: date | None = None, *, timeout_s: float = FETCH_TIMEOUT_S) -> bytes:
    url = ARCHIVE_URL.format(on_date) if on_date else FEED_URL
    try:
        response = httpx.get(url, timeout=timeout_s, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise FeedError(f"Cannot fetch {url}: {e}") from e

    log.info("nbk feed fetched %d bytes", len(response.content))
    return response.content


def parse_feed(xml: bytes) -> list[Rate]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise FeedError(f"Feed is not valid XML: {e}") from e

    root_date = (root.findtext("date") or "").strip()
    rates: list[Rate] = []
    for item in root.findall(".//item"):
        code = (item.findtext("title") or "").strip().upper()
        raw_value = (item.findtext("description") or "").strip()
        raw_quant = (item.findtext("quant") or "1").strip()
        raw_date = (item.findtext("pubDate") or root_date).strip()

        if len(code) != _CODE_LENGTH or not code.isalpha():
            continue

        try:
            value = float(raw_value)
            quant = int(raw_quant)
            on_date = datetime.strptime(raw_date, "%d.%m.%Y").date()
        except ValueError as e:
            log.warning("nbk skipping %s: %s", code or "<no code>", e)
            continue

        if quant <= 0 or value <= 0:
            log.warning("nbk skipping %s: value=%s quant=%s", code, value, quant)
            continue

        rates.append(
            Rate(
                code=code,
                tenge_per_unit=value / quant,
                quoted_per=quant,
                quoted_value=value,
                on_date=on_date,
            )
        )

    if not rates:
        raise FeedError("Feed parsed but contained no usable rates")

    log.info("nbk parsed %d rates for %s", len(rates), rates[0].on_date)
    return rates


def feed_fingerprint(rates: list[Rate]) -> str:
    payload = "|".join(
        f"{r.code}={r.quoted_value}/{r.quoted_per}@{r.on_date}" for r in sorted(rates, key=lambda r: r.code)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
