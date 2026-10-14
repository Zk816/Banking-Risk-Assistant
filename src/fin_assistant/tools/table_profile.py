import json
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from fin_assistant.agent.citations import untraceable_numbers
from fin_assistant.agent.llm import get_client
from fin_assistant.agent.prompts import RETRY_PROMPT, TABLE_SUMMARY_SYSTEM
from fin_assistant.config import settings
from fin_assistant.monitoring.tracing import observe
from fin_assistant.tools.kz_banks import _num

MAX_SHEETS = 5
MAX_COLUMNS = 40
TOP_VALUES = 5


class TableError(Exception):
    pass


@dataclass
class TableReport:
    text: str
    profile: dict[str, Any]
    rejected_numbers: list[str]


def _header_row(raw: pd.DataFrame) -> int:
    for i in range(min(15, len(raw))):
        cells = raw.iloc[i].dropna()
        if len(cells) >= 2 and sum(isinstance(c, str) for c in cells) >= 0.6 * len(cells):
            return i
    return 0


def _column(s: pd.Series) -> dict[str, Any]:
    s = s.dropna()
    out: dict[str, Any] = {"filled": int(len(s))}
    if s.empty:
        return out
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        dates = (
            pd.to_datetime(s, errors="coerce")
            if not pd.api.types.is_numeric_dtype(s)
            else pd.Series(dtype="datetime64[ns]")
        )
    nums = pd.to_numeric(s, errors="coerce")
    if nums.notna().mean() > 0.9:
        nums = nums.dropna()
        out.update(
            type="число",
            min=_num(nums.min()),
            max=_num(nums.max()),
            mean=_num(nums.mean()),
            sum=_num(nums.sum()),
        )
    elif dates.notna().mean() > 0.9:
        dates = dates.dropna()
        out.update(
            type="дата",
            first=str(dates.min().date()),
            last=str(dates.max().date()),
            by_year={str(int(y)): int(n) for y, n in dates.dt.year.value_counts().sort_index().items()},
        )
    else:
        values = s.astype(str).str.strip()
        out.update(
            type="текст",
            distinct=int(values.nunique()),
            most_frequent={v: int(n) for v, n in values.value_counts().head(TOP_VALUES).items()},
        )
    return out


def profile(path: Path) -> dict[str, Any]:
    try:
        sheets = pd.read_excel(path, sheet_name=None, header=None)
    except Exception as e:
        raise TableError(f"{path.name}: не удалось прочитать файл ({e})") from e
    out = {}
    for name, raw in list(sheets.items())[:MAX_SHEETS]:
        raw = raw.dropna(how="all").dropna(axis=1, how="all")
        if raw.empty:
            continue
        h = _header_row(raw)
        header = [str(c).strip() if pd.notna(c) else f"колонка {i + 1}" for i, c in enumerate(raw.iloc[h])]
        data = raw.iloc[h + 1 :]
        data.columns = header
        out[str(name)] = {
            "rows": int(len(data)),
            "columns": {c: _column(data[c]) for c in header[:MAX_COLUMNS]},
        }
    if not out:
        raise TableError(f"{path.name}: в файле нет данных")
    return out


def describe(table: dict[str, Any], question: str | None) -> TableReport:
    facts = json.dumps(table, ensure_ascii=False, default=str)
    messages = [
        {"role": "system", "content": TABLE_SUMMARY_SYSTEM},
        {
            "role": "user",
            "content": f"ПРОФИЛЬ ТАБЛИЦЫ:\n{facts}\n\nВОПРОС: {question or 'Опиши, что в файле.'}",
        },
    ]
    bad: list[str] = []
    for attempt in range(2):
        try:
            with observe(
                f"describe table {attempt + 1}", as_type="generation", model=settings.llm_model
            ) as gen:
                response = get_client().chat.completions.create(
                    model=settings.llm_model,
                    temperature=0.0,
                    max_tokens=3000,
                    messages=messages,
                    extra_body={"reasoning_effort": "low"},
                )
                gen.update(output=response.choices[0].message.content)
        except Exception as e:
            raise RuntimeError(f"Cannot reach the model server: {e}") from e
        text = (response.choices[0].message.content or "").strip()
        bad = untraceable_numbers(text, [facts])
        if not bad:
            return TableReport(text, table, [])
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": RETRY_PROMPT.format(numbers=", ".join(bad))},
        ]
    return TableReport(f"Описание отклонено: числа {bad} не следуют из профиля таблицы.", table, bad)
