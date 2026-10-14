from datetime import date

from fin_assistant.storage.db import fetch_all
from fin_assistant.storage.freshness import SOURCE_POLICY
from fin_assistant.tools.company_metrics import CONCEPTS, _humanise

from .facts import (
    EXTRA_POLICY,
    FLOW_METRICS,
    KZ_NORMS,
    NBK_LABELS,
    NPL_WATCH,
    TREND_WORSE_PP,
    Inference,
    _latest,
    bin_of,
)


def rule_concept_mapping(norm: str) -> Inference:
    spec = KZ_NORMS[norm]
    if spec["us_metric"] is None:
        return Inference(
            "R1 concept mapping",
            f"{norm} ({spec['name']}) = {spec['us_concept']} в US-отчётности. В данных SEC его нет; "
            f"ближайший раскрываемый показатель: k1-2 = Tier 1 ratio (включает основной капитал).",
            [f"{norm} maps_to {spec['us_concept']}", f"{spec['us_concept']} not_in database"],
            {"substitute": "k1-2"},
        )
    return Inference(
        "R1 concept mapping",
        f"{norm} ({spec['name']}) соответствует {spec['us_concept']}",
        [f"{norm} maps_to {spec['us_concept']}"],
        {"substitute": norm},
    )


def rule_capital_adequacy(company: str, norm: str) -> Inference:
    spec = KZ_NORMS[norm]
    actual_key, required_key = spec["us_metric"]
    bin_ = bin_of(company)
    vals = _latest(bin_, [actual_key]) if bin_ else {}
    if actual_key not in vals:
        return Inference(
            "R2 capital adequacy",
            f"{company}: {spec['us_concept']} не раскрыт — вывод невозможно сделать",
            [f"{company} lacks {actual_key}"],
        )
    period = vals[actual_key]["period"]
    same = _latest(bin_, [actual_key, required_key], period)
    actual = float(same[actual_key]["value"])
    us_req = float(same[required_key]["value"]) if required_key in same else None
    kz_min = spec["kz_min"]
    meets_kz = actual >= kz_min
    parts = [f"{company}: {spec['us_concept']} = {actual:.2%} на {period}"]
    if us_req is not None:
        parts.append(
            f"требование регулятора США {us_req:.2%} → {'СОБЛЮДАЕТСЯ' if actual >= us_req else 'НАРУШЕНО'}"
        )
    parts.append(
        f"минимум РК для {norm} {kz_min:.1%} → {'СОБЛЮДАЛСЯ БЫ' if meets_kz else 'НАРУШЕН'}"
        f" (запас {actual - kz_min:+.2%})"
    )
    return Inference(
        "R2 capital adequacy",
        "; ".join(parts),
        [
            f"{company} reports {actual_key}={actual:.4f} ({same[actual_key]['form']})",
            f"{norm} kz_min={kz_min}",
            *([f"{company} {required_key}={us_req:.4f}"] if us_req else []),
        ],
        {
            "actual": actual,
            "us_required": us_req,
            "kz_min": kz_min,
            "period": period.isoformat(),
            "source": same[actual_key]["form"],
        },
    )


def rule_common_period(companies: list[str], metric: str) -> Inference:
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
        return Inference(
            "R3 common period",
            f"Нет периода, за который '{metric}' есть у всех: сравнение невозможно",
            [f"{c}: {len(p)} periods" for c, p in periods.items()],
        )
    period = max(common)
    ranked = sorted(companies, key=lambda c: values[(c, period)][0], reverse=True)
    lines = [f"{c}: {_humanise(*values[(c, period)])}" for c in ranked]
    return Inference(
        "R3 common period",
        f"{CONCEPTS.get(metric) or NBK_LABELS.get(metric, metric)} на {period} (последний общий период): "
        + "; ".join(lines),
        [
            f"common periods: {len(common)}, latest {period}",
            *(["flow metric: annual 10-K figures only"] if annual_only else []),
            *(f"{c} latest own period {max(p)}" for c, p in periods.items()),
        ],
        {
            "period": period.isoformat(),
            "ranking": ranked,
            "annual": annual_only,
            "values": {c: values[(c, period)][0] for c in companies},
        },
    )


def rule_freshness(source: str, latest: date, today: date) -> Inference:
    kind, max_age = {**SOURCE_POLICY, **EXTRA_POLICY}.get(source, ("series", 30))
    age = (today - latest).days
    stale = age > max_age
    return Inference(
        "R4 freshness",
        f"данные {source} от {latest}: {age} дн. при допустимых {max_age} → "
        f"{'УСТАРЕЛИ' if stale else 'актуальны'}",
        [f"{source} policy {kind}/{max_age}d"],
        {"stale": stale, "age_days": age},
    )


def _overdue_share(bin_: str, period: date | None = None) -> tuple[float, date, str] | None:
    npl = _latest(bin_, ["nbk_npl90"], period)
    if "nbk_npl90" in npl:
        on = npl["nbk_npl90"]["period"]
        loans = _latest(bin_, ["nbk_loans"], on)
        if "nbk_loans" in loans and float(loans["nbk_loans"]["value"]):
            return (
                float(npl["nbk_npl90"]["value"]) / float(loans["nbk_loans"]["value"]),
                on,
                "NPL 90+ / ссудный портфель (НБ РК)",
            )
    v = _latest(bin_, ["overdue_loans", "loans_gross"], period)
    if {"overdue_loans", "loans_gross"} <= v.keys() and float(v["loans_gross"]["value"]):
        return (
            float(v["overdue_loans"]["value"]) / float(v["loans_gross"]["value"]),
            v["loans_gross"]["period"],
            "overdue/gross (700-Н)",
        )
    v = _latest(bin_, ["nonaccrual_loans", "loans_net", "credit_loss_allowance"], period)
    if {"nonaccrual_loans", "loans_net"} <= v.keys():
        gross = float(v["loans_net"]["value"]) + float(v.get("credit_loss_allowance", {"value": 0})["value"])
        if gross:
            return (
                float(v["nonaccrual_loans"]["value"]) / gross,
                v["loans_net"]["period"],
                "nonaccrual (10-Q)",
            )
    return None


def rule_npl_risk(company: str) -> Inference:
    bin_ = bin_of(company)
    got = _overdue_share(bin_) if bin_ else None
    if got is None:
        return Inference("R5 NPL risk", f"{company}: нет данных о проблемных кредитах", [])
    share, period, how = got
    level = "ВЫСОКИЙ" if share > NPL_WATCH else "низкий"
    return Inference(
        "R5 NPL risk",
        f"{company}: проблемные кредиты {share:.2%} портфеля на {period} → риск {level} "
        f"(порог {NPL_WATCH:.0%})",
        [f"{how}, {period}"],
        {"npl_share": share, "high": share > NPL_WATCH, "period": period.isoformat()},
    )


def rule_trend(company: str) -> Inference:
    bin_ = bin_of(company)
    now = _overdue_share(bin_) if bin_ else None
    if now is None:
        return Inference("R7 trend", f"{company}: нет данных для тренда", [])
    share, period, how = now
    target = date(period.year - 1, period.month, min(period.day, 28))
    loan_metric = "nbk_npl90" if "НБ РК" in how else "loans_gross"
    year_ago = fetch_all(
        "SELECT max(period) AS p FROM metrics WHERE bin = %s AND metric = %s AND period BETWEEN %s AND %s",
        (bin_, loan_metric, date.fromordinal(target.toordinal() - 92), target),
    )
    before = _overdue_share(bin_, year_ago[0]["p"]) if year_ago and year_ago[0]["p"] else None
    if before is None:
        return Inference("R7 trend", f"{company}: нет данных годом ранее", [])
    delta = share - before[0]
    verdict = (
        "УХУДШАЕТСЯ" if delta > TREND_WORSE_PP else "улучшается" if delta < -TREND_WORSE_PP else "стабильно"
    )
    return Inference(
        "R7 trend",
        f"{company}: проблемные кредиты {before[0]:.2%} ({before[1]}) → {share:.2%} ({period}), "
        f"{delta * 100:+.2f} п.п. за год → {verdict}",
        [how, f"threshold ±{TREND_WORSE_PP * 100:.0f} п.п."],
        {"delta": delta, "worse": delta > TREND_WORSE_PP, "now": share, "before": before[0]},
    )


def rule_kz_capital(company: str, norm: str) -> Inference:
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
        {
            "actual": actual,
            "kz_min": spec["kz_min"],
            "period": period.isoformat(),
            "source": v[metric]["form"],
        },
    )
