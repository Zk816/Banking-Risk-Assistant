from fin_assistant.agent.citations import check, extract_numbers, parse_markers

SOURCES = [
    "Статья 43. Коэффициент достаточности собственного капитала k2 устанавливается в размере не менее 0,080.",
    "Статья 16. Минимальный размер уставного капитала составляет 10 000 000 000 тенге.",
]


def test_valid_marker_passes() -> None:
    assert check("0,080 [1]", SOURCES).ok


def test_fabricated_marker_fails() -> None:
    report = check("Согласно [7], значение 0,080.", SOURCES)
    assert not report.ok
    assert report.invented == [7]


def test_marker_zero_is_fabricated() -> None:
    assert check("Значение [0] равно 0,080.", SOURCES).invented == [0]


def test_answer_without_any_citation_fails() -> None:
    report = check("Значение равно 0,080.", SOURCES)
    assert not report.ok
    assert "без единой ссылки" in report.reason


def test_repeated_marker_counted_once() -> None:
    assert check("Сначала [1], потом снова [1].", SOURCES).cited == [1]


def test_parse_markers_keeps_order_and_repeats() -> None:
    assert parse_markers("[2] затем [1] затем [2]") == [2, 1, 2]


def test_exact_number_is_traceable() -> None:
    assert check("0,080 [1]", SOURCES).untraceable_numbers == []


def test_rounded_number_is_caught() -> None:
    report = check("0,08 [1]", SOURCES)
    assert not report.ok
    assert report.untraceable_numbers == ["0,08"]


def test_number_from_a_different_article_is_caught() -> None:
    assert check("Составляет 0,065 [1]", SOURCES).untraceable_numbers == ["0,065"]


def test_thousands_separator_does_not_matter() -> None:
    assert check("10000000000 тенге [2]", SOURCES).ok


def test_nonbreaking_space_separator_is_handled() -> None:
    assert check("10 000 000 000 тенге [2]", SOURCES).ok


def test_citation_marker_is_not_read_as_a_number() -> None:
    assert "1" not in extract_numbers("Значение 0,080 [1]")


def test_structural_integers_are_skipped() -> None:
    assert extract_numbers("Статья 43 от 1995 года, пункт 2") == []


def test_number_traceable_to_an_uncited_source_still_passes() -> None:
    report = check("10 000 000 000 тенге [1]", SOURCES)
    assert report.untraceable_numbers == []
    assert report.ok


def test_reason_is_empty_when_ok() -> None:
    assert check("0,080 [1]", SOURCES).reason == ""


def test_reason_names_every_problem() -> None:
    report = check("0,08 согласно [9].", SOURCES)
    assert "несуществующие источники" in report.reason
    assert "числа, которых нет" in report.reason


def test_percentage_is_a_claim_however_short() -> None:
    assert extract_numbers("ставка НДС составляет 16%") == ["16"]


def test_spelled_out_percent_counts_too() -> None:
    assert extract_numbers("не более 10 процентов") == ["10"]


def test_amount_in_tenge_is_a_claim() -> None:
    assert extract_numbers("штраф 500 тенге") == ["500"]


def test_bare_structural_integers_are_still_skipped() -> None:
    assert extract_numbers("Статья 43 от 1995 года, пункт 2") == []


def test_percentage_present_in_the_source_passes() -> None:
    source = ["Предельная доля неработающих займов не должна превышать 10 процентов."]
    assert check("не более 10 процентов [1]", source).untraceable_numbers == []


def test_invented_percentage_is_caught() -> None:
    source = ["Предельная доля неработающих займов не должна превышать 10 процентов."]
    report = check("ставка НДС 16% [1]", source)
    assert report.untraceable_numbers == ["16"]
    assert not report.ok
