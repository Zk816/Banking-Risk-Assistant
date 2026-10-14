import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fin_assistant.agent.citations import untraceable_numbers
from fin_assistant.agent.llm import get_client
from fin_assistant.agent.prompts import RETRY_PROMPT, STATEMENT_MAPPING_SYSTEM, STATEMENT_SUMMARY_SYSTEM
from fin_assistant.config import settings
from fin_assistant.extract.statements import Statement, StatementError, period_key, read_statement
from fin_assistant.logging_setup import get_logger
from fin_assistant.monitoring.tracing import observe
from fin_assistant.tools.kz_banks import _num, fmt_kzt, fmt_pct

log = get_logger(__name__)

ITEMS: dict[str, str] = {
    "total_assets": "Итого активы",
    "current_assets": "Итого текущие (краткосрочные) активы",
    "cash": "Денежные средства и эквиваленты",
    "receivables": "Дебиторская задолженность",
    "inventory": "Запасы",
    "total_liabilities": "Итого обязательства",
    "current_liabilities": "Итого текущие (краткосрочные) обязательства",
    "short_term_debt": "Краткосрочные займы / кредиты",
    "long_term_debt": "Долгосрочные займы / кредиты / облигации",
    "equity": "Итого собственный капитал",
    "revenue": "Выручка / доход от реализации",
    "cost_of_sales": "Себестоимость",
    "operating_profit": "Операционная прибыль / прибыль от основной деятельности",
    "interest_expense": "Расходы по вознаграждению / финансовые расходы",
    "profit_before_tax": "Прибыль до налогообложения",
    "net_income": "Чистая прибыль (убыток) за период",
    "operating_cash_flow": "Чистые денежные средства от операционной деятельности",
    "capex": "Приобретение основных средств и нематериальных активов",
    "loans_to_customers": "Кредиты / займы клиентам (для банков)",
    "customer_deposits": "Средства / вклады клиентов (для банков)",
    "npl": "Неработающие / просроченные кредиты свыше 90 дней",
    "loan_loss_provisions": "Резервы под обесценение кредитов",
}

RATIOS = [
    (
        "current_ratio",
        ("current_assets", "/", "current_liabilities"),
        "x",
        ("<", 1.0),
        "Текущая ликвидность: краткосрочные активы / краткосрочные обязательства",
    ),
    (
        "equity_ratio",
        ("equity", "/", "total_assets"),
        "pct",
        ("<", 0.10),
        "Доля собственного капитала в активах",
    ),
    (
        "debt_to_equity",
        ("total_liabilities", "/", "equity"),
        "x",
        (">", 3.0),
        "Обязательства / собственный капитал",
    ),
    (
        "net_margin",
        ("net_income", "/", "revenue"),
        "pct",
        ("<", 0.0),
        "Чистая маржа: чистая прибыль / выручка",
    ),
    ("operating_margin", ("operating_profit", "/", "revenue"), "pct", ("<", 0.0), "Операционная маржа"),
    ("roe", ("net_income", "/", "equity"), "pct", ("<", 0.0), "Рентабельность капитала (ROE)"),
    ("roa", ("net_income", "/", "total_assets"), "pct", ("<", 0.0), "Рентабельность активов (ROA)"),
    (
        "interest_coverage",
        ("operating_profit", "/", "interest_expense"),
        "x",
        ("<", 1.5),
        "Покрытие процентов: операционная прибыль / процентные расходы",
    ),
    (
        "cash_conversion",
        ("operating_cash_flow", "/", "net_income"),
        "x",
        ("<", 0.5),
        "Операционный денежный поток / чистая прибыль",
    ),
    (
        "free_cash_flow",
        ("operating_cash_flow", "-", "capex"),
        "kzt",
        ("<", 0.0),
        "Свободный денежный поток: операционный поток минус капвложения",
    ),
    (
        "loans_to_deposits",
        ("loans_to_customers", "/", "customer_deposits"),
        "x",
        (">", 1.2),
        "Кредиты / депозиты (банки)",
    ),
    (
        "npl_share",
        ("npl", "/", "loans_to_customers"),
        "pct",
        (">", 0.03),
        "Доля неработающих кредитов (банки)",
    ),
    (
        "provision_coverage",
        ("loan_loss_provisions", "/", "npl"),
        "pct",
        ("<", 0.7),
        "Покрытие NPL резервами (банки)",
    ),
]


class AnalysisError(Exception):
    pass


class NotAStatementError(AnalysisError):
    pass


@dataclass
class Analysis:
    periods: list[str]
    items: dict[str, dict[str, float]]
    mapped_labels: dict[str, str]
    ratios: list[dict[str, Any]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    unit_note: str = ""

    def as_tool_result(self) -> dict[str, Any]:
        return {
            "found": True,
            "unit_in_file": self.unit_note,
            "periods": self.periods,
            "items": {k: {p: fmt_kzt(v) for p, v in per.items()} for k, per in self.items.items()},
            "mapped_from": self.mapped_labels,
            "ratios": self.ratios,
            "risk_flags": self.flags,
        }


def map_rows(statement: Statement) -> dict[str, int]:
    listing = "\n".join(f"{i}: {r.label}" for i, r in enumerate(statement.rows))
    items = "\n".join(f"{k}: {v}" for k, v in ITEMS.items())
    with observe(
        "map statement rows",
        as_type="generation",
        model=settings.llm_model,
        input={"rows": len(statement.rows)},
    ):
        response = _map_call(statement, listing, items)
    try:
        raw = json.loads(response.choices[0].message.content or "{}")
    except json.JSONDecodeError as e:
        raise AnalysisError("модель вернула не JSON при сопоставлении строк") from e
    out = {}
    for item, idx in raw.items():
        if item in ITEMS and isinstance(idx, int) and 0 <= idx < len(statement.rows):
            out[item] = idx
    return out


def _map_call(statement: Statement, listing: str, items: str):
    return get_client().chat.completions.create(
        model=settings.llm_model,
        temperature=0.0,
        max_tokens=3000,
        messages=[
            {"role": "system", "content": STATEMENT_MAPPING_SYSTEM.format(items=items)},
            {"role": "user", "content": listing},
        ],
        response_format={"type": "json_object"},
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )


def _fmt(kind: str, v: float) -> str:
    return fmt_pct(v) if kind == "pct" else fmt_kzt(v) if kind == "kzt" else f"{_num(v)}x"


def compute(statement: Statement, mapping: dict[str, int]) -> Analysis:
    items = {k: statement.rows[i].values for k, i in mapping.items()}
    labels = {k: statement.rows[i].label for k, i in mapping.items()}
    periods = sorted((p for p in statement.periods if any(p in v for v in items.values())), key=period_key)
    analysis = Analysis(periods, items, labels, unit_note=statement.unit_note)
    for name, (a, op, b), kind, (cmp, limit), meaning in RATIOS:
        if a not in items or b not in items:
            continue
        values = {}
        for p in periods:
            x, y = items[a].get(p), items[b].get(p)
            if x is None or y is None or (op == "/" and y == 0):
                continue
            if name in ("interest_coverage",):
                y = abs(y)
            if name == "free_cash_flow":
                values[p] = x - abs(y)
            else:
                values[p] = x / y
        if not values:
            continue
        analysis.ratios.append(
            {
                "ratio": name,
                "meaning": meaning,
                "values": {p: _fmt(kind, v) for p, v in values.items()},
                "threshold": f"{'<' if cmp == '<' else '>'} {_fmt(kind, limit)}",
            }
        )
        latest = max(values, key=period_key)
        v = values[latest]
        if (cmp == "<" and v < limit) or (cmp == ">" and v > limit):
            analysis.flags.append(f"{meaning}: {_fmt(kind, v)} за {latest} (порог {cmp} {_fmt(kind, limit)})")
    if "equity" in items:
        for p, v in items["equity"].items():
            if v < 0:
                analysis.flags.append(f"Отрицательный собственный капитал за {p}: {fmt_kzt(v)}")
    return analysis


def analyze_file(path: Path) -> Analysis:
    try:
        statement = read_statement(path)
    except StatementError as e:
        raise NotAStatementError(str(e)) from e
    mapping = map_rows(statement)
    if not mapping:
        raise NotAStatementError("не удалось распознать ни одной стандартной статьи отчетности")
    log.info("statement rows=%d mapped=%d periods=%s", len(statement.rows), len(mapping), statement.periods)
    return compute(statement, mapping)


@dataclass
class Report:
    text: str
    analysis: Analysis
    rejected_numbers: list[str] = field(default_factory=list)


def summarize(analysis: Analysis, question: str | None = None) -> Report:
    facts = json.dumps(analysis.as_tool_result(), ensure_ascii=False)
    messages = [
        {"role": "system", "content": STATEMENT_SUMMARY_SYSTEM},
        {
            "role": "user",
            "content": f"РАСЧЕТ:\n{facts}\n\nВОПРОС ПОЛЬЗОВАТЕЛЯ: {question or 'Проанализируй риски.'}",
        },
    ]
    bad: list[str] = []
    for attempt in range(2):
        try:
            with observe(
                f"write analysis {attempt + 1}", as_type="generation", model=settings.llm_model
            ) as gen:
                response = get_client().chat.completions.create(
                    model=settings.llm_model,
                    temperature=0.0,
                    max_tokens=4000,
                    messages=messages,
                    extra_body={"reasoning_effort": "low"},
                )
                gen.update(output=response.choices[0].message.content)
        except Exception as e:
            raise RuntimeError(f"Cannot reach the model server: {e}") from e
        text = (response.choices[0].message.content or "").strip()
        bad = untraceable_numbers(text, [facts])
        if not bad:
            return Report(text, analysis)
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": RETRY_PROMPT.format(numbers=", ".join(bad))},
        ]
    return Report(f"Анализ отклонён: числа {bad} не следуют из расчета.", analysis, rejected_numbers=bad)
