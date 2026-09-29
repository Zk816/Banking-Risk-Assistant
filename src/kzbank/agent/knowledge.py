"""Knowledge representation: an ontology of concepts, facts, rules, and a graph.

Three layers, each inspectable:

  ONTOLOGY  concept -> definition, jurisdiction, and how it maps to what is
            stored. This is where "k1-2 in Kazakhstan" meets "Tier 1 ratio" in a
            US 10-Q: the two regimes measure the same thing under different names.
  RULES     functions that derive a conclusion from stored facts. The verdict is
            computed here, in code; the language model is never asked to compare
            two numbers.
  GRAPH     the same knowledge as nodes and edges (bank -reports-> metric
            -maps_to-> concept -constrained_by-> rule), built from the database,
            for explanation and visualisation.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import networkx as nx

from kzbank.storage.db import fetch_all
from kzbank.storage.freshness import SOURCE_POLICY
from kzbank.tools.company_metrics import CONCEPTS, _humanise

# --- Ontology ---------------------------------------------------------------
# Kazakh prudential norms. Minimums are the regulator's base values without
# buffers. They are entered as facts, NOT retrieved: the NBK acts in our corpus
# are reporting and accounting rules and do not contain these numbers.
KZ_NORMS: dict[str, dict[str, Any]] = {
    "k1": {"name": "достаточность основного капитала", "us_concept": "CET1 ratio",
           "us_metric": None, "kz_min": 0.055},
    "k1-2": {"name": "достаточность капитала первого уровня", "us_concept": "Tier 1 ratio",
             "us_metric": ("tier1_ratio", "tier1_required"), "kz_min": 0.065},
    "k2": {"name": "достаточность собственного капитала", "us_concept": "Total capital ratio",
           "us_metric": ("total_capital_ratio", "total_capital_required"), "kz_min": 0.080},
}
KZ_NORMS_SOURCE = ("нормативные значения пруденциальных нормативов АРРФР/НБ РК "
                   "(внесены как факты базы знаний, не извлечены из корпуса)")

# Problem-loan share above which a bank is flagged. 3% is a common supervisory
# watch level for non-performing loans; it is a threshold for ranking, not law.
NPL_WATCH = 0.03

# Flow metrics accumulate over a period. A 10-Q reports them year-to-date, so a
# June value is six months and a December value is twelve: comparing across
# forms compares different lengths of time. These are compared on 10-K only.
FLOW_METRICS = frozenset({"net_income", "interest_income", "interest_expense", "revenue"})
# Annual figures arrive once a year plus a filing lag, so they age on that clock.
EXTRA_POLICY = {"edgar_annual": ("series", 455)}


@dataclass
class Inference:
    """One rule firing: what it concluded, from which facts."""

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
    """Freshness policy key for a set of banks: 'nbk_stats' for Kazakh banks, else 'edgar'."""
    rows = fetch_all("SELECT DISTINCT type FROM entities WHERE name_en = ANY(%s) OR name_ru = ANY(%s)",
                     (companies, companies))
    return "nbk_stats" if rows and all(r["type"] in ("kase", "nbk_bank") for r in rows) else "edgar"


# Metrics asked about in plain words -> the National Bank's figure, which exists
# for every Kazakh bank. The form 700-Н figures cover only the 13 listed banks.
NBK_METRIC = {"assets": "nbk_assets", "deposits": "nbk_deposits", "loans_gross": "nbk_loans",
              "loans_net": "nbk_loans", "equity": "nbk_equity", "liabilities": "nbk_liabilities",
              "net_income": "nbk_net_income_ytd", "net_income_ytd": "nbk_net_income_ytd",
              "credit_loss_allowance": "nbk_provisions", "overdue_loans": "nbk_npl90"}
NBK_LABELS = {"nbk_assets": "активы", "nbk_deposits": "вклады клиентов", "nbk_loans": "ссудный портфель",
              "nbk_equity": "собственный капитал", "nbk_liabilities": "обязательства",
              "nbk_net_income_ytd": "чистая прибыль с начала года", "nbk_provisions": "провизии",
              "nbk_npl90": "кредиты с просрочкой свыше 90 дней"}


def is_kz_bank(company: str) -> bool:
    rows = fetch_all("SELECT type FROM entities WHERE name_ru = %s OR name_en = %s", (company, company))
    return bool(rows) and rows[0]["type"] in ("kase", "nbk_bank")


def bin_of(company: str) -> str | None:
    rows = fetch_all("SELECT bin FROM entities WHERE name_en = %s OR name_ru = %s", (company, company))
    return rows[0]["bin"] if rows else None


# --- Rules ------------------------------------------------------------------
def rule_concept_mapping(norm: str) -> Inference:
    """R1. Map a Kazakh norm to what US filings report, or say it is not reported."""
    spec = KZ_NORMS[norm]
    if spec["us_metric"] is None:
        return Inference(
            "R1 concept mapping",
            f"{norm} ({spec['name']}) = {spec['us_concept']} в US-отчётности. В данных SEC его нет; "
            f"ближайший раскрываемый показатель: k1-2 = Tier 1 ratio (включает основной капитал).",
            [f"{norm} maps_to {spec['us_concept']}", f"{spec['us_concept']} not_in database"],
            {"substitute": "k1-2"},
        )
    return Inference("R1 concept mapping",
                     f"{norm} ({spec['name']}) соответствует {spec['us_concept']}",
                     [f"{norm} maps_to {spec['us_concept']}"], {"substitute": norm})


def rule_capital_adequacy(company: str, norm: str) -> Inference:
    """R2. Does the bank meet the norm, under US rules and under the Kazakh minimum?"""
    spec = KZ_NORMS[norm]
    actual_key, required_key = spec["us_metric"]
    bin_ = bin_of(company)
    vals = _latest(bin_, [actual_key]) if bin_ else {}
    if actual_key not in vals:
        return Inference("R2 capital adequacy", f"{company}: {spec['us_concept']} не раскрыт — "
                         "вывод невозможно сделать", [f"{company} lacks {actual_key}"])
    period = vals[actual_key]["period"]
    same = _latest(bin_, [actual_key, required_key], period)   # both from one filing
    actual = float(same[actual_key]["value"])
    us_req = float(same[required_key]["value"]) if required_key in same else None
    kz_min = spec["kz_min"]
    meets_kz = actual >= kz_min
    parts = [f"{company}: {spec['us_concept']} = {actual:.2%} на {period}"]
    if us_req is not None:
        parts.append(f"требование регулятора США {us_req:.2%} → "
                     f"{'СОБЛЮДАЕТСЯ' if actual >= us_req else 'НАРУШЕНО'}")
    parts.append(f"минимум РК для {norm} {kz_min:.1%} → {'СОБЛЮДАЛСЯ БЫ' if meets_kz else 'НАРУШЕН'}"
                 f" (запас {actual - kz_min:+.2%})")
    return Inference(
        "R2 capital adequacy", "; ".join(parts),
        [f"{company} reports {actual_key}={actual:.4f} ({same[actual_key]['form']})",
         f"{norm} kz_min={kz_min}", *( [f"{company} {required_key}={us_req:.4f}"] if us_req else [])],
        {"actual": actual, "us_required": us_req, "kz_min": kz_min, "period": period.isoformat(),
         "source": same[actual_key]["form"]},
    )


def rule_common_period(companies: list[str], metric: str) -> Inference:
    """R3. Compare companies only on the latest period ALL of them reported.

    Without this rule the system compared JPMorgan's 2026 profit with Citigroup's
    2020 profit: each company's "latest" row is a different quarter.
    """
    bins = {c: bin_of(c) for c in companies}
    annual_only = metric in FLOW_METRICS
    rows = fetch_all(
        """SELECT coalesce(e.name_en, e.name_ru) AS name_en, m.period, m.value, m.unit
           FROM metrics m JOIN entities e USING (bin)
           LEFT JOIN documents d ON d.id = m.source_doc
           WHERE m.bin = ANY(%s) AND m.metric = %s AND (NOT %s OR d.edition = '10-K')""",
        ([b for b in bins.values() if b], metric, annual_only),
    )
    periods: dict[str, set[date]] = {c: set() for c in companies}
    values: dict[tuple[str, date], tuple[float, str]] = {}
    for r in rows:
        periods[r["name_en"]].add(r["period"])
        values[(r["name_en"], r["period"])] = (float(r["value"]), r["unit"])
    common = set.intersection(*periods.values()) if all(periods.values()) else set()
    if not common:
        return Inference("R3 common period", f"Нет периода, за который '{metric}' есть у всех: "
                         "сравнение невозможно", [f"{c}: {len(p)} periods" for c, p in periods.items()])
    period = max(common)
    ranked = sorted(companies, key=lambda c: values[(c, period)][0], reverse=True)
    lines = [f"{c}: {_humanise(*values[(c, period)])}" for c in ranked]
    return Inference(
        "R3 common period",
        f"{CONCEPTS.get(metric) or NBK_LABELS.get(metric, metric)} на {period} (последний общий период): "
        + "; ".join(lines),
        [f"common periods: {len(common)}, latest {period}",
         *(["flow metric: annual 10-K figures only"] if annual_only else []),
         *(f"{c} latest own period {max(p)}" for c, p in periods.items())],
        {"period": period.isoformat(), "ranking": ranked, "annual": annual_only,
         "values": {c: values[(c, period)][0] for c in companies}},
    )


def rule_freshness(source: str, latest: date, today: date) -> Inference:
    """R4. Flag data older than its source's publishing cadence allows."""
    kind, max_age = {**SOURCE_POLICY, **EXTRA_POLICY}.get(source, ("series", 30))
    age = (today - latest).days
    stale = age > max_age
    return Inference("R4 freshness",
                     f"данные {source} от {latest}: {age} дн. при допустимых {max_age} → "
                     f"{'УСТАРЕЛИ' if stale else 'актуальны'}",
                     [f"{source} policy {kind}/{max_age}d"], {"stale": stale, "age_days": age})


def _overdue_share(bin_: str, period: date | None = None) -> tuple[float, date, str] | None:
    """(share, period, source) of problem loans in the customer portfolio.

    Kazakh banks (form 700-Н): overdue principal / gross customer loans.
    US banks (10-Q): non-accrual loans / (net loans + allowance). Different
    definitions of "problem loan", so the two are never ranked together.
    """
    npl = _latest(bin_, ["nbk_npl90"], period)
    if "nbk_npl90" in npl:
        on = npl["nbk_npl90"]["period"]
        loans = _latest(bin_, ["nbk_loans"], on)            # same month as the NPL figure
        if "nbk_loans" in loans and float(loans["nbk_loans"]["value"]):
            return (float(npl["nbk_npl90"]["value"]) / float(loans["nbk_loans"]["value"]), on,
                    "NPL 90+ / ссудный портфель (НБ РК)")
    v = _latest(bin_, ["overdue_loans", "loans_gross"], period)
    if {"overdue_loans", "loans_gross"} <= v.keys() and float(v["loans_gross"]["value"]):
        return (float(v["overdue_loans"]["value"]) / float(v["loans_gross"]["value"]),
                v["loans_gross"]["period"], "overdue/gross (700-Н)")
    v = _latest(bin_, ["nonaccrual_loans", "loans_net", "credit_loss_allowance"], period)
    if {"nonaccrual_loans", "loans_net"} <= v.keys():
        gross = float(v["loans_net"]["value"]) + float(v.get("credit_loss_allowance", {"value": 0})["value"])
        if gross:
            return float(v["nonaccrual_loans"]["value"]) / gross, v["loans_net"]["period"], "nonaccrual (10-Q)"
    return None


def rule_npl_risk(company: str) -> Inference:
    """R5. Share of problem loans in the customer portfolio, against a watch level."""
    bin_ = bin_of(company)
    got = _overdue_share(bin_) if bin_ else None
    if got is None:
        return Inference("R5 NPL risk", f"{company}: нет данных о проблемных кредитах", [])
    share, period, how = got
    level = "ВЫСОКИЙ" if share > NPL_WATCH else "низкий"
    return Inference("R5 NPL risk",
                     f"{company}: проблемные кредиты {share:.2%} портфеля на {period} → риск {level} "
                     f"(порог {NPL_WATCH:.0%})",
                     [f"{how}, {period}"], {"npl_share": share, "high": share > NPL_WATCH,
                                            "period": period.isoformat()})


# Change in the problem-loan share over a year that counts as deterioration.
TREND_WORSE_PP = 0.01


def rule_trend(company: str) -> Inference:
    """R7. Is loan quality getting worse? Problem-loan share now vs a year earlier."""
    bin_ = bin_of(company)
    now = _overdue_share(bin_) if bin_ else None
    if now is None:
        return Inference("R7 trend", f"{company}: нет данных для тренда", [])
    share, period, how = now
    # A year back, within one quarter: with a gap in the filings, the nearest
    # older point can be three years away, and that is not "a year ago".
    target = date(period.year - 1, period.month, min(period.day, 28))
    loan_metric = "nbk_npl90" if "НБ РК" in how else "loans_gross"
    year_ago = fetch_all("SELECT max(period) AS p FROM metrics WHERE bin = %s AND metric = %s "
                         "AND period BETWEEN %s AND %s",
                         (bin_, loan_metric, date.fromordinal(target.toordinal() - 92), target))
    before = _overdue_share(bin_, year_ago[0]["p"]) if year_ago and year_ago[0]["p"] else None
    if before is None:
        return Inference("R7 trend", f"{company}: нет данных годом ранее", [])
    delta = share - before[0]
    verdict = "УХУДШАЕТСЯ" if delta > TREND_WORSE_PP else "улучшается" if delta < -TREND_WORSE_PP else "стабильно"
    return Inference("R7 trend",
                     f"{company}: проблемные кредиты {before[0]:.2%} ({before[1]}) → {share:.2%} ({period}), "
                     f"{delta * 100:+.2f} п.п. за год → {verdict}",
                     [how, f"threshold ±{TREND_WORSE_PP * 100:.0f} п.п."],
                     {"delta": delta, "worse": delta > TREND_WORSE_PP, "now": share, "before": before[0]})


def kz_banks() -> list[str]:
    """Kazakh banks with an NPL figure in the last year of data, by name as stored.

    Banks that stopped reporting long ago (failed, merged) are left out: they
    would otherwise fill every ranking with years-old numbers.
    """
    return [r["name_ru"] for r in fetch_all(
        """SELECT DISTINCT e.name_ru FROM entities e JOIN metrics m USING (bin)
           WHERE e.type IN ('kase', 'nbk_bank') AND m.metric = 'nbk_npl90'
             AND m.period >= (SELECT max(period) FROM metrics WHERE metric = 'nbk_npl90') - 365
           ORDER BY 1""")]


def rule_kz_capital(company: str, norm: str) -> Inference:
    """R2 for Kazakh banks: the reported k1 / k1-2 / k2 against the Kazakh minimum."""
    spec = KZ_NORMS[norm]
    metric = norm.replace("-", "_")
    bin_ = bin_of(company)
    v = _latest(bin_, [metric]) if bin_ else {}
    if metric not in v:
        return Inference("R2 capital adequacy", f"{company}: норматив {norm} не найден в данных НБ РК", [])
    actual, period = float(v[metric]["value"]), v[metric]["period"]
    ok = actual >= spec["kz_min"]
    return Inference(
        "R2 capital adequacy",
        f"{company}: {norm} ({spec['name']}) = {actual:.1%} на {period}; минимум {spec['kz_min']:.1%} → "
        f"{'СОБЛЮДАЕТСЯ' if ok else 'НАРУШЕН'} (запас {actual - spec['kz_min']:+.1%})",
        [f"{company} {metric}={actual:.3f} (НБ РК, {period})", f"{norm} kz_min={spec['kz_min']}"],
        {"actual": actual, "kz_min": spec["kz_min"], "period": period.isoformat(),
         "source": v[metric]["form"]},
    )


# --- Graph ------------------------------------------------------------------
def build_graph(companies: list[str]) -> nx.DiGraph:
    """Knowledge graph around a few banks: what they report, what it means, which rule reads it."""
    g = nx.DiGraph()
    for norm, spec in KZ_NORMS.items():
        g.add_node(norm, kind="kz_norm", label=f"{norm}\n≥{spec['kz_min']:.1%}")
        g.add_node(spec["us_concept"], kind="us_concept")
        g.add_edge(norm, spec["us_concept"], rel="equivalent_to")
        g.add_edge(norm, "R2 capital adequacy", rel="checked_by")
    for rule in ("R2 capital adequacy", "R3 common period", "R5 NPL risk", "R7 trend"):
        g.add_node(rule, kind="rule")
    metric_concept = {"tier1_ratio": "Tier 1 ratio", "total_capital_ratio": "Total capital ratio"}
    for c in companies:
        b = bin_of(c)
        if not b:
            continue
        g.add_node(c, kind="bank")
        for r in fetch_all("SELECT DISTINCT metric FROM metrics WHERE bin = %s", (b,)):
            m = r["metric"]
            if m in metric_concept:
                g.add_edge(c, metric_concept[m], rel="reports")
            elif m in ("nonaccrual_loans", "overdue_loans"):
                g.add_node(m, kind="metric")
                g.add_edge(c, m, rel="reports")
                g.add_edge(m, "R5 NPL risk", rel="input_to")
                g.add_edge(m, "R7 trend", rel="input_to")
            elif m in ("assets", "deposits"):
                g.add_node(m, kind="metric")
                g.add_edge(c, m, rel="reports")
                g.add_edge(m, "R3 common period", rel="compared_by")
    return g
