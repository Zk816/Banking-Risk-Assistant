"""Official exchange rates from the National Bank of Kazakhstan.

Unlike adilet.zan.kz, the National Bank publishes a machine-readable RSS feed
meant to be consumed programmatically, so this module fetches rather than reads
files off disk.

The feed's trap is `quant`: AMD is quoted per **10** units, JPY per 100, while
most currencies are per 1. Storing the raw number would make the dram look
twelve times more valuable than it is. Everything here is normalised to
"tenge for 1 unit" before it goes near the database.
"""

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from kzbank.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "nbk"
FEED_URL = "https://nationalbank.kz/rss/rates_all.xml"
# Same data for any past day. Its XML differs: the date is given once, in the
# root <date>, instead of a <pubDate> on every item.
ARCHIVE_URL = "https://nationalbank.kz/rss/get_rates.cfm?fdate={:%d.%m.%Y}"
# Полите: the feed is small and changes once a day. No reason to retry hard.
FETCH_TIMEOUT_S = 30.0

# Currency codes are three letters; anything else in the feed is not a rate.
_CODE_LENGTH = 3


class FeedError(Exception):
    """The feed could not be fetched or made sense of."""


@dataclass(frozen=True)
class Rate:
    """One currency, normalised to tenge per single unit."""

    code: str            # "USD"
    tenge_per_unit: float
    quoted_per: int      # what the feed said: 1, 10 or 100
    quoted_value: float  # what the feed said, before normalising
    on_date: date

    @property
    def metric(self) -> str:
        """Stable machine name, e.g. "fx_rate_usd"."""
        return f"fx_rate_{self.code.lower()}"

    @property
    def unit(self) -> str:
        return f"KZT per 1 {self.code}"


def fetch_feed(on_date: date | None = None, *, timeout_s: float = FETCH_TIMEOUT_S) -> bytes:
    """Download today's feed, or the archive feed for `on_date`.

    Raises:
        FeedError: on any network failure or non-200 response.
    """
    url = ARCHIVE_URL.format(on_date) if on_date else FEED_URL
    try:
        response = httpx.get(url, timeout=timeout_s, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise FeedError(f"Cannot fetch {url}: {e}") from e

    log.info("nbk feed fetched %d bytes", len(response.content))
    return response.content


def parse_feed(xml: bytes) -> list[Rate]:
    """Parse the feed into normalised rates.

    A single malformed item is skipped with a warning; a malformed document is an
    error. One bad currency should not lose the other 47.

    Raises:
        FeedError: if the XML will not parse or contains no usable rates.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise FeedError(f"Feed is not valid XML: {e}") from e

    root_date = (root.findtext("date") or "").strip()      # archive feed only
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
                # The normalisation the whole module exists for.
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
    """Content hash for the documents row, so re-running is idempotent.

    Built from the values, not the raw bytes: the feed's XML formatting changes
    between requests even when the rates have not.
    """
    payload = "|".join(
        f"{r.code}={r.quoted_value}/{r.quoted_per}@{r.on_date}"
        for r in sorted(rates, key=lambda r: r.code)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
