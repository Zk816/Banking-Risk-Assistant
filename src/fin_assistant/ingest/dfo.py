import re
import time
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from fin_assistant.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "dfo"
BASE_URL = "https://opi.dfo.kz"
SEARCH_PATH = "/ru/opi/list"

USER_AGENT = "fin-assistant-research/0.1 (+educational RAG project; contact via repo)"

FETCH_TIMEOUT_S = 45.0
DELAY_S = 2.0

_ID_RE = re.compile(r"/ru/opi/list/(\d+)/view")

_RESULT_RE = re.compile(
    r"БИН:\s*(?P<bin>\d{12})\s*(?P<name>.+?)\s*,\s*(?P<address>Казахстан|Россия|[A-ZА-Я][^,]*,)",
    re.DOTALL,
)
_BIN_ONLY_RE = re.compile(r"БИН:\s*(\d{12})\s*(?P<rest>.*)", re.DOTALL)

_SUBSIDIARY_PREFIX = re.compile(r"^Дочерняя\s+(?:компания|организация)\s+", re.IGNORECASE)


class DfoError(Exception):
    pass


@dataclass(frozen=True)
class Organisation:
    bin: str
    name_ru: str
    object_id: str
    status: str | None = None

    @property
    def is_bank(self) -> bool:
        return "банк" in self.name_ru.lower() or "bank" in self.name_ru.lower()


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ru"},
        timeout=FETCH_TIMEOUT_S,
        follow_redirects=True,
    )


def search(query: str, *, by: str = "flNameRu") -> list[Organisation]:
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

        text = " ".join(link.get_text(" ", strip=True).split())

        parsed = _parse_result(text)
        if parsed is None or parsed[0] in seen:
            continue
        seen.add(parsed[0])
        bin_, name = parsed

        organisations.append(
            Organisation(
                bin=bin_,
                name_ru=name,
                object_id=id_match.group(1),
                status=_extract_status(text),
            )
        )

    return organisations


def _parse_result(text: str) -> tuple[str, str] | None:
    match = _RESULT_RE.search(text)
    if match:
        return match.group("bin"), _clean_name(match.group("name"))

    fallback = _BIN_ONLY_RE.search(text)
    if not fallback:
        return None
    return fallback.group(1), _clean_name(fallback.group("rest")[:160])


def _clean_name(raw: str) -> str:
    name = " ".join(raw.split())

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
    by_bin: dict[str, Organisation] = {}
    for i, query in enumerate(queries):
        if i:
            time.sleep(DELAY_S)
        for organisation in search(query):
            by_bin.setdefault(organisation.bin, organisation)
    return sorted(by_bin.values(), key=lambda o: o.name_ru)
