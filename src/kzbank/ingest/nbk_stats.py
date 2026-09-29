"""Per-bank prudential ratios from the National Bank's statistics section.

nationalbank.kz/ru/news/banks-performance publishes, per year, Excel files such
as "Сведения о выполнении пруденциальных нормативов": one sheet per reporting
date, one row per second-tier bank, with regulatory capital and the capital
adequacy ratios k1 / k1-1 / k1-2 / k2. Unlike form 700-Н, it covers every bank
in the country (Kaspi, Citibank, Bank of China...) and banks that later failed.
robots.txt does not disallow this section.

Ratio definitions changed over time (k1-1 existed 2010-2015; the Basel III set
k1, k1-2, k2 from 2016), so every ratio is stored under its exact printed name
and never merged across regimes.
"""

import re
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import openpyxl

from kzbank.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "nbk_stats"
BASE = "https://nationalbank.kz"
INDEX = f"{BASE}/ru/news/banks-performance"
USER_AGENT = "kzbank research (kairatzhaidar816@gmail.com)"
DELAY_S = 2.0
TIMEOUT_S = 120
DOWNLOAD_TRIES = 4

_RUBRIC = re.compile(r"banks-performance/rubrics/(\d+)")
_FILE = re.compile(r'href="/file/download/(\d+)"[^>]*>\s*([^<]{3,200})')
_SHEET_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_RATIO = re.compile(r"\((k\d(?:-\d)?)\)", re.IGNORECASE)
_BANK_ROW_END = re.compile(r"^(итого|всего)", re.IGNORECASE)

# File kinds by title; only the ones parsed today are downloaded by default.
KINDS: dict[str, str] = {
    "prudential": "выполнении пруденциальных нормативов",
    "capital_assets": "собственном капитале, обязательствах и активах",
    "loan_quality": "структуре и качестве ссудного портфеля",
    "overdue_loans": "кредитам с учетом удельного веса кредитов с просрочкой",
    "liquidity": "ликвидности",
    "margin": "процентной марже",
}


class NbkStatsError(Exception):
    """A page or file from the NBK statistics section could not be used."""


@dataclass(frozen=True)
class StatFile:
    file_id: str
    title: str
    kind: str | None


def _get(url: str) -> str:
    """curl, not httpx: nationalbank.kz resets Python TLS handshakes from this host."""
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        r = subprocess.run(["curl", "-sL", "-m", str(TIMEOUT_S), "-A", USER_AGENT, url],
                           capture_output=True, text=True, timeout=TIMEOUT_S + 10)
        if r.returncode == 0 and r.stdout:
            return r.stdout
        time.sleep(DELAY_S * 3 * attempt)      # timeouts come and go on this site
    raise NbkStatsError(f"{url}: curl exit {r.returncode} after {DOWNLOAD_TRIES} tries")


def list_files() -> list[StatFile]:
    """Every file on every year page of the statistics section.

    Raises:
        NbkStatsError: if the index page cannot be fetched.
    """
    import html as _html
    rubrics = sorted(set(_RUBRIC.findall(_get(INDEX))))
    out: dict[str, StatFile] = {}
    for rubric in rubrics:
        page = _get(f"{INDEX}/rubrics/{rubric}")
        for file_id, title in _FILE.findall(page):
            title = _html.unescape(title).strip()
            if not title:
                continue      # each link appears twice: an empty icon link, then the titled one
            kind = next((k for k, needle in KINDS.items() if needle in title.lower()), None)
            out.setdefault(file_id, StatFile(file_id, title, kind))
        time.sleep(DELAY_S)
    return list(out.values())


def download(f: StatFile, raw_dir: Path) -> Path:
    """Save one file as raw_dir/<kind>_<id>.xlsx|.xls, detected from its magic bytes.

    Raises:
        NbkStatsError: if the download fails or is neither xlsx nor xls.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    existing = list(raw_dir.glob(f"{f.kind}_{f.file_id}.*"))
    if existing:
        return existing[0]
    tmp = raw_dir / f"{f.kind}_{f.file_id}.part"
    head = b""
    for attempt in range(1, DOWNLOAD_TRIES + 1):
        r = subprocess.run(["curl", "-sL", "-m", str(TIMEOUT_S), "-A", USER_AGENT, "-o", str(tmp),
                            f"{BASE}/file/download/{f.file_id}"], timeout=TIMEOUT_S + 10)
        head = tmp.read_bytes()[:4] if tmp.exists() else b""
        if r.returncode == 0 and head:
            break
        time.sleep(DELAY_S * 3 * attempt)      # the site drops a connection now and then
    if not head:
        tmp.unlink(missing_ok=True)
        raise NbkStatsError(f"download {f.file_id} failed")
    ext = ".xlsx" if head[:2] == b"PK" else ".xls" if head == b"\xd0\xcf\x11\xe0" else None
    if ext is None:
        tmp.unlink(missing_ok=True)
        raise NbkStatsError(f"{f.file_id}: not a spreadsheet")
    target = tmp.with_suffix(ext)
    tmp.rename(target)
    time.sleep(DELAY_S)
    return target


# --- Parsing ------------------------------------------------------------------

@dataclass(frozen=True)
class BankRatio:
    period: date      # balance date: "на 1 января 2026" is the close of 31 Dec 2025
    bank: str         # name exactly as printed
    metric: str       # reg_capital | k1 | k1_1 | k1_2 | k2
    value: float
    unit: str         # KZT | ratio


def _sheets(path: Path) -> list[tuple[str, list[tuple]]]:
    """(sheet name, rows) for every sheet. .xls through xlrd, .xlsx through openpyxl."""
    if path.suffix.lower() == ".xls":
        import xlrd
        wb = xlrd.open_workbook(path, on_demand=True)
        return [(name, [tuple(wb.sheet_by_name(name).row_values(i))
                        for i in range(wb.sheet_by_name(name).nrows)]) for name in wb.sheet_names()]
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    return [(ws.title, [tuple(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]


def _num(c: object) -> float | None:
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        return float(c)
    if isinstance(c, str):
        try:
            return float(c.replace(" ", "").replace(",", "."))
        except ValueError:
            return None
    return None


def _pick_sheets(names: list[str]) -> dict[date, str]:
    """One sheet per date. For 1 January prefer the version "с ЗО" (after year-end entries)."""
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
    """All (bank, date, ratio) values in one "выполнение пруденциальных нормативов" file.

    Raises:
        NbkStatsError: if no sheet has the expected header.
    """
    sheets = dict(_sheets(path))
    out: list[BankRatio] = []
    for on, name in sorted(_pick_sheets(list(sheets)).items()):
        rows = sheets[name]
        hi = next((i for i, r in enumerate(rows[:15])
                   if any("Наименование банков" in str(c) for c in r if c)), None)
        if hi is None:
            continue
        header = [str(c or "").strip() for c in rows[hi]]
        col_name = next(i for i, h in enumerate(header) if "Наименование банков" in h)
        cols: dict[str, int] = {}
        for i, h in enumerate(header):
            if "Собственный капитал" in h and "reg_capital" not in cols:
                cols["reg_capital"] = i
            elif (m := _RATIO.search(h)):
                cols.setdefault(m.group(1).lower().replace("-", "_"), i)
        if not cols:
            continue
        # "на 1 июля" means the close of 30 June, as in form 700-Н.
        period = date.fromordinal(on.toordinal() - 1)
        for r in rows[hi + 1:]:
            if col_name >= len(r):
                continue
            bank = re.sub(r"\s+", " ", str(r[col_name] or "")).strip()
            if not bank or bank.replace(".", "").isdigit():
                continue          # the "1 | 2 | 3" column-number row
            if _BANK_ROW_END.match(bank):
                break
            for metric, i in cols.items():
                v = _num(r[i]) if i < len(r) else None
                if v is None:
                    continue
                if metric == "reg_capital":
                    out.append(BankRatio(period, bank, metric, v * 1000.0, "KZT"))   # file is in thousands
                else:
                    out.append(BankRatio(period, bank, metric, v, "ratio"))
    if not out:
        raise NbkStatsError(f"{path.name}: no prudential table found")
    return out


# --- Bank identity ------------------------------------------------------------

def normalise_bank(name: str) -> str:
    """'АО  "Банк ЦентрКредит"*' and 'АО "БАНК ЦЕНТРКРЕДИТ"' -> 'банк центркредит'.

    Legal form, quotes, notes in parentheses, footnote asterisks and case are
    dropped. The asterisks mark footnotes whose meaning changes from sheet to
    sheet ("report restated", a regulation reference), so they carry no label.
    """
    s = re.sub(r"\(.*?\)|\*+|\)", " ", name)
    s = re.sub(r"[\"«»“”'`]", " ", s)
    s = re.sub(r"\b(ао|дб|до|ао дб|дочерний банк|акционерное общество)\b", " ", s, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s).strip().lower()


# One bank, one history. Spelling variants and RENAMES of the same legal entity
# map to one name; the rename is confirmed in the data by one name's series
# ending the month the other's begins. Mergers (Kazkommertsbank into Halyk in
# 2018, First Heartland Bank into Jusan) are NOT renames and stay separate.
CANONICAL: dict[str, str] = {
    # renames
    "сбербанк": "Bereke Bank", "сбербанк россии": "Bereke Bank", "bereke bank": "Bereke Bank",
    "цеснабанк": "Alatau City Bank", "first heartland jýsan bank": "Alatau City Bank",
    "first heartland jusan bank": "Alatau City Bank", "jysan bank": "Alatau City Bank",
    "alatau city bank": "Alatau City Bank",
    "жилстройсбербанк казахстана": "Otbasy Bank", "отбасы банк": "Otbasy Bank",
    "банк тураналем": "BTA Bank", "банктураналем": "BTA Bank", "бта банк": "BTA Bank",
    "банк фридом финанс казахстан": "Freedom Bank", "фридом банк казахстан": "Freedom Bank",
    # spelling variants
    "home credit bank": "Home Credit Bank", "банк хоум кредит": "Home Credit Bank",
    "хоум кредит банк": "Home Credit Bank", "хоум кредит энд финанс банк": "Home Credit Bank",
    "банк bank rbk": "Bank RBK", "bank rbk": "Bank RBK", "банк rbk": "Bank RBK",
    "торгово-промышленный банк китая в алматы": "ICBC Almaty",
    "торгово-промышленный банк китая в г. алматы": "ICBC Almaty",
    "тпб китая в г.алматы": "ICBC Almaty", "тпбк": "ICBC Almaty",
    "исламский банк заман-банк": "Zaman Bank", "иб заман-банк": "Zaman Bank", "заман-банк": "Zaman Bank",
    "кзи банк": "KZI Bank", "казахстан-зираат интернешнл банк": "KZI Bank",
    "банк бта банк - темiрбанк": "Temirbank", "бта банк - темiрбанк": "Temirbank",
    "банк туран алем - темiрбанк": "Temirbank", "банктураналем - темiрбанк": "Temirbank",
    "темiрбанк": "Temirbank", "сеним-банк": "Senim Bank", "сенім-банк": "Senim Bank",
    "банк позитив": "Bank Pozitiv", "банк позитив казахстан": "Bank Pozitiv",
    "народный банк казахстана": "Halyk Bank", "банк центркредит": "Bank CenterCredit",
    "fortebank": "ForteBank", "евразийский банк": "Eurasian Bank", "нурбанк": "Nurbank",
    "altyn bank": "Altyn Bank", "kmf банк": "KMF Bank", "kaspi bank": "Kaspi Bank",
    "банк втб": "VTB Kazakhstan", "ситибанк казахстан": "Citibank Kazakhstan",
    "шинхан банк казахстан": "Shinhan Bank Kazakhstan", "банк китая в казахстане": "Bank of China Kazakhstan",
}

# Canonical name -> KASE issuer code, so both sources land on one entity.
KASE_CODE: dict[str, str] = {
    "Halyk Bank": "HSBK", "Bank CenterCredit": "CCBN", "ForteBank": "ASBN", "Freedom Bank": "FFBN",
    "Eurasian Bank": "EUBN", "Home Credit Bank": "HCBN", "Bereke Bank": "BERK", "Bank RBK": "INBN",
    "Nurbank": "NRBN", "Altyn Bank": "ATBN", "Alatau City Bank": "TSBN", "Otbasy Bank": "JSBN",
    "KMF Bank": "MFKM",
}


def canonical_bank(name: str) -> str:
    """The one name a bank's whole history is stored under."""
    norm = normalise_bank(name)
    return CANONICAL.get(norm, norm)


# --- "Сведения о собственном капитале, обязательствах и активах" ----------------
# Balance sheet, loan quality and profit for every bank, monthly since 2005.
# Columns are found by their printed label, not by position: the layout gained
# overdue buckets (7/30/90 days) and IFRS 9 "stage 3" columns over the years.
# Metrics carry an "nbk_" prefix so they never mix with the form 700-Н figures
# of the same banks, whose account-based definitions differ slightly.
_UNIT_THOUSANDS = re.compile(r"тыс\.?\s*тенге|тыс\.\s*теңге", re.IGNORECASE)


def _label_to_metric(label: str) -> str | None:
    l = label.lower()
    if "доля" in l or "обратное репо" in l or "сумма просроченной задолженности" in l:
        return None                       # shares are recomputed; repo and arrears amounts are not used
    for needle, metric in (
        ("свыше 90", "nbk_npl90"), ("свыше 30", "nbk_overdue30"), ("свыше 7", "nbk_overdue7"),
        ("3 стадии", "nbk_stage3"), ("кредиты с просрочкой", "nbk_overdue_any"),
        ("провизи", "nbk_provisions"), ("физических лиц", "nbk_deposits_individuals"),
        ("юридических лиц", "nbk_deposits_legal"), ("собственный капитал", "nbk_equity"),
        ("нераспределенный чистый доход", "nbk_net_income_ytd"),
        ("превышение текущих доходов", "nbk_net_income_ytd"),
        ("ссудный портфель", "nbk_loans"), ("в том числе займы", "nbk_loans"),
    ):
        if needle in l:
            return metric
    if l.startswith("активы"):
        return "nbk_assets"
    if l.startswith("обязательства"):
        return "nbk_liabilities"
    return None


def _column_labels(rows: list[tuple], hi: int, depth: int = 4) -> dict[int, str]:
    """Join the multi-row header of each column: 'Кредиты с просрочкой / свыше 90 дней / сумма'."""
    width = max(len(r) for r in rows[hi:hi + depth])
    labels: dict[int, str] = {}
    for i in range(width):
        parts = [str(rows[j][i]).strip() for j in range(hi, min(hi + depth, len(rows)))
                 if i < len(rows[j]) and rows[j][i] not in (None, "")
                 and not re.fullmatch(r"[\d.]+", str(rows[j][i]).strip())]
        if parts:
            labels[i] = " / ".join(parts)
    return labels


def parse_capital_assets(path: Path) -> list[BankRatio]:
    """Every bank's balance-sheet and loan-quality figures, one sheet per month.

    A bank row whose assets differ from liabilities + equity by more than 1% is
    dropped: in these files that means a misread or a footnote row.

    Raises:
        NbkStatsError: if no sheet has the expected header.
    """
    sheets = dict(_sheets(path))
    out: list[BankRatio] = []
    for on, name in sorted(_pick_sheets(list(sheets)).items()):
        rows = sheets[name]
        hi = next((i for i, r in enumerate(rows[:12])
                   if any("Наименование банка" in str(c) for c in r if c)), None)
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
        for r in rows[hi + 1:]:
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
