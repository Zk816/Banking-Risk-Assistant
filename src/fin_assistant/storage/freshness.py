from dataclasses import dataclass
from datetime import date, datetime, timezone

from fin_assistant.logging_setup import get_logger
from fin_assistant.storage.db import fetch_all

log = get_logger(__name__)

SourceKind = str

SOURCE_POLICY: dict[str, tuple[SourceKind, int]] = {
    "exchange_rates": ("series", 2),
    "nbk_regulations": ("reference", 90),
    "laws": ("reference", 365),
    "us_bank_filings": ("series", 120),
    "bank_balance_sheets": ("series", 120),
    "bank_statistics": ("series", 75),
    "base_rate": ("series", 90),
    "stability_reports": ("reference", 400),
    "company_reports": ("series", 120),
    "country_indicators": ("series", 550),
    "worldbank_reports": ("reference", 365),
    "bank_ratings": ("series", 60),
    "deposit_market": ("series", 120),
    "basel_standards": ("reference", 365),
    "web_official": ("reference", 365),
    "nbk_publications": ("reference", 180),
}

ENTITY_ONLY_SOURCES = frozenset({"dfo"})
DEFAULT_POLICY: tuple[SourceKind, int] = ("series", 30)

_FRESHNESS_SQL = """
SELECT source,
       max(published_at) AS latest_published,
       max(retrieved_at) AS latest_retrieved,
       count(*)          AS documents
FROM documents
GROUP BY source
ORDER BY source
"""


@dataclass(frozen=True)
class SourceFreshness:
    source: str
    latest_published: date | None
    latest_retrieved: datetime | None
    documents: int
    kind: SourceKind
    max_age_days: int

    @property
    def age_days(self) -> int | None:
        if self.kind == "reference":
            if self.latest_retrieved is None:
                return None
            return (utcnow() - self.latest_retrieved).days
        if self.latest_published is None:
            return None
        return (date.today() - self.latest_published).days

    @property
    def stale(self) -> bool:
        age = self.age_days
        return age is None or age > self.max_age_days

    @property
    def detail(self) -> str:
        if self.documents == 0:
            return "нет данных"
        if self.kind == "reference":
            return (
                f"проверено {self.latest_retrieved:%d.%m.%Y}, "
                f"{self.age_days} дн. назад, порог {self.max_age_days}"
            )
        if self.latest_published is None:
            return "нет даты публикации у документов источника"
        return (
            f"данные на {self.latest_published:%d.%m.%Y}, "
            f"{self.age_days} дн. назад, порог {self.max_age_days}"
        )


def check_sources() -> list[SourceFreshness]:
    rows = {r["source"]: r for r in fetch_all(_FRESHNESS_SQL)}

    results: list[SourceFreshness] = []
    for source in sorted((set(rows) | set(SOURCE_POLICY)) - ENTITY_ONLY_SOURCES):
        row = rows.get(source)
        kind, max_age = SOURCE_POLICY.get(source, DEFAULT_POLICY)
        results.append(
            SourceFreshness(
                source=source,
                latest_published=row["latest_published"] if row else None,
                latest_retrieved=row["latest_retrieved"] if row else None,
                documents=row["documents"] if row else 0,
                kind=kind,
                max_age_days=max_age,
            )
        )

    for result in results:
        if result.stale:
            log.warning("source %s is stale: %s", result.source, result.detail)
    return results


def age_of(period: date) -> int:
    return (date.today() - period).days


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
