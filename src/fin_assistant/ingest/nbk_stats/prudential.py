import re
from datetime import date
from pathlib import Path

from .common import _BANK_ROW_END, _RATIO, _SHEET_DATE, BankRatio, NbkStatsError, _num, _sheets


def _pick_sheets(names: list[str]) -> dict[date, str]:
    chosen: dict[date, str] = {}
    for name in names:
        m = _SHEET_DATE.search(name)
        if not m:
            continue
        on = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        final = "ЗО" in name.upper()
        if on not in chosen or final:
            chosen[on] = name
    return chosen


def parse_prudential(path: Path) -> list[BankRatio]:
    sheets = dict(_sheets(path))
    out: list[BankRatio] = []
    for on, name in sorted(_pick_sheets(list(sheets)).items()):
        rows = sheets[name]
        hi = next(
            (i for i, r in enumerate(rows[:15]) if any("Наименование банков" in str(c) for c in r if c)), None
        )
        if hi is None:
            continue
        header = [str(c or "").strip() for c in rows[hi]]
        col_name = next(i for i, h in enumerate(header) if "Наименование банков" in h)
        cols: dict[str, int] = {}
        for i, h in enumerate(header):
            if "Собственный капитал" in h and "reg_capital" not in cols:
                cols["reg_capital"] = i
            elif m := _RATIO.search(h):
                cols.setdefault(m.group(1).lower().replace("-", "_"), i)
        if not cols:
            continue
        period = date.fromordinal(on.toordinal() - 1)
        for r in rows[hi + 1 :]:
            if col_name >= len(r):
                continue
            bank = re.sub(r"\s+", " ", str(r[col_name] or "")).strip()
            if not bank or bank.replace(".", "").isdigit():
                continue
            if _BANK_ROW_END.match(bank):
                break
            for metric, i in cols.items():
                v = _num(r[i]) if i < len(r) else None
                if v is None:
                    continue
                if metric == "reg_capital":
                    out.append(BankRatio(period, bank, metric, v * 1000.0, "KZT"))
                else:
                    out.append(BankRatio(period, bank, metric, v, "ratio"))
    if not out:
        raise NbkStatsError(f"{path.name}: no prudential table found")
    return out
