import pytest

from fin_assistant.extract.chunking import _snap_back, chunk_document, split_on_boundaries

HEADING = "Статья 50. Требования к системе управления рисками"
LONG_ARTICLE = (
    f"{HEADING}\n\n"
    "1. Банк обязан формировать систему управления рисками. "
    + "Требования определяются уполномоченным органом. " * 8
    + "\n2. Система включает политику управления рисками. "
    + "Политика пересматривается при изменении условий. " * 8
    + "\n3. Совет директоров утверждает указанную политику ежегодно."
)


def chunks():
    return chunk_document(LONG_ARTICLE, chunk_size=400, overlap=40)


def test_long_article_is_split() -> None:
    assert len(chunks()) > 1


def test_every_piece_keeps_the_article_reference() -> None:
    assert all(c.article_ref == "Статья 50" for c in chunks())


def test_later_pieces_carry_the_heading() -> None:
    for piece in chunks()[1:]:
        assert piece.text.startswith(HEADING), piece.text[:60]


def test_first_piece_is_not_duplicated() -> None:
    first = chunks()[0].text
    assert first.startswith(HEADING)
    assert first.count(HEADING) == 1


def test_heading_makes_the_last_piece_self_contained() -> None:
    last = chunks()[-1].text
    assert "управления рисками" in last
    assert "Совет директоров" in last


def test_no_piece_body_starts_mid_word() -> None:
    for piece in chunks()[1:]:
        body = piece.text.split("\n\n", 1)[1]
        first_word = body.split()[0].strip(".,;:")
        assert first_word[0].isupper() or first_word[0].isdigit(), body[:50]


def test_snap_back_prefers_a_paragraph_break() -> None:
    text = "первое предложение.\n\nвторое предложение здесь"
    assert _snap_back(text, 30) == text.index("второе")


def test_snap_back_falls_back_to_sentence_then_space() -> None:
    assert _snap_back("раз. два три", 9) == 5
    assert _snap_back("разадва трии", 9) == 8


def test_snap_back_gives_up_on_an_unbroken_run() -> None:
    assert _snap_back("x" * 500, 300) == 300


def test_snap_back_at_end_of_text() -> None:
    assert _snap_back("короткий", 100) == len("короткий")


def test_windowing_always_advances() -> None:
    pieces = split_on_boundaries("слово " * 300, size=100, overlap=90)
    starts = [p.char_start for p in pieces]
    assert all(b > a for a, b in zip(starts, starts[1:], strict=False))


def test_windowing_covers_the_whole_text() -> None:
    text = "Предложение номер один. " * 40
    pieces = split_on_boundaries(text, size=200, overlap=20)
    assert pieces[0].char_start == 0
    assert pieces[-1].char_end == len(text)


def test_overlap_not_smaller_than_size_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be smaller"):
        split_on_boundaries("x" * 100, size=50, overlap=50)


def test_short_article_is_untouched() -> None:
    short = "Статья 9. Краткая статья\n\n1. Одно положение."
    out = chunk_document(short, chunk_size=1000, overlap=100)
    assert len(out) == 1
    assert out[0].text.count("Статья 9") == 1


def test_paragraph_without_line_breaks_is_not_treated_as_a_heading() -> None:
    one_long_line = "Статья 7. " + "Положение настоящей статьи применяется. " * 60
    pieces = chunk_document(one_long_line, chunk_size=300, overlap=50)
    assert len(pieces) > 1
    assert all(len(p.text) <= 300 for p in pieces)


def test_text_with_no_structure_still_chunks() -> None:
    plain = "Обычный текст без структуры. " * 100
    pieces = chunk_document(plain, chunk_size=200, overlap=20)
    assert len(pieces) > 1
    assert all(p.article_ref == "Документ" for p in pieces)


def test_heading_costing_a_third_of_the_chunk_is_dropped() -> None:
    heading = "Статья 11. " + "Очень длинное название статьи про требования. " * 3
    article = heading + "\n\n" + "Текст положения. " * 60
    pieces = chunk_document(article, chunk_size=300, overlap=30)
    assert all(len(p.text) <= 300 for p in pieces)
    assert not pieces[-1].text.startswith(heading[:40])
