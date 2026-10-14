import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fin_assistant.ingest.nbk_stats import _sheets

MAX_ROWS = 400
_PERIOD = re.compile(r"(19|20)\d{2}")
_UNITS = [
    (re.compile(r"млрд", re.I), 1e9),
    (re.compile(r"млн|million", re.I), 1e6),
    (re.compile(r"тыс|thousand|'000", re.I), 1e3),
]


class StatementError(Exception):
    pass


@dataclass
class StatementRow:
    sheet: str
    label: str
    values: dict[str, float] = field(default_factory=dict)


@dataclass
class Statement:
    rows: list[StatementRow]
    periods: list[str]
    unit_multiplier: float
    unit_note: str


def _num(c: object) -> float | None:
    if isinstance(c, bool):
        return None
    if isinstance(c, (int, float)):
        return float(c)
    if isinstance(c, str):
        s = c.strip().replace(" ", "").replace(" ", "")
        neg = s.startswith("(") and s.endswith(")")
        s = s.strip("()").replace(",", ".")
        try:
            v = float(s)
        except ValueError:
            return None
        return -v if neg else v
    return None


def _period_label(c: object) -> str | None:
    if isinstance(c, datetime):
        return str(c.year) if (c.month, c.day) == (12, 31) else c.date().isoformat()
    if (
        isinstance(c, (int, float))
        and not isinstance(c, bool)
        and float(c).is_integer()
        and 1990 <= c <= 2100
    ):
        return str(int(c))
    if isinstance(c, str):
        t = c.strip()
        if re.fullmatch(r"(19|20)\d{2}", t):
            return t
        if _PERIOD.search(t) and len(t) <= 40 and _num(t) is None:
            year_end = re.search(r"31\.12\.((?:19|20)\d{2})", t)
            return year_end.group(1) if year_end else t
    return None


def period_key(label: str) -> tuple[int, int, int]:
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.((?:19|20)\d{2})", label)
    if m:
        return int(m.group(3)), int(m.group(2)), int(m.group(1))
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", label)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = _PERIOD.search(label)
    return (int(m.group(0)), 12, 31) if m else (0, 0, 0)


def read_statement(path: Path) -> Statement:
    try:
        sheets = _sheets(path)
    except Exception as e:
        raise StatementError(f"{path.name}: не удалось открыть файл ({e})") from e
    rows: list[StatementRow] = []
    periods: list[str] = []
    multiplier, note = 1.0, "тенге (единица не указана)"
    for sheet, raw in sheets:
        raw = raw[:MAX_ROWS]
        head_text = " ".join(str(c) for r in raw[:12] for c in r if isinstance(c, str))
        for pattern, m in _UNITS:
            if pattern.search(head_text):
                multiplier, note = m, pattern.search(head_text).group(0)
                break
        col_period: dict[int, str] = {}
        for r in raw[:12]:
            for i, c in enumerate(r):
                label = _period_label(c)
                if label and i not in col_period:
                    col_period[i] = label
        for r in raw:
            label = next(
                (str(c).strip() for c in r if isinstance(c, str) and re.search(r"[A-Za-zА-Яа-яЁё]{3}", c)),
                None,
            )
            if not label:
                continue
            values = {}
            for i, c in enumerate(r):
                v = _num(c)
                if v is None or i not in col_period:
                    continue
                if _period_label(c) == col_period[i]:
                    continue
                values[col_period[i]] = v * multiplier
            if values:
                rows.append(StatementRow(sheet, label[:120], values))
        periods += [p for p in col_period.values() if p not in periods]
    if not rows:
        raise StatementError(f"{path.name}: не найдено строк с названием и числами по периодам")
    return Statement(rows, periods, multiplier, note)
