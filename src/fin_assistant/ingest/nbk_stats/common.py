import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import openpyxl

from fin_assistant.logging_setup import get_logger

log = get_logger(__name__)

SOURCE = "bank_statistics"

BASE = "https://nationalbank.kz"

INDEX = f"{BASE}/ru/news/banks-performance"

USER_AGENT = "fin-assistant research (kairatzhaidar816@gmail.com)"

DELAY_S = 2.0

TIMEOUT_S = 120

DOWNLOAD_TRIES = 4

_RUBRIC = re.compile(r"banks-performance/rubrics/(\d+)")

_FILE = re.compile(r'href="/file/download/(\d+)"[^>]*>\s*([^<]{3,200})')

_SHEET_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")

_RATIO = re.compile(r"\((k\d(?:-\d)?)\)", re.IGNORECASE)

_BANK_ROW_END = re.compile(r"^(итого|всего)", re.IGNORECASE)

KINDS: dict[str, str] = {
    "prudential": "выполнении пруденциальных нормативов",
    "capital_assets": "собственном капитале, обязательствах и активах",
    "loan_quality": "структуре и качестве ссудного портфеля",
    "overdue_loans": "кредитам с учетом удельного веса кредитов с просрочкой",
    "liquidity": "ликвидности",
    "margin": "процентной марже",
    "deposits": "структуре вкладов",
    "funding": "структуре фондирования",
    "asset_class": "классификации активов",
    "balances": "на балансовых и внебалансовых",
    "securities": "портфеля ценных бумаг",
    "investments": "инвестиц",
}


class NbkStatsError(Exception):
    pass


@dataclass(frozen=True)
class BankRatio:
    period: date
    bank: str
    metric: str
    value: float
    unit: str


def _sheets(path: Path) -> list[tuple[str, list[tuple]]]:
    if path.suffix.lower() == ".xls":
        import xlrd

        wb = xlrd.open_workbook(path, on_demand=True)
        return [
            (name, [tuple(wb.sheet_by_name(name).row_values(i)) for i in range(wb.sheet_by_name(name).nrows)])
            for name in wb.sheet_names()
        ]
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
