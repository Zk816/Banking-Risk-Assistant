from pathlib import Path

import pytest

from fin_assistant.ingest.adilet import (
    UnusableDocumentError,
    parse_directory,
    parse_file,
)

FIXTURES = Path(__file__).parent / "fixtures"

SPA_SHELL = """<!doctype html>
<html lang="ru"><head><title>НЦПС «Әділет»</title></head>
<body><!--SEO_BODY--><div id="root"></div></body></html>"""


def test_parses_a_saved_act() -> None:
    doc = parse_file(FIXTURES / "synthetic_bank_law.txt")
    assert doc.source == "laws"
    assert doc.language == "ru"
    assert "10 000 000 000" in doc.text
    assert len(doc.content_hash) == 64


def test_extracts_publication_date_from_russian_words() -> None:
    doc = parse_file(FIXTURES / "synthetic_bank_law.txt")
    assert doc.published_at is not None
    assert (doc.published_at.year, doc.published_at.month, doc.published_at.day) == (1995, 8, 31)


def test_extracts_edition() -> None:
    doc = parse_file(FIXTURES / "synthetic_bank_law.txt")
    assert doc.edition == "01.01.2026"


def test_empty_spa_shell_is_rejected_with_guidance(tmp_path: Path) -> None:
    path = tmp_path / "Z950002444_.html"
    path.write_text(SPA_SHELL, encoding="utf-8")

    with pytest.raises(UnusableDocumentError) as excinfo:
        parse_file(path)
    assert "shell" in str(excinfo.value)


def test_unsupported_type_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "act.pdf"
    path.write_bytes(b"%PDF-1.4")
    with pytest.raises(UnusableDocumentError, match="unsupported type"):
        parse_file(path)


def test_identical_content_hashes_identically(tmp_path: Path) -> None:
    body = "Статья 1. " + "текст закона. " * 100
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text(body, encoding="utf-8")
    docs, problems = parse_directory(tmp_path)
    assert problems == []
    assert docs[0].content_hash == docs[1].content_hash


def test_directory_reports_problems_instead_of_swallowing(tmp_path: Path) -> None:
    (tmp_path / "good.txt").write_text("Статья 1. " + "текст. " * 200, encoding="utf-8")
    (tmp_path / "bad.html").write_text(SPA_SHELL, encoding="utf-8")

    docs, problems = parse_directory(tmp_path)
    assert len(docs) == 1
    assert len(problems) == 1


def test_kazakh_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "kz.txt"
    path.write_text("Банктің жарғылық капиталы туралы ереже. " * 40, encoding="utf-8")
    assert parse_file(path).language == "kk"
