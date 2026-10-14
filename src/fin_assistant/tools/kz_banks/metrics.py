from datetime import date, timedelta
from typing import Any

from fin_assistant.storage.db import fetch_all

from .banks import _bank_table, _pick, _unknown_bank, _values, resolve_bank
from .formatting import METRICS, MINIMUMS, SOURCE, _format, _num, fmt_kzt


def get_bank_metrics(bank: str, metrics: list[str], on_date: str | None = None) -> dict[str, Any]:
    hit = resolve_bank(bank)
    if not hit:
        return _unknown_bank(bank)
    bin_, name = hit
    out = []
    for metric in metrics:
        if metric not in METRICS:
            out.append(
                {
                    "metric": metric,
                    "found": False,
                    "reason": f"Неизвестный показатель; доступны: {', '.join(METRICS)}",
                }
            )
            continue
        picked = _pick(_values(bin_, metric), on_date)
        if not picked:
            out.append({"metric": metric, "found": False, "reason": "нет данных за этот период"})
            continue
        p, v = picked
        row = {"metric": metric, "meaning": METRICS[metric][2], "value": _format(metric, v), "as_of": str(p)}
        if metric in MINIMUMS:
            row["regulatory_minimum"] = _format(metric, MINIMUMS[metric])
            row["meets_minimum"] = v >= MINIMUMS[metric]
        out.append(row)
    return {"found": any(r.get("found", True) for r in out), "bank": name, "values": out, "source": SOURCE}


def compare_banks(banks: list[str], metric: str) -> dict[str, Any]:
    if metric not in METRICS:
        return {"found": False, "reason": f"Неизвестный показатель; доступны: {', '.join(METRICS)}"}
    series, missing = {}, []
    for b in banks:
        hit = resolve_bank(b)
        if not hit:
            missing.append(b)
            continue
        series[hit[1]] = _values(hit[0], metric)
    if not series:
        return {"found": False, "reason": "Ни один банк не найден", "not_found": missing}
    common = set.intersection(*(set(v) for v in series.values() if v)) if all(series.values()) else set()
    if not common:
        return {"found": False, "reason": "Нет общего периода, за который отчитались все банки"}
    p = max(common)
    rows = sorted(
        ({"bank": n, "value": _format(metric, v[p]), "_raw": v[p]} for n, v in series.items()),
        key=lambda r: -r["_raw"],
    )
    gap = None
    if len(rows) >= 2:
        d = rows[0]["_raw"] - rows[1]["_raw"]
        gap = (
            f"{_num(d * 100)} п.п."
            if METRICS[metric][1] in ("pct", "ratio_pct")
            else fmt_kzt(d)
            if METRICS[metric][1] == "kzt"
            else _num(d)
        )
    for r in rows:
        r.pop("_raw")
    return {
        "found": True,
        "metric": metric,
        "meaning": METRICS[metric][2],
        "as_of": str(p),
        "ranking_high_to_low": rows,
        "gap_first_vs_second": gap,
        "not_found": missing,
        "source": SOURCE,
    }


def rank_banks(metric: str, highest_first: bool = True, top_n: int = 10) -> dict[str, Any]:
    if metric not in METRICS:
        return {"found": False, "reason": f"Неизвестный показатель; доступны: {', '.join(METRICS)}"}
    _, names = _bank_table()
    latest: dict[str, tuple[date, float]] = {}
    for bin_, name in names.items():
        picked = _pick(_values(bin_, metric), None)
        if picked and picked[0] >= date.today() - timedelta(days=400):
            latest[name] = picked
    if not latest:
        return {"found": False, "reason": "нет данных"}
    p = max(d for d, _ in latest.values())
    current = sorted(
        ((n, v) for n, (d, v) in latest.items() if d == p), key=lambda x: x[1], reverse=highest_first
    )
    stale = sorted(n for n, (d, _) in latest.items() if d != p)
    return {
        "found": True,
        "metric": metric,
        "meaning": METRICS[metric][2],
        "as_of": str(p),
        "order": "по убыванию" if highest_first else "по возрастанию",
        "banks_ranked": len(current),
        "ranking": [
            {"rank": i + 1, "bank": n, "value": _format(metric, v)}
            for i, (n, v) in enumerate(current[:top_n])
        ],
        "not_reported_this_month": stale,
        "source": SOURCE,
    }


def metric_history(bank: str, metric: str, years: int = 3) -> dict[str, Any]:
    hit = resolve_bank(bank)
    if not hit:
        return _unknown_bank(bank)
    if metric not in METRICS:
        return {"found": False, "reason": f"Неизвестный показатель; доступны: {', '.join(METRICS)}"}
    values = _values(hit[0], metric)
    if not values:
        return {"found": False, "reason": "нет данных"}
    last = max(values)
    points = sorted({p for p in values if p.month == 12 and p.year >= last.year - years} | {last})
    out = {
        "found": True,
        "bank": hit[1],
        "metric": metric,
        "meaning": METRICS[metric][2],
        "history": [{"as_of": str(p), "value": _format(metric, values[p])} for p in points],
        "source": SOURCE,
    }
    year_ago = (
        date(last.year - 1, last.month, last.day)
        if not (last.month == 2 and last.day == 29)
        else date(last.year - 1, 2, 28)
    )
    if year_ago in values:
        diff = values[last] - values[year_ago]
        change = (
            f"{'+' if diff >= 0 else '−'}{_num(abs(diff) * 100)} п.п."
            if METRICS[metric][1] in ("pct", "ratio_pct")
            else f"{'+' if diff >= 0 else '−'}{fmt_kzt(abs(diff))}"
            if METRICS[metric][1] == "kzt"
            else _num(diff)
        )
        out["change_12m"] = {
            "from": str(year_ago),
            "from_value": _format(metric, values[year_ago]),
            "to": str(last),
            "to_value": _format(metric, values[last]),
            "change": change,
        }
    return out


def sector_history(metric: str = "npl90_share", years: int = 10) -> dict[str, Any]:
    expr = METRICS.get(metric, ("",))[0]
    if not expr or METRICS[metric][1] in ("ratio_pct", "ratio") and "/" not in expr:
        return {
            "found": False,
            "reason": "Для сектора доступны суммы и доли: npl90_share, assets, loans, deposits, npl90, provisions, net_income_ytd",
        }
    parts = expr.split("/")
    rows = fetch_all(
        """SELECT period, metric, sum(value) v FROM metrics
                         WHERE metric = ANY(%s) AND bin IS NOT NULL AND (extract(month from period) = 12
                               OR period = (SELECT max(period) FROM metrics WHERE metric = 'nbk_assets'))
                         GROUP BY period, metric""",
        (parts,),
    )
    by: dict[date, dict[str, float]] = {}
    for r in rows:
        by.setdefault(r["period"], {})[r["metric"]] = float(r["v"])
    last = max(by)
    pts = []
    for p in sorted(by):
        if p.year < last.year - years or any(x not in by[p] for x in parts):
            continue
        v = by[p][parts[0]] / by[p][parts[1]] if len(parts) == 2 else by[p][parts[0]]
        pts.append({"as_of": str(p), "value": _format(metric, v), "_raw": v})
    for prev, cur in zip(pts, pts[1:]):
        d = cur["_raw"] - prev["_raw"]
        cur["change_vs_previous"] = (
            f"{'+' if d >= 0 else '−'}{_num(abs(d) * 100)} п.п."
            if METRICS[metric][1] == "pct"
            else f"{'+' if d >= 0 else '−'}{fmt_kzt(abs(d))}"
        )
    for pt in pts:
        pt.pop("_raw")
    return {
        "found": bool(pts),
        "metric": metric,
        "meaning": METRICS[metric][2] + " — банковский сектор",
        "history": pts,
        "source": SOURCE,
    }


def get_ratings(bank: str) -> dict[str, Any]:
    hit = resolve_bank(bank)
    if not hit:
        return _unknown_bank(bank)
    rows = fetch_all(
        "SELECT agency, rating, previous, outlook, rated_on FROM ratings WHERE bin = %s ORDER BY agency",
        (hit[0],),
    )
    if not rows:
        return {"found": False, "reason": f"Нет рейтингов для {hit[1]} в данных КФГД"}
    return {
        "found": True,
        "bank": hit[1],
        "ratings": [
            {
                "agency": r["agency"],
                "rating": r["rating"],
                "previous": r["previous"],
                "outlook": r["outlook"],
                "date": str(r["rated_on"]),
            }
            for r in rows
        ],
        "source": "bank_ratings (КФГД, kdif.kz/ru/banks-rating)",
    }


def get_base_rate(on_date: str | None = None) -> dict[str, Any]:
    limit = on_date or str(date.today())
    rows = fetch_all(
        "SELECT period, value FROM metrics WHERE metric = 'base_rate' AND period <= %s ORDER BY period DESC LIMIT 2",
        (limit,),
    )
    if not rows:
        return {"found": False, "reason": "нет решений по базовой ставке до этой даты"}
    out = {
        "found": True,
        "rate": f"{_num(float(rows[0]['value']))}%",
        "decided_on": str(rows[0]["period"]),
        "source": "base_rate (Национальный Банк РК)",
    }
    if len(rows) > 1:
        d = float(rows[0]["value"]) - float(rows[1]["value"])
        out["previous"] = {"rate": f"{_num(float(rows[1]['value']))}%", "decided_on": str(rows[1]["period"])}
        out["change"] = (
            "без изменений" if d == 0 else f"{'повышена' if d > 0 else 'снижена'} на {_num(abs(d))} п.п."
        )
    return out
