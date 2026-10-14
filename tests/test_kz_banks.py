import pytest

from fin_assistant.tools import kz_banks as t


def test_formats_are_russian_style() -> None:
    assert t.fmt_kzt(10_539_907_022_000) == "10,54 трлн тенге"
    assert t.fmt_kzt(539_100_000_000) == "539,1 млрд тенге"
    assert t.fmt_pct(0.0682) == "6,82%"


@pytest.mark.parametrize(
    "spelling,expected",
    [
        ("Каспи", "Kaspi Bank"),
        ("kaspi", "Kaspi Bank"),
        ("Халык", "Halyk"),
        ("Народный банк", "Halyk"),
        ("BCC", "ЦентрКредит"),
        ("Банк ЦентрКредит", "ЦентрКредит"),
        ("ВТБ", "VTB"),
        ("Forte", "ForteBank"),
    ],
)
def test_resolves_common_spellings(spelling: str, expected: str) -> None:
    hit = t.resolve_bank(spelling)
    assert hit is not None and expected in hit[1]


def test_unknown_bank_is_not_matched_to_a_lookalike() -> None:
    assert t.resolve_bank("Ромашка") is None
    result = t.get_bank_metrics("Ромашка", ["npl90_share"])
    assert result["found"] is False and "не найден" in result["reason"]


def test_metric_values_carry_date_and_source() -> None:
    result = t.get_bank_metrics("Kaspi", ["k2"])
    row = result["values"][0]
    assert row["as_of"] and row["value"].endswith("%") and "regulatory_minimum" in row
    assert result["source"].startswith("bank_statistics")


def test_unknown_metric_is_refused_with_the_list() -> None:
    result = t.get_bank_metrics("Kaspi", ["roe_magic"])
    assert result["values"][0]["found"] is False


def test_schemas_and_functions_match() -> None:
    names = {s["function"]["name"] for s in t.TOOL_SCHEMAS}
    assert names == set(t.TOOL_REGISTRY)
