from typing import Any

from .banks import list_banks
from .countries import INDICATORS, country_indicator
from .documents import DATASETS, search_documents
from .formatting import METRICS
from .metrics import (
    compare_banks,
    get_bank_metrics,
    get_base_rate,
    get_ratings,
    metric_history,
    rank_banks,
    sector_history,
)


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_METRIC_ENUM = {
    "type": "string",
    "enum": list(METRICS),
    "description": "; ".join(f"{k}: {v[2]}" for k, v in METRICS.items()),
}

_BANK = {
    "type": "string",
    "description": "Название банка как написал пользователь (Kaspi, Халык, BCC, ForteBank...)",
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    _fn(
        "get_bank_metrics",
        "Показатели одного казахстанского банка на последнюю дату (или на дату on_date): "
        "NPL, капитал k1/k2, ликвидность k4, активы, депозиты, прибыль, маржа, провизии.",
        {
            "bank": _BANK,
            "metrics": {"type": "array", "items": _METRIC_ENUM},
            "on_date": {"type": "string", "description": "YYYY-MM-DD, необязательно"},
        },
        ["bank", "metrics"],
    ),
    _fn(
        "compare_banks",
        "Сравнить один показатель у нескольких банков на общую дату.",
        {"banks": {"type": "array", "items": _BANK}, "metric": _METRIC_ENUM},
        ["banks", "metric"],
    ),
    _fn(
        "rank_banks",
        "Рейтинг всех банков по показателю (у кого самый высокий/низкий NPL, капитал, маржа, активы...).",
        {"metric": _METRIC_ENUM, "highest_first": {"type": "boolean"}, "top_n": {"type": "integer"}},
        ["metric"],
    ),
    _fn(
        "metric_history",
        "Динамика показателя банка по годам и изменение за 12 месяцев (растет ли просрочка, ухудшается ли капитал).",
        {"bank": _BANK, "metric": _METRIC_ENUM, "years": {"type": "integer"}},
        ["bank", "metric"],
    ),
    _fn(
        "sector_history",
        "Показатель по всему банковскому сектору по годам (доля NPL сектора, активы сектора).",
        {"metric": _METRIC_ENUM, "years": {"type": "integer"}},
        ["metric"],
    ),
    _fn("list_banks", "Список банков, по которым есть данные за последний месяц.", {}, []),
    _fn("get_ratings", "Текущие кредитные рейтинги банка от Moody's, S&P, Fitch.", {"bank": _BANK}, ["bank"]),
    _fn(
        "country_indicator",
        "Годовые показатели Всемирного банка по Казахстану и соседям: NPL, инфляция, рост ВВП, ROE банков, кредит/ВВП.",
        {
            "indicator": {"type": "string", "enum": list(INDICATORS)},
            "countries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "kazakhstan, uzbekistan, kyrgyzstan, russia, georgia, azerbaijan, armenia, turkey, poland, mongolia, world",
            },
            "year": {"type": "integer"},
        },
        ["indicator"],
    ),
    _fn(
        "get_base_rate",
        "Базовая ставка Национального Банка на дату (по умолчанию сейчас).",
        {"on_date": {"type": "string", "description": "YYYY-MM-DD"}},
        [],
    ),
    _fn(
        "search_documents",
        "Поиск по текстам документов: законы, постановления НБ РК, отчеты о финстабильности, МСФО-отчетность и "
        "аудиторские заключения компаний, стандарты Базеля, обзоры НБ, отчеты Всемирного банка. Для вопросов 'почему', "
        "'как рассчитывается', 'что требует закон', 'кто аудитор', 'какие меры'.",
        {
            "query": {"type": "string", "description": "поисковый запрос по смыслу, на русском"},
            "query_en": {
                "type": "string",
                "description": "тот же запрос на английском — обязательно для basel_standards и "
                "worldbank_reports (эти документы на английском) и когда нужны термины вроде LCR, HQLA, NPE",
            },
            "datasets": {
                "type": "array",
                "items": {"type": "string", "enum": list(DATASETS)},
                "description": "; ".join(f"{k}: {v}" for k, v in DATASETS.items()),
            },
            "bank": {"type": "string", "description": "если вопрос про конкретную компанию/банк"},
            "doc_types": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "audit",
                        "review",
                        "fs",
                        "rating",
                        "law",
                        "regulation",
                        "stability_report",
                        "basel_standard",
                    ],
                },
            },
        },
        ["query"],
    ),
]

TOOL_REGISTRY: dict[str, Any] = {
    "get_bank_metrics": get_bank_metrics,
    "compare_banks": compare_banks,
    "rank_banks": rank_banks,
    "metric_history": metric_history,
    "sector_history": sector_history,
    "list_banks": list_banks,
    "get_ratings": get_ratings,
    "country_indicator": country_indicator,
    "get_base_rate": get_base_rate,
    "search_documents": search_documents,
}
