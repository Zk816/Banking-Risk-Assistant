from datetime import date
from typing import Any

from fin_assistant.logging_setup import get_logger
from fin_assistant.storage.db import fetch_all, fetch_one
from fin_assistant.storage.freshness import SOURCE_POLICY, age_of

log = get_logger(__name__)

MAX_ROWS = 50

_MAX_RATE_AGE_DAYS = SOURCE_POLICY["exchange_rates"][1]

_RATE_SQL = """
SELECT m.value, m.unit, m.period, d.title AS source_title, d.url AS source_url
FROM metrics m
LEFT JOIN documents d ON d.id = m.source_doc
WHERE m.metric = %s AND m.period = %s
"""

_LATEST_RATE_SQL = """
SELECT m.value, m.unit, m.period, d.title AS source_title, d.url AS source_url
FROM metrics m
LEFT JOIN documents d ON d.id = m.source_doc
WHERE m.metric = %s
ORDER BY m.period DESC
LIMIT 1
"""

_COVERAGE_SQL = """
SELECT replace(metric, 'fx_rate_', '') AS code,
       min(period) AS earliest, max(period) AS latest, count(*) AS n
FROM metrics
WHERE metric LIKE 'fx_rate_%%'
GROUP BY metric
ORDER BY code
LIMIT %s
"""


def get_exchange_rate(currency: str, on_date: str | None = None) -> dict[str, Any]:
    code = currency.strip().upper()
    metric = f"fx_rate_{code.lower()}"

    if on_date:
        try:
            period = date.fromisoformat(on_date)
        except ValueError:
            return {"found": False, "error": f"Дата '{on_date}' не в формате YYYY-MM-DD"}
        row = fetch_one(_RATE_SQL, (metric, period))
        missing = f"Курс {code} на {on_date} не найден"
    else:
        row = fetch_one(_LATEST_RATE_SQL, (metric,))
        missing = f"Курс {code} не найден ни на одну дату"

    if row is None:
        log.info("tool get_exchange_rate miss code=%s date=%s", code, on_date)
        return {"found": False, "error": missing}

    result = {
        "found": True,
        "currency": code,
        "value": float(row["value"]),
        "unit": row["unit"],
        "period": row["period"].isoformat(),
        "source": row["source_title"],
        "source_url": row["source_url"],
    }

    if not on_date:
        age = age_of(row["period"])
        result["age_days"] = age
        if age > _MAX_RATE_AGE_DAYS:
            result["stale"] = True
            result["warning"] = (
                f"Это последние данные, но им {age} дн. "
                f"Курса на сегодня в базе нет — обязательно укажи дату в ответе."
            )
            log.warning("stale rate served code=%s age=%dd", code, age)

    return result


def list_available_rates() -> dict[str, Any]:
    rows = fetch_all(_COVERAGE_SQL, (MAX_ROWS,))
    return {
        "count": len(rows),
        "currencies": [
            {
                "code": r["code"].upper(),
                "earliest": r["earliest"].isoformat(),
                "latest": r["latest"].isoformat(),
                "days": r["n"],
            }
            for r in rows
        ],
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_exchange_rate",
            "description": (
                "Официальный курс валюты Национального Банка Казахстана, "
                "в тенге за ОДНУ единицу валюты. Используй для любых вопросов "
                "о курсах валют. Никогда не называй курс по памяти."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "currency": {
                        "type": "string",
                        "description": "Трёхбуквенный код валюты: USD, EUR, RUB, CNY",
                    },
                    "on_date": {
                        "type": "string",
                        "description": "Дата в формате YYYY-MM-DD. Опусти, чтобы получить последний доступный курс.",
                    },
                },
                "required": ["currency"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_available_rates",
            "description": (
                "Список валют, по которым есть данные, и за какие даты. "
                "Вызови, если не уверен, есть ли нужная валюта или дата."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

TOOL_REGISTRY = {
    "get_exchange_rate": get_exchange_rate,
    "list_available_rates": list_available_rates,
}
