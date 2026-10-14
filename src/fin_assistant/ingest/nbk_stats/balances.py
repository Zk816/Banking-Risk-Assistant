import re
from datetime import date
from pathlib import Path

from .common import _BANK_ROW_END, BankRatio, NbkStatsError, _num, _sheets, log
from .prudential import _pick_sheets

_UNIT_THOUSANDS = re.compile(r"тыс\.?\s*тенге|тыс\.\s*теңге", re.IGNORECASE)


def _label_to_metric(label: str) -> str | None:
    l = label.lower()
    if "доля" in l or "обратное репо" in l or "сумма просроченной задолженности" in l:
        return None
    for needle, metric in (
        ("свыше 90", "nbk_npl90"),
        ("свыше 30", "nbk_overdue30"),
        ("свыше 7", "nbk_overdue7"),
        ("3 стадии", "nbk_stage3"),
        ("кредиты с просрочкой", "nbk_overdue_any"),
        ("провизи", "nbk_provisions"),
        ("физических лиц", "nbk_deposits_individuals"),
        ("юридических лиц", "nbk_deposits_legal"),
        ("собственный капитал", "nbk_equity"),
        ("нераспределенный чистый доход", "nbk_net_income_ytd"),
        ("превышение текущих доходов", "nbk_net_income_ytd"),
        ("ссудный портфель", "nbk_loans"),
        ("в том числе займы", "nbk_loans"),
    ):
        if needle in l:
            return metric
    if l.startswith("активы"):
        return "nbk_assets"
    if l.startswith("обязательства"):
        return "nbk_liabilities"
    return None


def _column_labels(rows: list[tuple], hi: int, depth: int = 4) -> dict[int, str]:
    width = max(len(r) for r in rows[hi : hi + depth])
    labels: dict[int, str] = {}
    for i in range(width):
        parts = [
            str(rows[j][i]).strip()
            for j in range(hi, min(hi + depth, len(rows)))
            if i < len(rows[j])
            and rows[j][i] not in (None, "")
            and not re.fullmatch(r"[\d.]+", str(rows[j][i]).strip())
        ]
        if parts:
            labels[i] = " / ".join(parts)
    return labels


def _bank_rows(rows: list[tuple], start: int, col_name: int):
    for r in rows[start:]:
        if col_name >= len(r):
            continue
        bank = re.sub(r"\s+", " ", str(r[col_name] or "")).strip()
        if not bank or bank.replace(".", "").isdigit() or bank[0].isdigit():
            continue
        if _BANK_ROW_END.match(bank):
            break
        yield bank, r


MARGIN_COLUMNS: dict[str, tuple[str, str, str]] = {
    "(нетто)": ("nbk_earning_assets_net", "KZT", "Interest-earning assets, net of provisions"),
    "(брутто)": ("nbk_earning_assets_gross", "KZT", "Interest-earning assets, gross"),
    "обязательства, связанные": ("nbk_interest_bearing_liab", "KZT", "Interest-bearing liabilities"),
    "доходы, связанные": ("nbk_interest_income_ytd", "KZT", "Interest income since 1 January"),
    "расходы, связанные": ("nbk_interest_expense_ytd", "KZT", "Interest expense since 1 January"),
    "чистый доход": ("nbk_net_interest_income_ytd", "KZT", "Net interest income since 1 January"),
    "маржа": ("nbk_interest_margin", "ratio", "Net interest margin: net interest income / earning assets"),
    "спрэд": ("nbk_interest_spread", "ratio", "Spread: yield on assets minus cost of liabilities"),
}


def parse_margin(path: Path) -> list[BankRatio]:
    out: list[BankRatio] = []
    sheets = dict(_sheets(path))
    for on, name in sorted(_pick_sheets(list(sheets)).items()):
        rows = sheets[name]
        hi = next(
            (i for i, r in enumerate(rows[:15]) if any("Наименование банка" in str(c) for c in r if c)), None
        )
        if hi is None:
            continue
        cols = {}
        for i, c in enumerate(rows[hi]):
            label = str(c or "").lower()
            hit = next((v for k, v in MARGIN_COLUMNS.items() if k in label), None)
            if hit and hit[0] not in {m for m, _ in cols.values()}:
                cols[i] = (hit[0], hit[1])
        col_name = next(i for i, c in enumerate(rows[hi]) if "Наименование банка" in str(c or ""))
        period = date.fromordinal(on.toordinal() - 1)
        for bank, r in _bank_rows(rows, hi + 1, col_name):
            for i, (metric, unit) in cols.items():
                v = _num(r[i]) if i < len(r) else None
                if v is not None:
                    out.append(BankRatio(period, bank, metric, v * 1000.0 if unit == "KZT" else v, unit))
    if not out:
        raise NbkStatsError(f"{path.name}: no margin table found")
    return out


def parse_balances(path: Path) -> tuple[list[BankRatio], dict[str, str]]:
    out: list[BankRatio] = []
    names: dict[str, str] = {}
    sheets = dict(_sheets(path))
    for on, name in sorted(_pick_sheets(list(sheets)).items()):
        rows = sheets[name]
        hi = next(
            (
                i
                for i, r in enumerate(rows[:15])
                if any("Наименование БВУ" in str(c) or "Наименование банка" in str(c) for c in r if c)
            ),
            None,
        )
        if hi is None:
            continue
        col_name = next(
            i
            for i, c in enumerate(rows[hi])
            if "Наименование БВУ" in str(c or "") or "Наименование банка" in str(c or "")
        )
        code_row = next(
            (
                j
                for j in range(hi, min(hi + 4, len(rows)))
                if sum(bool(re.fullmatch(r"\d{4}(\.0)?", str(c or "").strip())) for c in rows[j]) > 5
            ),
            None,
        )
        cols: dict[int, str] = {}
        if code_row is not None:
            for i, c in enumerate(rows[code_row]):
                code = str(c or "").strip().split(".")[0]
                if re.fullmatch(r"\d{4}", code):
                    cols[i] = f"nbk_bal_{code}"
                    label_row = rows[code_row + 1] if code_row + 1 < len(rows) else ()
                    if i < len(label_row) and label_row[i]:
                        names.setdefault(cols[i], re.sub(r"\s+", " ", str(label_row[i])).strip())
            first_bank_row = code_row + 2
        else:
            for i, c in enumerate(rows[hi]):
                m = re.search(r"\((\d{4})\)", str(c or ""))
                if m:
                    cols[i] = f"nbk_bal_{m.group(1)}"
            first_bank_row = hi + 1
        if not cols:
            continue
        period = date.fromordinal(on.toordinal() - 1)
        for bank, r in _bank_rows(rows, first_bank_row, col_name):
            for i, metric in cols.items():
                v = _num(r[i]) if i < len(r) else None
                if v is not None and v != 0:
                    out.append(BankRatio(period, bank, metric, v * 1000.0, "KZT"))
    if not out:
        raise NbkStatsError(f"{path.name}: no balances table found")
    return out, names


def parse_capital_assets(path: Path) -> list[BankRatio]:
    sheets = dict(_sheets(path))
    out: list[BankRatio] = []
    for on, name in sorted(_pick_sheets(list(sheets)).items()):
        rows = sheets[name]
        hi = next(
            (i for i, r in enumerate(rows[:12]) if any("Наименование банка" in str(c) for c in r if c)), None
        )
        if hi is None:
            continue
        head = " ".join(str(c) for r in rows[:hi] for c in r if c)
        scale = 1000.0 if _UNIT_THOUSANDS.search(head) else 1.0
        cols: dict[str, int] = {}
        for i, label in _column_labels(rows, hi).items():
            metric = _label_to_metric(label)
            if metric and metric not in cols:
                cols[metric] = i
        col_name = next(i for i, c in enumerate(rows[hi]) if "Наименование банка" in str(c or ""))
        if "nbk_assets" not in cols:
            continue
        period = date.fromordinal(on.toordinal() - 1)
        for r in rows[hi + 1 :]:
            if col_name >= len(r):
                continue
            bank = re.sub(r"\s+", " ", str(r[col_name] or "")).strip()
            if not bank or bank.replace(".", "").isdigit() or bank[0].isdigit():
                continue
            if _BANK_ROW_END.match(bank):
                break
            values = {m: _num(r[i]) for m, i in cols.items() if i < len(r)}
            values = {m: v * scale for m, v in values.items() if v is not None}
            a, l, e = values.get("nbk_assets"), values.get("nbk_liabilities"), values.get("nbk_equity")
            if a and l is not None and e is not None and abs(a - l - e) > 0.01 * abs(a):
                log.warning("%s %s %s: assets != liabilities + equity, row dropped", path.name, name, bank)
                continue
            if "nbk_deposits_individuals" in values and "nbk_deposits_legal" in values:
                values["nbk_deposits"] = values["nbk_deposits_individuals"] + values["nbk_deposits_legal"]
            out += [BankRatio(period, bank, m, v, "KZT") for m, v in values.items()]
    if not out:
        raise NbkStatsError(f"{path.name}: no capital/assets table found")
    return out
