import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import httpx
import openpyxl

from fin_assistant.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "bank_balance_sheets"
BASE = "https://kase.kz"
USER_AGENT = "fin-assistant research (kairatzhaidar816@gmail.com)"
DELAY_S = 2.0
TIMEOUT_S = 60.0

BANKS: dict[str, str] = {
    "HSBK": "Народный Банк Казахстана (Halyk Bank)",
    "CCBN": "Банк ЦентрКредит",
    "ASBN": "ForteBank",
    "FFBN": "Фридом Банк Казахстан",
    "EUBN": "Евразийский банк",
    "HCBN": "Home Credit Bank",
    "BERK": "Bereke Bank",
    "INBN": "Bank RBK",
    "NRBN": "Нурбанк",
    "ATBN": "Altyn Bank",
    "TSBN": "Alatau City Bank",
    "JSBN": "Отбасы банк",
    "MFKM": "KMF Банк",
}

_DOC_JSON = re.compile(r'\{"logo_rectangular".*?"language":"[a-z]+"\}')
_BIN = re.compile(r"БИН\s*[–—-]\s*(\d{12})")
_FORM_700 = re.compile(r"700-?[НH]", re.IGNORECASE)
_ACCOUNT = re.compile(r"\d{4}")
_DATE_CELL = re.compile(r"(\d{4})-(\d{2})-(\d{2})|(\d{1,2})\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{4})")
_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "января",
            "февраля",
            "марта",
            "апреля",
            "мая",
            "июня",
            "июля",
            "августа",
            "сентября",
            "октября",
            "ноября",
            "декабря",
        ],
        start=1,
    )
}
_DATE_WORDS = re.compile(r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})")
UNIT_GUESS_LIMIT = 1e10
TOTAL_TOLERANCE = 0.005


class KaseError(Exception):
    pass


@dataclass(frozen=True)
class Report:
    code: str
    name: str
    url: str
    published: date


@dataclass(frozen=True)
class Balance:
    report_date: date
    account: str
    name: str
    amount: float


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S, follow_redirects=True)


def list_reports(code: str) -> tuple[str | None, list[Report]]:
    try:
        with _client() as c:
            page = c.get(f"{BASE}/ru/issuers/{code}/")
            page.raise_for_status()
    except httpx.HTTPError as e:
        raise KaseError(f"issuer page {code}: {e}") from e

    reports = []
    for m in _DOC_JSON.finditer(page.text):
        try:
            doc = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        link = doc.get("link") or ""
        if _FORM_700.search(doc.get("name", "")) and link.lower().endswith((".xls", ".xlsx")):
            reports.append(Report(code, doc["name"], BASE + link, date.fromisoformat(doc["date0"])))
    m = _BIN.search(page.text)
    return (m.group(1) if m else None), sorted(reports, key=lambda r: r.published)


def download(report: Report, raw_dir: Path) -> tuple[Path, bool]:
    target = raw_dir / report.code / report.url.rsplit("/", 1)[-1]
    if target.exists() and target.stat().st_size > 0:
        return target, False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _client() as c:
            r = c.get(report.url)
            r.raise_for_status()
    except httpx.HTTPError as e:
        raise KaseError(f"{report.url}: {e}") from e
    target.write_bytes(r.content)
    time.sleep(DELAY_S)
    return target, True


def _cell(c: object) -> str:
    if isinstance(c, float) and c.is_integer():
        return str(int(c))
    return str(c).strip().strip('="') if c is not None else ""


def _number(c: object) -> float | None:
    return float(c) if isinstance(c, (int, float)) and not isinstance(c, bool) else None


def _load_rows(path: Path) -> list[tuple]:
    try:
        if path.suffix.lower() == ".xls":
            import xlrd

            sheet = xlrd.open_workbook(path).sheet_by_index(0)
            return [tuple(sheet.row_values(i)) for i in range(sheet.nrows)]
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        return [tuple(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    except Exception as e:
        raise KaseError(f"{path.name}: cannot open: {e}") from e


def parse_700n(path: Path) -> list[Balance]:
    rows = _load_rows(path)

    for i, r in enumerate(rows[:30]):
        labels = [_cell(c) for c in r]
        has_date_col = any("DATE" in h.upper() for h in labels)
        if "Номер счета" in labels and not has_date_col and "Признак резидентства" not in labels:
            return _parse_report(path, rows, i, labels)
        if "Номер счета" in labels:
            return _parse_flat(path, rows, i, labels)
        if "№ счета" in labels or "КОДЫ" in labels or "Коды" in labels:
            return _parse_report(path, rows, i, labels)
    raise KaseError(f"{path.name}: unknown layout (no account-number header)")


_FILE_DATE = re.compile(r"_(\d{2})(\d{2})(\d{2})(?:[_.]|$)")


def _date_from_filename(path: Path) -> date | None:
    m = _FILE_DATE.search(path.stem + ".")
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), 2000 + int(m.group(3))
    try:
        day = date(y, mo, d)
    except ValueError:
        return None
    return day.fromordinal(day.toordinal() - 1) if d == 1 else day


def _parse_flat(path: Path, rows: list[tuple], hi: int, header: list[str]) -> list[Balance]:
    col_acc = header.index("Номер счета")
    col_name = (
        header.index("Наименование номера счета") if "Наименование номера счета" in header else col_acc + 1
    )
    col_date = next((i for i, h in enumerate(header) if "DATE" in h.upper()), None)
    fallback = _date_from_filename(path) if col_date is None else None
    if col_date is None and fallback is None:
        raise KaseError(f"{path.name}: flat layout without a date column or dated file name")
    out = []
    for r in rows[hi + 1 :]:
        if not r or col_acc >= len(r) or r[col_acc] is None:
            continue
        account = _cell(r[col_acc])
        if not _ACCOUNT.fullmatch(account):
            continue
        amount = next((v for c in reversed(r) if (v := _number(c)) is not None), None)
        d = r[col_date] if col_date is not None else fallback
        if amount is None or not isinstance(d, (datetime, date)):
            continue
        out.append(Balance(d.date() if isinstance(d, datetime) else d, account, _cell(r[col_name]), amount))
    if not out:
        raise KaseError(f"{path.name}: flat layout but no balances parsed")
    return out


def _report_date(rows: list[tuple]) -> date | None:
    candidates: list[date] = []
    for r in rows:
        text = " ".join(_cell(c) for c in r).lower()
        if "постановлен" in text or "приложение" in text:
            continue
        for c in r:
            if isinstance(c, datetime):
                candidates.append(c.date())
            elif isinstance(c, date):
                candidates.append(c)
        if m := _DATE_WORDS.search(text):
            return date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
        if m := _DATE_CELL.search(text):
            g = m.groups()
            candidates.append(
                date(int(g[0]), int(g[1]), int(g[2])) if g[0] else date(int(g[5]), int(g[4]), int(g[3]))
            )
    return candidates[0] if candidates else None


def _parse_report(path: Path, rows: list[tuple], hi: int, header: list[str]) -> list[Balance]:
    head_text = " ".join(_cell(c) for r in rows[:hi] for c in r).lower()
    scale = 1000.0 if re.search(r"тысячах|тыс\.", head_text) else None
    report_date = _report_date(rows[:hi])
    if report_date is None:
        raise KaseError(f"{path.name}: report layout without a report date")

    blocks = [i for i, h in enumerate(header) if h in ("№ счета", "КОДЫ", "Коды", "Номер счета")]
    amount_col = header.index("Сумма") if "Сумма" in header and len(blocks) == 1 else None
    out: list[Balance] = []
    totals: dict[str, float] = {}
    seen: set[str] = set()
    for r in rows[hi + 1 :]:
        for b in blocks:
            if b + 2 >= len(r):
                continue
            acc, name = _cell(r[b]), _cell(r[b + 1])
            if amount_col is not None:
                amount = _number(r[amount_col]) if amount_col < len(r) else None
            else:
                amount = next((v for c in r[b + 2 : b + 4] if (v := _number(c)) is not None), None)
            if amount is None or not name or name.isdigit():
                continue
            if acc in {"1", "2", "3"}:
                totals.setdefault(acc, amount * (scale or 1.0))
            elif _ACCOUNT.fullmatch(acc) and not acc.endswith("0") and acc not in seen:
                seen.add(acc)
                out.append(Balance(report_date, acc, name, amount * (scale or 1.0)))
    if not out or set(totals) != {"1", "2", "3"}:
        raise KaseError(f"{path.name}: report layout without accounts or class totals")
    if scale is None:
        scale = 1000.0 if totals["1"] < UNIT_GUESS_LIMIT else 1.0
        out = [Balance(b.report_date, b.account, b.name, b.amount * scale) for b in out]
        totals = {k: v * scale for k, v in totals.items()}
    if abs(totals["1"] - totals["2"] - totals["3"]) > TOTAL_TOLERANCE * totals["1"]:
        raise KaseError(f"{path.name}: printed assets != liabilities + equity")
    return out + [Balance(report_date, cls, "итог класса", v) for cls, v in totals.items()]


def _in(acc: str, lo: int, hi: int) -> bool:
    return lo <= int(acc) <= hi


METRIC_RULES: dict[str, tuple[str, object]] = {
    "assets": ("активы", lambda a: a[0] == "1"),
    "liabilities": ("обязательства", lambda a: a[0] == "2"),
    "equity": ("собственный капитал", lambda a: a[0] == "3"),
    "loans_gross": ("займы клиентам до провизий", lambda a: _in(a, 1401, 1427)),
    "loans_net": ("займы клиентам за вычетом провизий", lambda a: _in(a, 1401, 1435)),
    "overdue_loans": ("просроченная задолженность клиентов", lambda a: a in {"1409", "1421", "1424"}),
    "credit_loss_allowance": ("провизии по займам клиентам", lambda a: a == "1428"),
    "deposits": ("вклады и текущие счета клиентов", lambda a: _in(a, 2201, 2249) and a != "2227"),
    "net_income_ytd": ("чистая прибыль с начала года", lambda a: a == "3599"),
}


def to_metrics(balances: list[Balance]) -> dict[str, float]:
    out: dict[str, float] = {}
    for metric, (_, rule) in METRIC_RULES.items():
        out[metric] = sum(b.amount for b in balances if len(b.account) == 4 and rule(b.account))
    out["credit_loss_allowance"] = -out["credit_loss_allowance"]
    printed = {b.account: b.amount for b in balances if len(b.account) == 1}
    for cls, metric in (("1", "assets"), ("2", "liabilities"), ("3", "equity")):
        if cls in printed:
            out[metric] = printed[cls]
    return out
