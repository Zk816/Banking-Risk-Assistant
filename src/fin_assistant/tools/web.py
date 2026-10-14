import ipaddress
import re
import socket
from datetime import date
from typing import Any
from urllib.parse import urlparse

import httpx
import pymupdf
from bs4 import BeautifulSoup

from fin_assistant.config import settings
from fin_assistant.logging_setup import get_logger
from fin_assistant.storage.db import connect

log = get_logger(__name__)

TIMEOUT_S = 20.0
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 12000
MAX_RESULTS = 8
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) research-assistant"

OFFICIAL = re.compile(
    r"(\.gov\.kz|adilet\.zan\.kz|nationalbank\.kz|kgd\.gov\.kz|aifc\.kz|afsa\.aifc\.kz|"
    r"court\.aifc\.kz|kase\.kz|finreg\.kz|oecd\.org|bis\.org|worldbank\.org|imf\.org)$",
    re.I,
)


class WebError(Exception):
    pass


def web_search(query: str, max_results: int = MAX_RESULTS) -> dict[str, Any]:
    try:
        r = httpx.get(
            f"{settings.searxng_url}/search",
            timeout=TIMEOUT_S,
            params={"q": query, "format": "json", "language": "ru"},
        )
        r.raise_for_status()
        results = r.json().get("results", [])
    except (httpx.HTTPError, ValueError) as e:
        return {"found": False, "reason": f"Веб-поиск недоступен: {type(e).__name__}"}
    seen, rows = set(), []
    for x in results:
        url = x.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        host = urlparse(url).hostname or ""
        rows.append(
            {
                "title": (x.get("title") or "")[:200],
                "url": url,
                "snippet": (x.get("content") or "")[:400],
                "published": x.get("publishedDate"),
                "official": bool(OFFICIAL.search(host)),
            }
        )
    rows.sort(key=lambda r: not r["official"])
    if not rows:
        return {"found": False, "reason": "Ничего не найдено в интернете по этому запросу."}
    return {
        "found": True,
        "query": query,
        "searched_on": str(date.today()),
        "results": rows[:max_results],
        "source": "web (результаты поиска в интернете; перед ответом прочитай страницу через fetch_page)",
    }


def _public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def _remember(url: str, host: str, title: str) -> None:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO web_pages (url, domain, title, official) VALUES (%s, %s, %s, %s)
                           ON CONFLICT (url) DO UPDATE SET times_used = web_pages.times_used + 1, last_seen = now()""",
                (url, host, title, bool(OFFICIAL.search(host))),
            )
    except Exception:
        log.warning("could not record web page %s", url, exc_info=True)


def fetch_page(url: str, max_chars: int = MAX_TEXT_CHARS, remember: bool = True) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or not _public_host(parsed.hostname):
        return {"found": False, "reason": "Адрес недоступен: разрешены только публичные http(s)-страницы."}
    try:
        with httpx.Client(
            timeout=TIMEOUT_S, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        ) as client:
            with client.stream("GET", url) as r:
                r.raise_for_status()
                if r.url.host and not _public_host(r.url.host):
                    return {"found": False, "reason": "Переадресация на непубличный адрес."}
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_PAGE_BYTES:
                        break
                content_type = r.headers.get("content-type", "")
    except httpx.HTTPError as e:
        return {"found": False, "reason": f"Страница не открылась: {type(e).__name__}"}
    if body[:4] == b"%PDF" or "pdf" in content_type:
        try:
            with pymupdf.open(stream=body, filetype="pdf") as doc:
                text = "\n".join(page.get_text() for page in doc)
        except Exception:
            return {"found": False, "reason": "PDF не читается."}
        title = url.rsplit("/", 1)[-1]
    else:
        soup = BeautifulSoup(body, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
            tag.decompose()
        title = (soup.title.string or "").strip() if soup.title and soup.title.string else url
        whole = (soup.body or soup).get_text("\n")
        block = soup.find("article") or soup.find("main")
        text = block.get_text("\n") if block and len(block.get_text(strip=True)) >= 500 else whole
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t ]+", " ", text)).strip()
    if len(text) < 200:
        return {
            "found": False,
            "reason": "На странице нет текста (вероятно, она собирается скриптом в браузере).",
        }
    host = parsed.hostname or ""
    if remember:
        _remember(url, host, title[:200])
    return {
        "found": True,
        "url": url,
        "title": title[:200],
        "official": bool(OFFICIAL.search(host)),
        "fetched_on": str(date.today()),
        "truncated": len(text) > max_chars,
        "text": text[:max_chars],
        "source": f"web: {host}",
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Поиск в интернете. ТОЛЬКО если в наших источниках (search_documents и др.) ответа нет или тема вне их: "
            "налоги (Налоговый кодекс, КПН/ИПН у источника), СИДН/международные договоры, МФЦА/AIFC, правила листинга KASE, "
            "законы о рынке ценных бумаг, ТОО, свежие новости. Возвращает ссылки; потом читай 1-3 лучшие через fetch_page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "поисковый запрос; конкретно, с годом при необходимости",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": "Текст одной страницы или PDF из результатов web_search. Официальные источники (official=true) предпочтительнее.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        },
    },
]
TOOL_REGISTRY: dict[str, Any] = {"web_search": web_search, "fetch_page": fetch_page}
