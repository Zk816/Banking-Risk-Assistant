"""How old is our data, and is that too old?

The project rule is explicit: if data for a period is not published, the system
says so rather than quietly serving an older figure. Right now nothing enforces
that. `get_exchange_rate("USD")` with no date returns the most recent row it has
— and if that row is three months old, the caller gets a stale rate presented as
current, with a correct-looking date attached that nobody reads.

Staleness is a property of the source, not of the row: exchange rates are
published every business day, laws change every few years. So each source gets
its own tolerance.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone

from kzbank.logging_setup import get_logger
from kzbank.storage.db import fetch_all

log = get_logger(__name__)

# Two kinds of source, and they age differently.
#
#   "series"    — a new data point every period. The question is whether the
#                 newest *data point* is recent, so age is measured from
#                 published_at. Exchange rates.
#
#   "reference" — a document that stands until amended. The 1995 banking law is
#                 not stale for being from 1995; it is stale if we have not
#                 checked for a newer edition in a long time. Age is measured
#                 from retrieved_at.
#
# Measuring a law by published_at is the obvious mistake, and it reports a
# perfectly current act as 11 347 days out of date.
SourceKind = str  # "series" | "reference"

SOURCE_POLICY: dict[str, tuple[SourceKind, int]] = {
    # NBK publishes every business day; two days covers a weekend, three would
    # hide a Monday outage.
    "nbk": ("series", 2),
    # Re-check the legal corpus quarterly. Both are reference sources: a 2009
    # resolution is not stale for being from 2009 — it is stale if we have not
    # looked for amendments in months.
    # "adilet" is not listed: its only document was a synthetic test fixture,
    # removed from the corpus. A configured source with no rows reads as an
    # outage, and there is no Adilet ingest to be out of date.
    "nbk_npa": ("reference", 90),
    # Quarterly filings, and a 10-Q lands weeks after the quarter it covers.
    # 120 days allows one full quarter plus the filing lag before we call the
    # series stale.
    "edgar": ("series", 120),
    # KASE form 700-Н: quarterly, published about two weeks after quarter end.
    # Same clock as a 10-Q.
    "kase": ("series", 120),
    # NBK per-bank prudential ratios: monthly, published about a month after
    # the reporting date.
    "nbk_stats": ("series", 75),
    # Base rate: about eight scheduled decisions a year, so the newest can be
    # up to ~2 months old without anything being wrong.
    "nbk_policy": ("series", 90),
}

# Sources that fill `entities` but publish no documents. Freshness here is read
# from `documents`, so listing dfo above made it permanently "нет данных" — the
# registry is not stale, it simply is not that kind of source.
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
    """Age of one source's newest record."""

    source: str
    latest_published: date | None
    latest_retrieved: datetime | None
    documents: int
    kind: SourceKind
    max_age_days: int

    @property
    def age_days(self) -> int | None:
        """Days since whichever date actually measures this source's freshness."""
        if self.kind == "reference":
            if self.latest_retrieved is None:
                return None
            return (utcnow() - self.latest_retrieved).days
        if self.latest_published is None:
            return None
        return (date.today() - self.latest_published).days

    @property
    def stale(self) -> bool:
        """True when the newest record is older than this source tolerates.

        A source with no data at all counts as stale: an empty table cannot
        answer anything, and reporting it as fresh would be worse than useless.
        """
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
    """Freshness of every source that has at least one document.

    A source configured in MAX_AGE_DAYS but absent from the database is included
    with zero documents — missing entirely is the staleness that matters most.
    """
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
    """Days between `period` and today. Negative for a future date."""
    return (date.today() - period).days


def utcnow() -> datetime:
    """Timezone-aware now, so comparisons against `retrieved_at` are valid."""
    return datetime.now(timezone.utc)
