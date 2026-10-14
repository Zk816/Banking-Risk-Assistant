from datetime import date
from typing import Any

from fin_assistant.logging_setup import get_logger
from fin_assistant.storage.db import fetch_all

log = get_logger(__name__)

MAX_ROWS = 24

CONCEPTS: dict[str, str] = {
    "assets": "совокупные активы",
    "liabilities": "обязательства",
    "equity": "собственный капитал",
    "deposits": "депозиты клиентов",
    "net_income": "чистая прибыль",
    "interest_income": "процентные доходы",
    "loans_net": "ссудный портфель за вычетом резервов",
    "credit_loss_allowance": "резервы под кредитные убытки",
}

_COMPANY_SQL = """
SELECT bin, name_en FROM entities
WHERE type = 'edgar' AND name_en ILIKE %s
ORDER BY length(name_en)
LIMIT 5
"""

_METRIC_SQL = """
SELECT m.value, m.unit, m.period, e.name_en, d.title AS form, d.url
FROM metrics m
JOIN entities e ON e.bin = m.bin
LEFT JOIN documents d ON d.id = m.source_doc
WHERE m.bin = %s AND m.metric = %s
  AND (%s::date IS NULL OR m.period = %s::date)
ORDER BY m.period DESC
LIMIT %s
"""

_COMPANIES_SQL = """
SELECT e.name_en, count(DISTINCT m.metric) AS concepts,
       min(m.period) AS earliest, max(m.period) AS latest
FROM entities e JOIN metrics m ON m.bin = e.bin
WHERE e.type = 'edgar'
GROUP BY e.name_en ORDER BY e.name_en
"""


def _humanise(value: float, unit: str) -> str:
    for threshold, suffix in ((1e12, "трлн"), (1e9, "млрд"), (1e6, "млн")):
        if abs(value) >= threshold:
            scaled = value / threshold
            return f"{scaled:,.2f}".replace(",", " ").replace(".", ",") + f" {suffix} {unit}"
    return f"{value:,.0f}".replace(",", " ") + f" {unit}"


def _find_company(name: str) -> dict[str, Any] | None:
    rows = fetch_all(_COMPANY_SQL, (f"%{name.strip()}%",))
    return rows[0] if rows else None


def get_company_metric(company: str, metric: str, period: str | None = None) -> dict[str, Any]:
    if metric not in CONCEPTS:
        return {
            "found": False,
            "error": f"Неизвестный показатель '{metric}'. Доступны: {', '.join(CONCEPTS)}",
        }

    match = _find_company(company)
    if match is None:
        return {"found": False, "error": f"Компания '{company}' не найдена в базе"}

    on_date = None
    if period:
        try:
            on_date = date.fromisoformat(period)
        except ValueError:
            return {"found": False, "error": f"Дата '{period}' не в формате YYYY-MM-DD"}

    rows = fetch_all(_METRIC_SQL, (match["bin"], metric, on_date, on_date, 1 if on_date else MAX_ROWS))
    if not rows:
        when = f" на {period}" if period else ""
        return {"found": False, "error": f"{match['name_en']}: показателя '{metric}'{when} нет в базе"}

    log.info("tool get_company_metric %s/%s rows=%d", match["bin"], metric, len(rows))
    return {
        "found": True,
        "company": match["name_en"],
        "metric": metric,
        "description": CONCEPTS[metric],
        "values": [
            {
                "period": r["period"].isoformat(),
                "value": float(r["value"]),
                "formatted": _humanise(float(r["value"]), r["unit"]),
                "unit": r["unit"],
                "source": r["form"],
            }
            for r in rows
        ],
    }


def list_companies() -> dict[str, Any]:
    rows = fetch_all(_COMPANIES_SQL)
    return {
        "count": len(rows),
        "metrics_available": list(CONCEPTS),
        "companies": [
            {
                "name": r["name_en"],
                "concepts": r["concepts"],
                "earliest": r["earliest"].isoformat(),
                "latest": r["latest"].isoformat(),
            }
            for r in rows
        ],
    }


_ADEQUACY_PAIRS: dict[str, tuple[str, str, str]] = {
    "tier1": ("tier1_ratio", "tier1_required", "достаточность капитала первого уровня"),
    "total": ("total_capital_ratio", "total_capital_required", "достаточность совокупного капитала"),
}

_ADEQUACY_SQL = """
SELECT m.metric, m.value, m.period, d.title AS form
FROM metrics m LEFT JOIN documents d ON d.id = m.source_doc
WHERE m.bin = %s AND m.metric = ANY(%s)
  AND m.period = (
      SELECT max(period) FROM metrics
      WHERE bin = %s AND metric = ANY(%s)
        AND (%s::date IS NULL OR period = %s::date)
  )
"""


def check_capital_adequacy(company: str, rule: str = "tier1", period: str | None = None) -> dict[str, Any]:
    if rule not in _ADEQUACY_PAIRS:
        return {
            "found": False,
            "error": f"Неизвестный норматив '{rule}'. Доступны: {', '.join(_ADEQUACY_PAIRS)}",
        }

    match = _find_company(company)
    if match is None:
        return {"found": False, "error": f"Компания '{company}' не найдена в базе"}

    actual_key, required_key, label = _ADEQUACY_PAIRS[rule]
    keys = [actual_key, required_key]

    on_date = None
    if period:
        try:
            on_date = date.fromisoformat(period)
        except ValueError:
            return {"found": False, "error": f"Дата '{period}' не в формате YYYY-MM-DD"}

    rows = fetch_all(_ADEQUACY_SQL, (match["bin"], keys, match["bin"], keys, on_date, on_date))
    values = {r["metric"]: r for r in rows}

    if actual_key not in values or required_key not in values:
        missing = [k for k in keys if k not in values]
        return {
            "found": False,
            "error": f"{match['name_en']}: нет данных по {', '.join(missing)} — сравнение невозможно",
        }

    actual = float(values[actual_key]["value"])
    required = float(values[required_key]["value"])
    meets = actual >= required

    actual_pct = f"{actual * 100:.2f}".replace(".", ",")
    required_pct = f"{required * 100:.2f}".replace(".", ",")

    return {
        "found": True,
        "company": match["name_en"],
        "rule": label,
        "period": values[actual_key]["period"].isoformat(),
        "actual_pct": actual_pct,
        "required_pct": required_pct,
        "meets_requirement": meets,
        "verdict": (
            f"{match['name_en']}: {label} {actual_pct}% "
            f"при требуемых {required_pct}% — "
            f"норматив {'СОБЛЮДАЕТСЯ' if meets else 'НАРУШЕН'}"
        ),
        "source": values[actual_key]["form"],
    }


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_company_metric",
            "description": (
                "Финансовый показатель банка или компании из отчётности SEC "
                "(формы 10-K, 10-Q). Активы, депозиты, капитал, прибыль, "
                "ссудный портфель. Используй для любых вопросов о цифрах "
                "конкретной компании. Никогда не называй цифру по памяти."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "Название компании, например JPMorgan, Wells Fargo, Citigroup",
                    },
                    "metric": {
                        "type": "string",
                        "enum": list(CONCEPTS),
                        "description": "Какой показатель",
                    },
                    "period": {
                        "type": "string",
                        "description": "Конец квартала, YYYY-MM-DD. Опусти, чтобы получить ряд за последние периоды.",
                    },
                },
                "required": ["company", "metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_capital_adequacy",
            "description": (
                "Соблюдает ли банк норматив достаточности капитала. Сравнение "
                "выполняется в инструменте; в ответе есть готовое поле verdict — "
                "процитируй его дословно, ничего не вычисляй сам."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {"type": "string", "description": "Название банка"},
                    "rule": {
                        "type": "string",
                        "enum": list(_ADEQUACY_PAIRS),
                        "description": "tier1 — капитал первого уровня, total — совокупный",
                    },
                    "period": {
                        "type": "string",
                        "description": "Конец квартала YYYY-MM-DD; опусти для последнего",
                    },
                },
                "required": ["company"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_companies",
            "description": (
                "Список компаний в базе и доступных показателей. Вызови, если "
                "не уверен, есть ли нужная компания."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

REGISTRY = {
    "get_company_metric": get_company_metric,
    "check_capital_adequacy": check_capital_adequacy,
    "list_companies": list_companies,
}
