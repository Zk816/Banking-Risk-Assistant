"""Kazakh bank balance sheets from KASE (Kazakhstan Stock Exchange).

Every listed bank discloses its regulatory balance sheet, form 700-Н, each
quarter on its KASE issuer page as an Excel file: account-by-account balances of
the banking chart of accounts. This is the only machine-readable per-bank
financial data for Kazakhstan we found: the regulator's site is unreachable from
here and the financial-statement depository (dfo.kz) serves empty report forms.

The issuer page embeds its whole document list as JSON (name, category, date,
file link), so listing a bank's reports needs one request and no guessing.
KASE has no robots.txt; requests are still spaced out and identified.
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import httpx
import openpyxl

from kzbank.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "kase"
BASE = "https://kase.kz"
USER_AGENT = "kzbank research (kairatzhaidar816@gmail.com)"
DELAY_S = 2.0
TIMEOUT_S = 60.0

# Second-tier banks listed on KASE, by issuer code. Subsidiaries that are not
# banks (Halyk Finance, BCC Invest) and foreign issuers are left out on purpose.
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
_MONTHS = {m: i for i, m in enumerate(
    ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
     "сентября", "октября", "ноября", "декабря"], start=1)}
_DATE_WORDS = re.compile(r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})")
# Raw printed assets below this with no unit marker are read as thousands of tenge.
UNIT_GUESS_LIMIT = 1e10
# Printed assets must equal liabilities + equity within this share.
TOTAL_TOLERANCE = 0.005


class KaseError(Exception):
    """A KASE page or file could not be fetched or understood."""


@dataclass(frozen=True)
class Report:
    code: str
    name: str        # e.g. "Отчет (форма 700-Н) на 01 июля 2026 года"
    url: str
    published: date


@dataclass(frozen=True)
class Balance:
    report_date: date
    account: str     # 4-digit account; "1"/"2"/"3" are printed class totals
    name: str
    amount: float    # tenge


def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S,
                        follow_redirects=True)


def list_reports(code: str) -> tuple[str | None, list[Report]]:
    """The BIN printed on the issuer page (if any) and every form 700-Н spreadsheet, oldest first.

    Raises:
        KaseError: if the issuer page cannot be fetched.
    """
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
            continue   # one malformed entry must not hide the rest
        link = doc.get("link") or ""
        if _FORM_700.search(doc.get("name", "")) and link.lower().endswith((".xls", ".xlsx")):
            reports.append(Report(code, doc["name"], BASE + link, date.fromisoformat(doc["date0"])))
    m = _BIN.search(page.text)
    return (m.group(1) if m else None), sorted(reports, key=lambda r: r.published)


def download(report: Report, raw_dir: Path) -> tuple[Path, bool]:
    """Save one report under raw_dir/CODE/. Returns (path, downloaded_now).

    Raises:
        KaseError: if the file cannot be fetched.
    """
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


# --- Parsing ------------------------------------------------------------------

def _cell(c: object) -> str:
    # Some files store text as formulas: ="1001". Legacy .xls stores codes as
    # floats: 1000.0. Both are normalised to the plain code.
    if isinstance(c, float) and c.is_integer():
        return str(int(c))
    return str(c).strip().strip('="') if c is not None else ""


def _number(c: object) -> float | None:
    return float(c) if isinstance(c, (int, float)) and not isinstance(c, bool) else None


def _load_rows(path: Path) -> list[tuple]:
    """First sheet as tuples of cell values. .xlsx via openpyxl, legacy .xls via xlrd.

    Raises:
        KaseError: if the file cannot be opened.
    """
    try:
        if path.suffix.lower() == ".xls":
            import xlrd   # only needed for the 2006-2021 archive
            sheet = xlrd.open_workbook(path).sheet_by_index(0)
            return [tuple(sheet.row_values(i)) for i in range(sheet.nrows)]
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        return [tuple(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    except Exception as e:  # noqa: BLE001  both libraries raise many types on a bad file
        raise KaseError(f"{path.name}: cannot open: {e}") from e


def parse_700n(path: Path) -> list[Balance]:
    """Read the balances out of one form 700-Н spreadsheet.

    Two layouts exist on KASE:
      flat   - one row per account and breakdown, a REPORT_DATE column, in
               tenge (Halyk, ForteBank). Every row is a posting account.
      report - the printed form "Отчет об остатках на балансовых и
               внебалансовых счетах", often in thousands of tenge (BCC,
               Freedom). Group totals are interleaved with accounts and some
               groups are not printed at all, so the tree cannot be rebuilt:
               codes ending in 0 are dropped and the class totals are read
               from the printed class rows, which must balance.

    Raises:
        KaseError: an unreadable file, an unknown layout, or totals that do not balance.
    """
    rows = _load_rows(path)

    for i, r in enumerate(rows[:30]):
        labels = [_cell(c) for c in r]
        has_date_col = any("DATE" in h.upper() for h in labels)
        # Home Credit: "Номер счета | Наименование | Сумма | Номер счета СГК",
        # no date column, class and group totals printed: a report, not flat.
        # Flat files break every account down by residency; the printed report does not.
        if "Номер счета" in labels and not has_date_col and "Признак резидентства" not in labels:
            return _parse_report(path, rows, i, labels)
        if "Номер счета" in labels:
            return _parse_flat(path, rows, i, labels)
        if "№ счета" in labels or "КОДЫ" in labels or "Коды" in labels:
            return _parse_report(path, rows, i, labels)
    raise KaseError(f"{path.name}: unknown layout (no account-number header)")


_FILE_DATE = re.compile(r"_(\d{2})(\d{2})(\d{2})(?:[_.]|$)")


def _date_from_filename(path: Path) -> date | None:
    """eubn_700h_311221.xlsx -> 2021-12-31; hsbk_700h_010726 ("на 01.07.26") -> 2026-06-30."""
    m = _FILE_DATE.search(path.stem + ".")
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), 2000 + int(m.group(3))
    try:
        day = date(y, mo, d)
    except ValueError:
        return None
    # A balance "на 01 июля" is the close of 30 June.
    return day.fromordinal(day.toordinal() - 1) if d == 1 else day


def _parse_flat(path: Path, rows: list[tuple], hi: int, header: list[str]) -> list[Balance]:
    col_acc = header.index("Номер счета")
    col_name = header.index("Наименование номера счета") if "Наименование номера счета" in header else col_acc + 1
    col_date = next((i for i, h in enumerate(header) if "DATE" in h.upper()), None)
    # Some banks (Eurasian) omit the date column; the file name carries it.
    fallback = _date_from_filename(path) if col_date is None else None
    if col_date is None and fallback is None:
        raise KaseError(f"{path.name}: flat layout without a date column or dated file name")
    out = []
    for r in rows[hi + 1:]:
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
    """The date the balances are as of, from the header rows.

    The header also cites the regulation that defines the form ("постановлению
    ... от 21.04.2020г. №54"); that date is not the report date, and taking it
    once stamped every Freedom Bank report with 2020-04-21. Rows citing a
    regulation are skipped, and a "за 30 сентября 2024 года" phrase wins.
    """
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
            candidates.append(date(int(g[0]), int(g[1]), int(g[2])) if g[0] else date(int(g[5]), int(g[4]), int(g[3])))
    return candidates[0] if candidates else None


def _parse_report(path: Path, rows: list[tuple], hi: int, header: list[str]) -> list[Balance]:
    head_text = " ".join(_cell(c) for r in rows[:hi] for c in r).lower()
    scale = 1000.0 if re.search(r"тысячах|тыс\.", head_text) else None
    report_date = _report_date(rows[:hi])
    if report_date is None:
        raise KaseError(f"{path.name}: report layout without a report date")

    blocks = [i for i, h in enumerate(header) if h in ("№ счета", "КОДЫ", "Коды", "Номер счета")]
    # Where a "Сумма" column is named, read the amount there: a later column
    # may hold another code (Home Credit prints an extended account code).
    amount_col = header.index("Сумма") if "Сумма" in header and len(blocks) == 1 else None
    out: list[Balance] = []
    totals: dict[str, float] = {}
    seen: set[str] = set()
    for r in rows[hi + 1:]:
        for b in blocks:
            if b + 2 >= len(r):
                continue
            acc, name = _cell(r[b]), _cell(r[b + 1])
            if amount_col is not None:
                amount = _number(r[amount_col]) if amount_col < len(r) else None
            else:
                amount = next((v for c in r[b + 2:b + 4] if (v := _number(c)) is not None), None)
            # Skip the column-numbering row under the header: "1 | 2 | 3 | 4".
            if amount is None or not name or name.isdigit():
                continue
            if acc in {"1", "2", "3"}:
                totals.setdefault(acc, amount * (scale or 1.0))
            # First row of an account is its total; following rows with the
            # same code are its breakdown by residency, sector and currency.
            elif _ACCOUNT.fullmatch(acc) and not acc.endswith("0") and acc not in seen:
                seen.add(acc)
                out.append(Balance(report_date, acc, name, amount * (scale or 1.0)))
    if not out or set(totals) != {"1", "2", "3"}:
        raise KaseError(f"{path.name}: report layout without accounts or class totals")
    if scale is None:
        # No unit printed (2006-era files). The printed form is kept in
        # thousands; no bank here has under 10 bn tenge of assets, so a raw
        # total below 1e10 can only be thousands.
        scale = 1000.0 if totals["1"] < UNIT_GUESS_LIMIT else 1.0
        out = [Balance(b.report_date, b.account, b.name, b.amount * scale) for b in out]
        totals = {k: v * scale for k, v in totals.items()}
    if abs(totals["1"] - totals["2"] - totals["3"]) > TOTAL_TOLERANCE * totals["1"]:
        raise KaseError(f"{path.name}: printed assets != liabilities + equity")
    return out + [Balance(report_date, cls, "итог класса", v) for cls, v in totals.items()]


# --- From accounts to metrics -------------------------------------------------
# Kazakhstan's banking chart of accounts. Contra accounts (provisions, discounts)
# are already negative in form 700-Н, so every group is a plain sum; verified on
# Halyk 2026-06-30: assets 21.1961 trln = liabilities + equity to the tenge.

def _in(acc: str, lo: int, hi: int) -> bool:
    return lo <= int(acc) <= hi


METRIC_RULES: dict[str, tuple[str, object]] = {
    "assets":        ("активы", lambda a: a[0] == "1"),
    "liabilities":   ("обязательства", lambda a: a[0] == "2"),
    "equity":        ("собственный капитал", lambda a: a[0] == "3"),
    # Loans to customers: principal incl. overdue, before provisions (1401-1427).
    "loans_gross":   ("займы клиентам до провизий", lambda a: _in(a, 1401, 1427)),
    # Same group plus provisions, discount, premium (1428-1435): the carrying amount.
    "loans_net":     ("займы клиентам за вычетом провизий", lambda a: _in(a, 1401, 1435)),
    "overdue_loans": ("просроченная задолженность клиентов", lambda a: a in {"1409", "1421", "1424"}),
    # Provisions are a negative balance; reported as a positive allowance.
    "credit_loss_allowance": ("провизии по займам клиентам", lambda a: a == "1428"),
    # Customer accounts and deposits; lease liabilities (2227) are not deposits.
    "deposits":      ("вклады и текущие счета клиентов", lambda a: _in(a, 2201, 2249) and a != "2227"),
    # Current-year retained profit: year-to-date, NOT comparable with annual figures.
    "net_income_ytd": ("чистая прибыль с начала года", lambda a: a == "3599"),
}


def to_metrics(balances: list[Balance]) -> dict[str, float]:
    """Sum account balances into named metrics for one report date.

    Printed class totals (report layout) are used for assets, liabilities and
    equity instead of a leaf sum, which in that layout can be partial.
    """
    out: dict[str, float] = {}
    for metric, (_, rule) in METRIC_RULES.items():
        out[metric] = sum(b.amount for b in balances if len(b.account) == 4 and rule(b.account))  # type: ignore[operator]
    out["credit_loss_allowance"] = -out["credit_loss_allowance"]
    printed = {b.account: b.amount for b in balances if len(b.account) == 1}
    for cls, metric in (("1", "assets"), ("2", "liabilities"), ("3", "equity")):
        if cls in printed:
            out[metric] = printed[cls]
    return out
