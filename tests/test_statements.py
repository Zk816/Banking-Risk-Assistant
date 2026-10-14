from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fin_assistant.api import main
from fin_assistant.extract.statements import period_key, read_statement
from fin_assistant.tools.statement_analysis import compute

FIXTURE = Path(__file__).parent / "fixtures" / "test_statement.xlsx"


def _row(statement, label: str) -> int:
    return next(i for i, r in enumerate(statement.rows) if r.label == label)


@pytest.fixture(scope="module")
def statement():
    return read_statement(FIXTURE)


def test_reads_thousands_brackets_and_one_label_per_year(statement) -> None:
    assert statement.unit_multiplier == 1e3
    assert sorted(statement.periods, key=period_key) == ["2024", "2025"]
    cost = statement.rows[_row(statement, "Себестоимость реализации")]
    assert cost.values["2025"] == -3_700_000_000


def test_ratios_and_flags_are_computed_by_code(statement) -> None:
    mapping = {
        k: _row(statement, label)
        for k, label in {
            "current_assets": "Итого краткосрочных активов",
            "current_liabilities": "Итого краткосрочных обязательств",
            "operating_profit": "Операционная прибыль",
            "interest_expense": "Финансовые расходы",
            "operating_cash_flow": "Чистые денежные средства от операционной деятельности",
            "capex": "Приобретение основных средств",
        }.items()
    }
    a = compute(statement, mapping)
    ratios = {r["ratio"]: r["values"] for r in a.ratios}
    assert ratios["current_ratio"]["2025"] == "0,89x"
    assert ratios["interest_coverage"]["2025"] == "0,76x"
    assert ratios["free_cash_flow"]["2025"] == "-460,0 млн тенге"
    assert any("Текущая ликвидность" in f and "2025" in f for f in a.flags)


def test_upload_rejects_non_excel(monkeypatch) -> None:
    from fin_assistant.config import settings

    monkeypatch.setattr(settings, "api_keys", "")
    with TestClient(main.app, raise_server_exceptions=False) as client:
        r = client.post("/v1/analyze", files={"file": ("report.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 422 and r.json()["code"] == "bad_file"


def test_table_profile_is_computed_by_code(tmp_path) -> None:
    import openpyxl

    from fin_assistant.tools.table_profile import profile

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Объект", "Город", "Дата начала", "Квартир"])
    for name, city, start, n in [
        ("ЖК А", "Астана", "2021-03-01", 100),
        ("ЖК Б", "Алматы", "2022-05-01", 50),
        ("ЖК В", "Астана", "2021-07-01", 30),
    ]:
        ws.append([name, city, start, n])
    path = tmp_path / "registry.xlsx"
    wb.save(path)
    cols = profile(path)["Sheet"]["columns"]
    assert cols["Город"]["most_frequent"]["Астана"] == 2
    assert cols["Дата начала"]["by_year"] == {"2021": 2, "2022": 1}
    assert cols["Квартир"]["sum"] == "180,00"
