from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fin_assistant.storage.db import fetch_all

KZ_NORMS: dict[str, dict[str, Any]] = {
    "k1": {
        "name": "достаточность основного капитала",
        "us_concept": "CET1 ratio",
        "us_metric": None,
        "kz_min": 0.055,
    },
    "k1-2": {
        "name": "достаточность капитала первого уровня",
        "us_concept": "Tier 1 ratio",
        "us_metric": ("tier1_ratio", "tier1_required"),
        "kz_min": 0.065,
    },
    "k2": {
        "name": "достаточность собственного капитала",
        "us_concept": "Total capital ratio",
        "us_metric": ("total_capital_ratio", "total_capital_required"),
        "kz_min": 0.080,
    },
}

KZ_NORMS_SOURCE = (
    "нормативные значения пруденциальных нормативов АРРФР/НБ РК "
    "(внесены как факты базы знаний, не извлечены из корпуса)"
)

NPL_WATCH = 0.03

FLOW_METRICS = frozenset({"net_income", "interest_income", "interest_expense", "revenue"})

EXTRA_POLICY = {"edgar_annual": ("series", 455)}


@dataclass
class Inference:
    rule: str
    conclusion: str
    facts: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


def _latest(bin_: str, metrics: list[str], period: date | None = None) -> dict[str, dict[str, Any]]:
    rows = fetch_all(
        """SELECT DISTINCT ON (m.metric) m.metric, m.value, m.unit, m.period, d.title AS form
           FROM metrics m LEFT JOIN documents d ON d.id = m.source_doc
           WHERE m.bin = %s AND m.metric = ANY(%s) AND (%s::date IS NULL OR m.period = %s::date)
           ORDER BY m.metric, m.period DESC""",
        (bin_, metrics, period, period),
    )
    return {r["metric"]: r for r in rows}


def source_of(companies: list[str]) -> str:
    rows = fetch_all(
        "SELECT DISTINCT type FROM entities WHERE name_en = ANY(%s) OR name_ru = ANY(%s)",
        (companies, companies),
    )
    return (
        "bank_statistics"
        if rows and all(r["type"] in ("bank_balance_sheets", "nbk_bank") for r in rows)
        else "us_bank_filings"
    )


NBK_METRIC = {
    "assets": "nbk_assets",
    "deposits": "nbk_deposits",
    "loans_gross": "nbk_loans",
    "loans_net": "nbk_loans",
    "equity": "nbk_equity",
    "liabilities": "nbk_liabilities",
    "net_income": "nbk_net_income_ytd",
    "net_income_ytd": "nbk_net_income_ytd",
    "credit_loss_allowance": "nbk_provisions",
    "overdue_loans": "nbk_npl90",
}

NBK_LABELS = {
    "nbk_assets": "активы",
    "nbk_deposits": "вклады клиентов",
    "nbk_loans": "ссудный портфель",
    "nbk_equity": "собственный капитал",
    "nbk_liabilities": "обязательства",
    "nbk_net_income_ytd": "чистая прибыль с начала года",
    "nbk_provisions": "провизии",
    "nbk_npl90": "кредиты с просрочкой свыше 90 дней",
}


def is_kz_bank(company: str) -> bool:
    rows = fetch_all("SELECT type FROM entities WHERE name_ru = %s OR name_en = %s", (company, company))
    return bool(rows) and rows[0]["type"] in ("bank_balance_sheets", "nbk_bank")


def bin_of(company: str) -> str | None:
    rows = fetch_all("SELECT bin FROM entities WHERE name_en = %s OR name_ru = %s", (company, company))
    return rows[0]["bin"] if rows else None


TREND_WORSE_PP = 0.01


def kz_banks() -> list[str]:
    return [
        r["name_ru"]
        for r in fetch_all(
            """SELECT DISTINCT e.name_ru FROM entities e JOIN metrics m USING (bin)
           WHERE e.type IN ('bank_balance_sheets', 'nbk_bank') AND m.metric = 'nbk_npl90'
             AND m.period >= (SELECT max(period) FROM metrics WHERE metric = 'nbk_npl90') - 365
           ORDER BY 1"""
        )
    ]
