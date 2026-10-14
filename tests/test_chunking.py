import pytest

from fin_assistant.extract.chunking import chunk_document, chunk_text, normalize_whitespace

LEGAL = (
    "Глава 3. Пруденциальные нормативы\n\n"
    "Статья 42. Минимальный размер уставного капитала банка "
    "составляет 10 миллиардов тенге.\n\n"
    "Статья 43. Коэффициент достаточности собственного капитала k2 "
    "устанавливается в размере не менее 0,08."
)


def test_empty_text_yields_nothing() -> None:
    assert chunk_text("", chunk_size=100, overlap=10) == []
    assert chunk_text("   \n\n  ", chunk_size=100, overlap=10) == []


def test_short_text_is_one_chunk() -> None:
    chunks = chunk_text("короткий текст", chunk_size=100, overlap=10)
    assert len(chunks) == 1
    assert chunks[0].ordinal == 0
    assert chunks[0].text == "короткий текст"


def test_chunks_are_contiguous_and_ordered() -> None:
    text = "a" * 250
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    starts = [c.char_start for c in chunks]
    assert starts == [0, 80, 160]
    assert chunks[-1].char_end == len(text)


def test_overlap_actually_repeats_content() -> None:
    text = "".join(str(i % 10) for i in range(300))
    chunks = chunk_text(text, chunk_size=100, overlap=30)
    assert chunks[0].text[-30:] == chunks[1].text[:30]


def test_offsets_map_back_to_source() -> None:
    text = "b" * 500
    for c in chunk_text(text, chunk_size=120, overlap=20):
        assert text[c.char_start : c.char_end].strip() == c.text


def test_overlap_not_smaller_than_size_is_rejected() -> None:
    with pytest.raises(ValueError, match="smaller than chunk_size"):
        chunk_text("x" * 100, chunk_size=50, overlap=50)


def test_normalize_collapses_blank_runs() -> None:
    assert normalize_whitespace("a\n\n\n\n\nb") == "a\n\nb"
    assert normalize_whitespace("trailing   \nspaces  ") == "trailing\nspaces"


def test_naive_chunking_splits_an_article_midway() -> None:
    chunks = chunk_text(LEGAL, chunk_size=60, overlap=0)
    heading = next(c for c in chunks if "Статья 42" in c.text)
    assert "10 миллиардов" not in heading.text
    orphan = next(c for c in chunks if "10 миллиардов" in c.text)
    assert "Статья 42" not in orphan.text


def test_structure_chunking_keeps_the_article_whole() -> None:
    chunks = chunk_document(LEGAL, chunk_size=1000, overlap=0)
    article_42 = next(c for c in chunks if c.article_ref.endswith("Статья 42"))
    assert "10 миллиардов" in article_42.text


def test_every_chunk_knows_its_article() -> None:
    for chunk in chunk_document(LEGAL):
        assert chunk.article_ref is not None


def test_chunks_start_at_the_article_heading() -> None:
    for chunk in chunk_document(LEGAL):
        if "Статья" in chunk.article_ref:
            assert chunk.text.startswith("Статья"), chunk.text[:40]


def test_long_article_is_split_but_keeps_its_reference() -> None:
    long_article = "Статья 7. " + "Положение настоящей статьи применяется. " * 60
    chunks = chunk_document(long_article, chunk_size=300, overlap=50)
    assert len(chunks) > 1
    assert all(c.article_ref == "Статья 7" for c in chunks)


def test_chapter_is_included_in_the_reference() -> None:
    chunks = chunk_document(LEGAL)
    refs = {c.article_ref for c in chunks}
    assert any(r.startswith("Глава") and "Статья" in r for r in refs), refs


def test_text_without_articles_falls_back_to_windowing() -> None:
    plain = "Обычный текст без структуры. " * 100
    chunks = chunk_document(plain, chunk_size=200, overlap=20)
    assert len(chunks) > 1
    assert all(c.article_ref == "Документ" for c in chunks)
