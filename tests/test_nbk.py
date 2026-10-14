from datetime import date
from pathlib import Path

import pytest

from fin_assistant.ingest.nbk import FeedError, feed_fingerprint, parse_feed

FEED = (Path(__file__).parent / "fixtures" / "nbk_rates.xml").read_bytes()


def by_code(rates, code):
    return next(r for r in rates if r.code == code)


def test_rate_quoted_per_one_is_unchanged() -> None:
    assert by_code(parse_feed(FEED), "USD").tenge_per_unit == pytest.approx(447.85)


def test_rate_quoted_per_ten_is_divided() -> None:
    amd = by_code(parse_feed(FEED), "AMD")
    assert amd.quoted_value == pytest.approx(12.41)
    assert amd.quoted_per == 10
    assert amd.tenge_per_unit == pytest.approx(1.241)


def test_rate_quoted_per_hundred_is_divided() -> None:
    assert by_code(parse_feed(FEED), "KRW").tenge_per_unit == pytest.approx(0.3304)


def test_extreme_denominator_survives() -> None:
    assert by_code(parse_feed(FEED), "IRR").tenge_per_unit == pytest.approx(0.000326)


def test_original_quote_is_kept_for_audit() -> None:
    amd = by_code(parse_feed(FEED), "AMD")
    assert amd.quoted_value / amd.quoted_per == pytest.approx(amd.tenge_per_unit)


def test_unparseable_value_is_skipped_not_fatal() -> None:
    codes = {r.code for r in parse_feed(FEED)}
    assert "BAD" not in codes
    assert "USD" in codes


def test_zero_rate_is_rejected() -> None:
    assert "ZER" not in {r.code for r in parse_feed(FEED)}


def test_non_currency_titles_are_ignored() -> None:
    assert "EURO" not in {r.code for r in parse_feed(FEED)}


def test_malformed_xml_is_an_error() -> None:
    with pytest.raises(FeedError, match="not valid XML"):
        parse_feed(b"<rss><channel><item>")


def test_feed_with_no_usable_rates_is_an_error() -> None:
    empty = b'<?xml version="1.0"?><rss><channel></channel></rss>'
    with pytest.raises(FeedError, match="no usable rates"):
        parse_feed(empty)


def test_date_is_parsed_from_dd_mm_yyyy() -> None:
    assert by_code(parse_feed(FEED), "USD").on_date == date(2026, 9, 23)


def test_metric_name_is_stable_and_lowercase() -> None:
    assert by_code(parse_feed(FEED), "USD").metric == "fx_rate_usd"


def test_unit_states_the_normalised_basis() -> None:
    assert by_code(parse_feed(FEED), "AMD").unit == "KZT per 1 AMD"


def test_same_rates_hash_the_same() -> None:
    assert feed_fingerprint(parse_feed(FEED)) == feed_fingerprint(parse_feed(FEED))


def test_fingerprint_ignores_order() -> None:
    rates = parse_feed(FEED)
    assert feed_fingerprint(rates) == feed_fingerprint(list(reversed(rates)))


def test_changed_rate_changes_the_hash() -> None:
    changed = FEED.replace(b"447.85", b"448.00")
    assert feed_fingerprint(parse_feed(FEED)) != feed_fingerprint(parse_feed(changed))
