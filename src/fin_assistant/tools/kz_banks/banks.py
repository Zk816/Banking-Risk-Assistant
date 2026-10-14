import difflib
import re
from datetime import date
from functools import lru_cache
from typing import Any

from fin_assistant.agent.perception import KZ_ALIASES, NBK_ALIASES
from fin_assistant.ingest import kase
from fin_assistant.storage.db import fetch_all

from .formatting import METRICS, SOURCE


def _series(bin_: str, db_metric: str) -> dict[date, float]:
    rows = fetch_all("SELECT period, value FROM metrics WHERE bin = %s AND metric = %s", (bin_, db_metric))
    return {r["period"]: float(r["value"]) for r in rows}


def _values(bin_: str, metric: str) -> dict[date, float]:
    expr = METRICS[metric][0]
    if "/" in expr:
        a, b = expr.split("/")
        num, den = _series(bin_, a), _series(bin_, b)
        return {p: num[p] / den[p] for p in num if den.get(p)}
    return _series(bin_, expr)


def _pick(values: dict[date, float], on: str | None) -> tuple[date, float] | None:
    if not values:
        return None
    if on:
        limit = date.fromisoformat(on[:10])
        eligible = [p for p in values if p <= limit]
        if not eligible:
            return None
        p = max(eligible)
    else:
        p = max(values)
    return p, values[p]


def _norm(s: str) -> str:
    s = re.sub(r"[«»\"'().,]", " ", s.lower())
    s = re.sub(r"\b(ао|дб|банк|bank|jsc|акционерное общество)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=1)
def _bank_table() -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    rows = fetch_all("""SELECT DISTINCT e.bin, e.name_ru FROM entities e JOIN metrics m ON m.bin = e.bin
                        WHERE e.type IN ('bank_balance_sheets', 'nbk_bank') AND m.metric = 'nbk_assets'""")
    names = {r["bin"]: r["name_ru"] for r in rows}
    by_name = {v: k for k, v in names.items()}
    aliases: dict[str, tuple[str, str]] = {}
    for bin_, name in names.items():
        aliases.setdefault(_norm(name), (bin_, name))
    for code, words in KZ_ALIASES.items():
        name = kase.BANKS.get(code)
        if name in by_name:
            for w in words:
                aliases[_norm(w)] = (by_name[name], name)
    for name, words in NBK_ALIASES.items():
        if name in by_name:
            for w in words:
                aliases[_norm(w)] = (by_name[name], name)
    return {a: v for a, v in aliases.items() if a}, names


def resolve_bank(name: str) -> tuple[str, str] | None:
    aliases, _ = _bank_table()
    q = _norm(name)
    if not q:
        return None
    if q in aliases:
        return aliases[q]
    inside = [a for a in aliases if len(a) >= 3 and re.search(rf"(^|\s){re.escape(a)}(\s|$)", q)]
    if inside:
        return aliases[max(inside, key=len)]
    close = difflib.get_close_matches(q, list(aliases), n=1, cutoff=0.82)
    return aliases[close[0]] if close else None


def _unknown_bank(name: str) -> dict[str, Any]:
    aliases, names = _bank_table()
    hint = difflib.get_close_matches(_norm(name), list(aliases), n=3, cutoff=0.5)
    return {
        "found": False,
        "reason": f"Банк «{name}» не найден в данных Национального Банка.",
        "similar_names": [aliases[h][1] for h in hint],
    }


def list_banks() -> dict[str, Any]:
    last = fetch_all("SELECT max(period) p FROM metrics WHERE metric = 'nbk_assets'")[0]["p"]
    rows = fetch_all(
        """SELECT e.name_ru FROM metrics m JOIN entities e ON e.bin = m.bin
                        WHERE m.metric = 'nbk_assets' AND m.period = %s ORDER BY m.value DESC""",
        (last,),
    )
    return {"found": True, "as_of": str(last), "banks": [r["name_ru"] for r in rows], "source": SOURCE}
