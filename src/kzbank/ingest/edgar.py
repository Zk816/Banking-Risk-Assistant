"""Company financials from SEC EDGAR.

Why this source. The project needs figures attached to an entity, a period and a
unit; Kazakhstan does not publish them where a program can reach them — ARDFM is
a JavaScript shell, and opi.dfo.kz serves an empty template (verified: 379 cells,
five organisations, every year since 2021, all zero). EDGAR publishes exactly
this shape, officially, for programmatic use.

What comes back is XBRL: every figure already carries its tag, unit, period and
the filing it came from. That is the `metrics` table's schema, arriving ready-made
— no unit normalisation guesswork like the NBK feed's `quant`.

SEC asks for one thing in return: a User-Agent naming who you are and how to
reach you. Their fair-access policy caps requests at 10/second; we stay far
under it.
"""

import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from kzbank.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "edgar"
DATA_URL = "https://data.sec.gov"

# SEC's fair-access policy requires a descriptive User-Agent with contact details
# and refuses anonymous traffic. This is a condition of use, not a workaround.
USER_AGENT = "kzbank-research/0.1 (educational RAG project; kairatzhaidar816@gmail.com)"
TIMEOUT_S = 60.0
# Their limit is 10 requests/second. One every two seconds is nowhere near it.
DELAY_S = 2.0

# A company's full fact set is several megabytes, so only the concepts a banking
# question actually needs are stored. XBRL tag -> what to call it.
BANK_CONCEPTS: dict[str, str] = {
    "Assets": "assets",
    "Liabilities": "liabilities",
    "StockholdersEquity": "equity",
    "Deposits": "deposits",
    "NetIncomeLoss": "net_income",
    "InterestAndDividendIncomeOperating": "interest_income",
    "LoansAndLeasesReceivableNetReportedAmount": "loans_net",
    "FinancingReceivableAllowanceForCreditLosses": "credit_loss_allowance",
    "InterestExpense": "interest_expense",
    "Revenues": "revenue",

    # Capital adequacy. These are the direct US analogue of Kazakhstan's k1/k1-2:
    # the ratio a bank actually holds, and the minimum it must hold. Having both
    # in one table is what makes "does the bank meet the requirement?" answerable
    # — the question the README opens with and the project could never answer.
    "TierOneRiskBasedCapitalToRiskWeightedAssets": "tier1_ratio",
    "TierOneRiskBasedCapitalRequiredForCapitalAdequacyToRiskWeightedAssets": "tier1_required",
    "TierOneLeverageCapitalToAverageAssets": "leverage_ratio",
    "CapitalToRiskWeightedAssets": "total_capital_ratio",
    "CapitalRequiredForCapitalAdequacyToRiskWeightedAssets": "total_capital_required",

    # Not every bank tags these; extract_facts skips a concept a filer omits.
    "FinancingReceivableRecordedInvestmentNonaccrualStatus": "nonaccrual_loans",
    "FinancingReceivableRecordedInvestment90DaysPastDueStillAccruing": "past_due_90",
}

# 10-K is the annual report, 10-Q quarterly. Anything else (8-K, S-1) is a
# different kind of document and its figures are not comparable in a series.
PERIODIC_FORMS = frozenset({"10-K", "10-Q"})


class EdgarError(Exception):
    """EDGAR could not be reached or returned something unusable."""


@dataclass(frozen=True)
class CompanyFact:
    """One reported figure, with everything needed to cite it."""

    cik: str
    company: str
    concept: str          # our name, e.g. "assets"
    xbrl_tag: str         # the original tag, kept so a figure can be traced
    value: float
    unit: str             # "USD", "shares"
    period_end: date
    fiscal_period: str    # "Q2", "FY"
    fiscal_year: int
    form: str             # "10-Q"
    filed: date
    accession: str        # the filing's id, which is the provenance


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=DATA_URL,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        timeout=TIMEOUT_S,
        follow_redirects=True,
    )


def normalise_cik(cik: str | int) -> str:
    """EDGAR wants a ten-digit zero-padded CIK: 19617 -> 'CIK0000019617'."""
    digits = str(cik).upper().removeprefix("CIK").lstrip("0") or "0"
    return f"CIK{int(digits):010d}"


def fetch_company_facts(cik: str | int) -> dict[str, Any]:
    """Every XBRL fact a company has ever reported.

    Several megabytes per company — JPMorgan's holds 918 concepts.

    Raises:
        EdgarError: on a network failure or a non-200 response.
    """
    key = normalise_cik(cik)
    try:
        with _client() as client:
            response = client.get(f"/api/xbrl/companyfacts/{key}.json")
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as e:
        raise EdgarError(f"Cannot fetch facts for {key}: {e}") from e
    except ValueError as e:
        raise EdgarError(f"{key}: response was not JSON") from e

    log.info("edgar facts %s concepts=%d", key,
             len(payload.get("facts", {}).get("us-gaap", {})))
    return payload


def extract_facts(
    payload: dict[str, Any],
    *,
    concepts: dict[str, str] | None = None,
    since: int = 2020,
) -> list[CompanyFact]:
    """Pull the concepts we care about out of a company's fact set.

    Only periodic filings are kept, and only the most recently filed value for
    each (concept, period): a figure is commonly restated in a later filing, and
    storing both would put two different numbers under one period.

    Raises:
        EdgarError: if the payload has no recognisable fact structure.
    """
    wanted = concepts or BANK_CONCEPTS
    try:
        company = payload["entityName"]
        gaap = payload["facts"]["us-gaap"]
        cik = normalise_cik(payload["cik"])
    except KeyError as e:
        raise EdgarError(f"Unexpected payload shape, missing {e}") from e

    # (concept, period_end) -> the fact from the latest filing seen so far.
    best: dict[tuple[str, date], CompanyFact] = {}

    for tag, name in wanted.items():
        concept = gaap.get(tag)
        if concept is None:
            continue
        for unit, entries in concept.get("units", {}).items():
            for entry in entries:
                fact = _to_fact(entry, cik=cik, company=company, tag=tag,
                                name=name, unit=unit, since=since)
                if fact is None:
                    continue
                key = (fact.concept, fact.period_end)
                previous = best.get(key)
                if previous is None or fact.filed > previous.filed:
                    best[key] = fact

    facts = sorted(best.values(), key=lambda f: (f.concept, f.period_end))
    log.info("edgar extracted %d facts for %s", len(facts), company)
    return facts


def _to_fact(
    entry: dict[str, Any], *, cik: str, company: str, tag: str,
    name: str, unit: str, since: int,
) -> CompanyFact | None:
    """One XBRL entry as a CompanyFact, or None if it is not usable."""
    form = entry.get("form", "")
    if form not in PERIODIC_FORMS:
        return None

    fiscal_year = entry.get("fy")
    if not isinstance(fiscal_year, int) or fiscal_year < since:
        return None

    try:
        period_end = date.fromisoformat(entry["end"])
        filed = date.fromisoformat(entry["filed"])
        value = float(entry["val"])
    except (KeyError, ValueError, TypeError):
        return None

    return CompanyFact(
        cik=cik, company=company, concept=name, xbrl_tag=tag,
        value=value, unit=unit, period_end=period_end,
        fiscal_period=entry.get("fp", "?"), fiscal_year=fiscal_year,
        form=form, filed=filed, accession=entry.get("accn", ""),
    )


def fetch_many(ciks: list[str | int], *, since: int = 2020) -> list[CompanyFact]:
    """Fetch several companies politely, sleeping between requests."""
    facts: list[CompanyFact] = []
    for i, cik in enumerate(ciks):
        if i:
            time.sleep(DELAY_S)
        facts.extend(extract_facts(fetch_company_facts(cik), since=since))
    return facts
