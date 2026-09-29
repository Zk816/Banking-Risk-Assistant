"""Organisations from the Financial Reporting Depository (opi.dfo.kz).

Why this source and not adilet: dfo.kz serves its organisation search as plain
server-rendered HTML over a GET form, and publishes no robots.txt at all — there
is no stated restriction to work around. adilet.zan.kz states the opposite
explicitly, in robots.txt and in its own page source, so it stays a manual
export.

What this gets us is the `entities` table, empty since Step 0: real BINs for
real Kazakh banks. A BIN is the project's identity key — names appear in
Russian, Kazakh, English and superseded forms, and cannot be relied on.

Report *contents* are a separate problem: they load from
`/ru/report-json/{id}/get-reports`, which wants a POST with an anti-forgery
token. Not attempted here.
"""

import re
import time
from dataclasses import dataclass
import httpx
from bs4 import BeautifulSoup

from kzbank.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "dfo"
BASE_URL = "https://opi.dfo.kz"
SEARCH_PATH = "/ru/opi/list"

# Identify ourselves honestly rather than impersonating a browser. A site that
# wants to refuse us should be able to.
USER_AGENT = "kzbank-research/0.1 (+educational RAG project; contact via repo)"

FETCH_TIMEOUT_S = 45.0
# One request every two seconds. The search is a database query on their side,
# and there is no hurry — this runs once and the result is cached in our tables.
DELAY_S = 2.0

_ID_RE = re.compile(r"/ru/opi/list/(\d+)/view")

# A result reads: "{status} {availability} БИН: {bin} {name} , {address}".
# The name sits between the BIN and the address, and the address always starts
# after a standalone comma with a country.
_RESULT_RE = re.compile(
    r"БИН:\s*(?P<bin>\d{12})\s*(?P<name>.+?)\s*,\s*(?P<address>Казахстан|Россия|[A-ZА-Я][^,]*,)",
    re.DOTALL,
)
_BIN_ONLY_RE = re.compile(r"БИН:\s*(\d{12})\s*(?P<rest>.*)", re.DOTALL)

# "Дочерняя компания АО «Банк X» Акционерное общество «Y»" — the listed entity
# is Y, not X. Taking the first quoted name would file a leasing subsidiary
# under its parent bank's name.
_SUBSIDIARY_PREFIX = re.compile(r"^Дочерняя\s+(?:компания|организация)\s+", re.IGNORECASE)


class DfoError(Exception):
    """The depository could not be reached or its page could not be parsed."""


@dataclass(frozen=True)
class Organisation:
    """One entity as the depository lists it."""

    bin: str
    name_ru: str
    object_id: str
    status: str | None = None

    @property
    def is_bank(self) -> bool:
        """Crude, and deliberately so — the listing has no legal-form field.

        Used only to report what a search found, never to decide what to store.
        """
        return "банк" in self.name_ru.lower() or "bank" in self.name_ru.lower()


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ru"},
        timeout=FETCH_TIMEOUT_S,
        follow_redirects=True,
    )


def search(query: str, *, by: str = "flNameRu") -> list[Organisation]:
    """Search the depository by name (`flNameRu`) or BIN (`flBin`).

    Raises:
        DfoError: on a network failure, a non-200 response, or unparseable HTML.
        ValueError: if `by` is not a supported filter.
    """
    if by not in {"flNameRu", "flBin"}:
        raise ValueError(f"unsupported filter {by!r}; use flNameRu or flBin")

    try:
        with _client() as client:
            response = client.get(SEARCH_PATH, params={by: query})
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise DfoError(f"Cannot reach {BASE_URL}{SEARCH_PATH}: {e}") from e

    found = parse_listing(response.text)
    log.info("dfo search %s=%r -> %d organisations", by, query, len(found))
    return found


def parse_listing(html: str) -> list[Organisation]:
    """Parse a results page into organisations.

    Each result is a link to `/ru/opi/list/{id}/view` followed by the
    organisation's details. The page has no table — results are divs — so this
    walks the links and reads the block each one heads.

    Raises:
        DfoError: if the page has no recognisable structure at all.
    """
    soup = BeautifulSoup(html, "lxml")
    links = soup.select('a[href*="/ru/opi/list/"]')
    if not links:
        if "opi" not in html:
            raise DfoError("Page does not look like a depository listing")
        return []

    organisations: list[Organisation] = []
    seen: set[str] = set()

    for link in links:
        id_match = _ID_RE.search(link.get("href", ""))
        if not id_match:
            continue

        # There are no per-result cards: the whole listing is one flat div whose
        # direct children are the results, and each link's own text carries the
        # status, BIN, name and address. Walking up to a parent finds the
        # container holding all fifteen.
        text = " ".join(link.get_text(" ", strip=True).split())

        parsed = _parse_result(text)
        if parsed is None or parsed[0] in seen:
            continue
        seen.add(parsed[0])
        bin_, name = parsed

        organisations.append(
            Organisation(
                bin=bin_, name_ru=name,
                object_id=id_match.group(1), status=_extract_status(text),
            )
        )

    return organisations


def _parse_result(text: str) -> tuple[str, str] | None:
    """Pull (BIN, name) out of one result's text, or None if it has no BIN."""
    match = _RESULT_RE.search(text)
    if match:
        return match.group("bin"), _clean_name(match.group("name"))

    # No recognisable address; keep the BIN and whatever name we can see.
    fallback = _BIN_ONLY_RE.search(text)
    if not fallback:
        return None
    return fallback.group(1), _clean_name(fallback.group("rest")[:160])


def _clean_name(raw: str) -> str:
    """Normalise a listed name, resolving the subsidiary case.

    "Дочерняя компания АО «Банк ЦентрКредит» Акционерное общество «BCC Leasing»"
    names two organisations; the one being listed is the second.
    """
    name = " ".join(raw.split())

    # A foreign address does not start with a country, so the name regex keeps
    # running into it: "ЕВРОПЕЙСКИЙ ИНВЕСТИЦИОННЫЙ БАНК , ,,,98-100, boulevard".
    # The run of commas is the depository's empty address fields; cut there.
    name = re.split(r",\s*,|,{2,}", name)[0]
    name = name.strip(" ,;")
    if _SUBSIDIARY_PREFIX.match(name):
        quoted = re.findall(r"[«\"\'']([^»\"\'']{3,90})[»\"\'']", name)
        if len(quoted) >= 2:
            return quoted[-1].strip()
    return name[:200]


def _extract_status(text: str) -> str | None:
    for status in ("Активный", "Ликвидирован", "Приостановлен"):
        if status in text:
            return status
    return None


def search_many(queries: list[str]) -> list[Organisation]:
    """Run several searches politely, de-duplicating by BIN.

    Sleeps DELAY_S between requests. Worth stating plainly: this is one request
    per query against someone else's database, and the results are cached in our
    own tables afterwards.
    """
    by_bin: dict[str, Organisation] = {}
    for i, query in enumerate(queries):
        if i:
            time.sleep(DELAY_S)
        for organisation in search(query):
            by_bin.setdefault(organisation.bin, organisation)
    return sorted(by_bin.values(), key=lambda o: o.name_ru)
