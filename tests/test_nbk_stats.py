from datetime import date
from pathlib import Path

import openpyxl

from fin_assistant.ingest.nbk_stats import (
    _label_to_metric,
    _pick_sheets,
    canonical_bank,
    normalise_bank,
    parse_capital_assets,
    parse_prudential,
)


def test_normalise_strips_legal_form_quotes_and_footnotes() -> None:
    assert normalise_bank('АО  "Банк ЦентрКредит"*') == "банк центркредит"
    assert normalise_bank('ДБ АО "Сбербанк"') == "сбербанк"
    assert normalise_bank('АО "Altyn Bank" (ДБ АО "Народный Банк Казахстана")') == "altyn bank"


def test_renames_share_one_history_but_mergers_do_not() -> None:
    assert canonical_bank('ДБ АО "Сбербанк"') == canonical_bank('АО "Bereke Bank"') == "Bereke Bank"
    assert canonical_bank('АО "Цеснабанк"') == canonical_bank('АО "Alatau City Bank"')
    assert canonical_bank('АО "КАЗКОММЕРЦБАНК"') != canonical_bank('АО "Народный Банк Казахстана"')


def test_january_sheet_prefers_final_version() -> None:
    chosen = _pick_sheets(["01.01.2026", "01.01.2026 с ЗО", "01.02.2026"])
    assert chosen[date(2026, 1, 1)] == "01.01.2026 с ЗО"
    assert len(chosen) == 2


def test_column_labels_map_to_metrics() -> None:
    assert _label_to_metric("Кредиты с просрочкой платежей / свыше 90 дней / сумма") == "nbk_npl90"
    assert _label_to_metric("свыше 90 дней / доля в ссудном портфеле") is None
    assert _label_to_metric("Активы") == "nbk_assets"
    assert _label_to_metric("из них вклады / физических лиц") == "nbk_deposits_individuals"
    assert _label_to_metric('в том числе / операции "Обратное РЕПО"') is None


def _book(tmp_path: Path, name: str, sheets: dict[str, list[list]]) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for r in rows:
            ws.append(r)
    path = tmp_path / name
    wb.save(path)
    return path


def test_parse_prudential(tmp_path: Path) -> None:
    rows = [
        ["Выполнение пруденциальных нормативов"],
        ["(тыс. тенге)"],
        [
            "№ п/п",
            "Наименование банков второго уровня",
            "Собственный капитал ",
            "Коэф.достаточности (k1)",
            "Коэф.достаточности (k1-2)",
            "Коэф.достаточности (k2)",
        ],
        [1, 2, 3, 4, 5, 6],
        [1, 'АО "Kaspi Bank"', 1000, 0.127, 0.127, 0.13],
        ["Итого", None, None, None, None, None],
    ]
    got = parse_prudential(_book(tmp_path, "p.xlsx", {"01.07.2026": rows}))
    by = {r.metric: r for r in got}
    assert by["reg_capital"].value == 1_000_000
    assert by["k2"].value == 0.13
    assert by["k1"].period == date(2026, 6, 30)
    assert {r.bank for r in got} == {'АО "Kaspi Bank"'}


def test_parse_capital_assets_drops_unbalanced_rows(tmp_path: Path) -> None:
    rows = [
        ["Сведения о собственном капитале"],
        ["тыс. тенге"],
        ["№", "Наименование банка", "Активы", "Обязательства", "Собственный капитал по балансу"],
        [1, 'АО "Нурбанк"', 100, 80, 20],
        [2, 'АО "Плохой банк"', 100, 80, 50],
    ]
    got = parse_capital_assets(_book(tmp_path, "c.xlsx", {"01.08.2026": rows}))
    assert {r.bank for r in got} == {'АО "Нурбанк"'}
    assert {r.metric: r.value for r in got}["nbk_assets"] == 100_000
