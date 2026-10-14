import html
import re
from dataclasses import dataclass
from datetime import date

from fin_assistant.ingest.nbk_stats import _get

SOURCE = "base_rate"
INDEX = "https://nationalbank.kz/ru/news/grafik-prinyatiya-resheniy-po-bazovoy-stavke"

_RUBRIC = re.compile(r"grafik-prinyatiya-resheniy-po-bazovoy-stavke/rubrics/(\d+)")
_ROW = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s*\|[\s|]*(\d{1,2}(?:[.,]\d{1,2})?)\s*\|")


@dataclass(frozen=True)
class RateDecision:
    on_date: date
    rate: float


def rubric_ids() -> list[str]:
    return sorted(set(_RUBRIC.findall(_get(INDEX))))


def parse_decisions(page_html: str) -> list[RateDecision]:
    start, end = page_html.find("<table"), page_html.find("</table>")
    if start < 0 or end < 0:
        return []
    text = html.unescape(re.sub(r"<[^>]+>", " | ", page_html[start:end]))
    text = re.sub(r"\s+", " ", text)
    out = []
    for d, m, y, rate in _ROW.findall(text):
        out.append(RateDecision(date(int(y), int(m), int(d)), float(rate.replace(",", "."))))
    return out
