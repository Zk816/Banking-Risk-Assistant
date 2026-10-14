import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from fin_assistant.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "us_bank_filings"
DATA_URL = "https://data.sec.gov"

USER_AGENT = "fin-assistant-research/0.1 (educational RAG project; kairatzhaidar816@gmail.com)"
TIMEOUT_S = 60.0
DELAY_S = 2.0

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
    "TierOneRiskBasedCapitalToRiskWeightedAssets": "tier1_ratio",
    "TierOneRiskBasedCapitalRequiredForCapitalAdequacyToRiskWeightedAssets": "tier1_required",
    "TierOneLeverageCapitalToAverageAssets": "leverage_ratio",
    "CapitalToRiskWeightedAssets": "total_capital_ratio",
    "CapitalRequiredForCapitalAdequacyToRiskWeightedAssets": "total_capital_required",
    "FinancingReceivableRecordedInvestmentNonaccrualStatus": "nonaccrual_loans",
    "FinancingReceivableRecordedInvestment90DaysPastDueStillAccruing": "past_due_90",
}

PERIODIC_FORMS = frozenset({"10-K", "10-Q"})


class EdgarError(Exception):
    pass


@dataclass(frozen=True)
class CompanyFact:
    cik: str
    company: str
    concept: str
    xbrl_tag: str
    value: float
    unit: str
    period_end: date
    fiscal_period: str
    fiscal_year: int
    form: str
    filed: date
    accession: str


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=DATA_URL,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        timeout=TIMEOUT_S,
        follow_redirects=True,
    )


def normalise_cik(cik: str | int) -> str:
    digits = str(cik).upper().removeprefix("CIK").lstrip("0") or "0"
    return f"CIK{int(digits):010d}"


def fetch_company_facts(cik: str | int) -> dict[str, Any]:
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

    log.info("edgar facts %s concepts=%d", key, len(payload.get("facts", {}).get("us-gaap", {})))
    return payload


def extract_facts(
    payload: dict[str, Any],
    *,
    concepts: dict[str, str] | None = None,
    since: int = 2020,
) -> list[CompanyFact]:
    wanted = concepts or BANK_CONCEPTS
    try:
        company = payload["entityName"]
        gaap = payload["facts"]["us-gaap"]
        cik = normalise_cik(payload["cik"])
    except KeyError as e:
        raise EdgarError(f"Unexpected payload shape, missing {e}") from e

    best: dict[tuple[str, date], CompanyFact] = {}

    for tag, name in wanted.items():
        concept = gaap.get(tag)
        if concept is None:
            continue
        for unit, entries in concept.get("units", {}).items():
            for entry in entries:
                fact = _to_fact(entry, cik=cik, company=company, tag=tag, name=name, unit=unit, since=since)
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
    entry: dict[str, Any],
    *,
    cik: str,
    company: str,
    tag: str,
    name: str,
    unit: str,
    since: int,
) -> CompanyFact | None:
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
        cik=cik,
        company=company,
        concept=name,
        xbrl_tag=tag,
        value=value,
        unit=unit,
        period_end=period_end,
        fiscal_period=entry.get("fp", "?"),
        fiscal_year=fiscal_year,
        form=form,
        filed=filed,
        accession=entry.get("accn", ""),
    )


def fetch_many(ciks: list[str | int], *, since: int = 2020) -> list[CompanyFact]:
    facts: list[CompanyFact] = []
    for i, cik in enumerate(ciks):
        if i:
            time.sleep(DELAY_S)
        facts.extend(extract_facts(fetch_company_facts(cik), since=since))
    return facts
