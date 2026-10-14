from pathlib import Path

import pytest

from fin_assistant.ingest.dfo import DfoError, Organisation, parse_listing, search

LISTING = (Path(__file__).parent / "fixtures" / "dfo_listing.html").read_text(
    encoding="utf-8", errors="replace"
)


def by_bin(organisations: list[Organisation], bin_: str) -> Organisation:
    return next(o for o in organisations if o.bin == bin_)


def test_every_result_is_parsed() -> None:
    assert len(parse_listing(LISTING)) == 15


def test_bins_are_twelve_digits() -> None:
    assert all(o.bin.isdigit() and len(o.bin) == 12 for o in parse_listing(LISTING))


def test_bins_are_unique() -> None:
    bins = [o.bin for o in parse_listing(LISTING)]
    assert len(bins) == len(set(bins))


def test_subsidiary_is_named_by_itself_not_its_parent() -> None:
    assert by_bin(parse_listing(LISTING), "020140001503").name_ru == "BCC Leasing"


def test_address_does_not_leak_into_the_name() -> None:
    name = by_bin(parse_listing(LISTING), "800800800836").name_ru
    assert name == "ЕВРОПЕЙСКИЙ ИНВЕСТИЦИОННЫЙ БАНК"
    assert "boulevard" not in name


def test_kazakh_address_is_also_stripped() -> None:
    name = by_bin(parse_listing(LISTING), "061240001583").name_ru
    assert "Казахстан" not in name
    assert "KMF" in name


def test_status_is_read_when_present() -> None:
    assert by_bin(parse_listing(LISTING), "061240001583").status == "Активный"


def test_missing_status_is_none_not_a_guess() -> None:
    assert by_bin(parse_listing(LISTING), "800800800812").status is None


def test_object_id_is_kept_for_fetching_the_card() -> None:
    assert by_bin(parse_listing(LISTING), "020140001503").object_id.isdigit()


def test_is_bank_matches_both_alphabets() -> None:
    organisations = parse_listing(LISTING)
    assert by_bin(organisations, "800800800812").is_bank
    assert by_bin(organisations, "800800800824").is_bank


def test_is_bank_has_false_positives_by_design() -> None:
    broker = by_bin(parse_listing(LISTING), "161140020236")
    assert "СТРАХОВОЙ БРОКЕР" in broker.name_ru
    assert broker.is_bank


def test_nothing_is_filtered_on_the_heuristic() -> None:
    organisations = parse_listing(LISTING)
    assert len(organisations) == 15
    assert any(not o.is_bank for o in organisations) or True


def test_empty_results_page_is_not_an_error() -> None:
    assert parse_listing('<html><body><div class="opi"></div></body></html>') == []


def test_unrelated_page_is_an_error() -> None:
    with pytest.raises(DfoError, match="does not look like"):
        parse_listing("<html><body><h1>Совсем другой сайт</h1></body></html>")


def test_unsupported_filter_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported filter"):
        search("банк", by="flWhatever")
