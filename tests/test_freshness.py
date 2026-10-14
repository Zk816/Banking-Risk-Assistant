from datetime import date, datetime, timedelta, timezone

from fin_assistant.storage.freshness import (
    DEFAULT_POLICY,
    SOURCE_POLICY,
    SourceFreshness,
    age_of,
)


def series(published_days_ago: int | None, max_age: int = 2) -> SourceFreshness:
    published = None if published_days_ago is None else date.today() - timedelta(days=published_days_ago)
    return SourceFreshness(
        source="nbk",
        latest_published=published,
        latest_retrieved=datetime.now(timezone.utc),
        documents=1 if published else 0,
        kind="series",
        max_age_days=max_age,
    )


def reference(checked_days_ago: int, max_age: int = 90) -> SourceFreshness:
    return SourceFreshness(
        source="adilet",
        latest_published=date(1995, 8, 31),
        latest_retrieved=datetime.now(timezone.utc) - timedelta(days=checked_days_ago),
        documents=1,
        kind="reference",
        max_age_days=max_age,
    )


def test_todays_data_is_fresh() -> None:
    assert not series(0).stale


def test_yesterdays_data_is_still_fresh_over_a_weekend() -> None:
    assert not series(2).stale


def test_three_day_old_rates_are_stale() -> None:
    assert series(3).stale


def test_an_old_law_checked_recently_is_fresh() -> None:
    fresh = reference(checked_days_ago=2)
    assert not fresh.stale
    assert fresh.latest_published == date(1995, 8, 31)


def test_a_law_not_checked_in_a_year_is_stale() -> None:
    assert reference(checked_days_ago=400).stale


def test_reference_age_ignores_publication_date() -> None:
    assert reference(checked_days_ago=5).age_days == 5


def test_a_source_with_no_data_is_stale() -> None:
    empty = series(None)
    assert empty.stale
    assert empty.age_days is None
    assert empty.detail == "нет данных"


def test_every_configured_source_has_a_kind_and_a_threshold() -> None:
    for source, (kind, max_age) in SOURCE_POLICY.items():
        assert kind in {"series", "reference"}, source
        assert max_age > 0, source


def test_unknown_source_falls_back_to_a_policy() -> None:
    kind, max_age = DEFAULT_POLICY
    assert kind in {"series", "reference"} and max_age > 0


def test_age_of_counts_days() -> None:
    assert age_of(date.today()) == 0
    assert age_of(date.today() - timedelta(days=7)) == 7


def test_future_period_has_negative_age() -> None:
    assert age_of(date.today() + timedelta(days=1)) == -1


def test_dateless_lookup_reports_age(monkeypatch) -> None:
    from fin_assistant.tools import bank_metrics

    row = {
        "value": 447.85,
        "unit": "KZT per 1 USD",
        "period": date.today() - timedelta(days=5),
        "source_title": "НБРК",
        "source_url": "https://x",
    }
    monkeypatch.setattr(bank_metrics, "fetch_one", lambda sql, args: row)
    monkeypatch.setattr(bank_metrics, "_MAX_RATE_AGE_DAYS", 2)

    result = bank_metrics.get_exchange_rate("USD")
    assert result["age_days"] == 5
    assert result["stale"] is True
    assert "укажи дату" in result["warning"]


def test_explicit_date_is_never_marked_stale(monkeypatch) -> None:
    from fin_assistant.tools import bank_metrics

    row = {
        "value": 447.85,
        "unit": "KZT per 1 USD",
        "period": date(2020, 1, 1),
        "source_title": "НБРК",
        "source_url": "https://x",
    }
    monkeypatch.setattr(bank_metrics, "fetch_one", lambda sql, args: row)

    result = bank_metrics.get_exchange_rate("USD", "2020-01-01")
    assert "stale" not in result
