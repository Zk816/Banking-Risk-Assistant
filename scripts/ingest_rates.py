"""Fetch official exchange rates from the National Bank and store them.

This is the *numbers* path, the other half of the system: values go into SQL
rows, not into a vector index. Nothing here embeds anything.

Run: uv run python scripts/ingest_rates.py
     uv run python scripts/ingest_rates.py --dry-run
"""

import argparse
import sys
import time
from datetime import date, timedelta

from kzbank.ingest.nbk import FEED_URL, SOURCE, FeedError, feed_fingerprint, fetch_feed, parse_feed
from kzbank.logging_setup import get_logger, setup_logging
from kzbank.storage.db import INSERT_DOCUMENT_SQL, INSERT_METRIC_SQL, connect, fetch_all

ARCHIVE_DELAY_S = 2.0

log = get_logger(__name__)

TITLE = "Официальные курсы валют НБРК"


def store_rates(rates: list, *, fingerprint: str) -> tuple[int, int]:
    """Record the feed as a document, then every rate against it.

    Returns (document_id, rows_written). The feed is the provenance: each metric
    row points at the document it came from, so "where did this number come
    from" always has an answer.
    """
    on_date = rates[0].on_date

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            INSERT_DOCUMENT_SQL,
            (SOURCE, f"{TITLE} на {on_date:%d.%m.%Y}", FEED_URL, "ru", None,
             on_date, fingerprint, None),
        )
        row = cur.fetchone()
        if row is None:
            # Same content hash — this exact set of rates is already stored.
            return -1, 0
        document_id = int(row["id"])

        for rate in rates:
            cur.execute(
                INSERT_METRIC_SQL,
                (
                    None,               # bin: a currency belongs to no company
                    rate.metric,
                    rate.on_date,
                    rate.tenge_per_unit,
                    rate.unit,
                    document_id,
                    rate.on_date,
                ),
            )

    return document_id, len(rates)


def backfill_days(since: date, daily_days: int) -> list[date]:
    """Every day of the last `daily_days`, and the 1st of each month before that.

    Daily for 20 years is ~7,900 requests; the archive answers in 0.1 to 20 s,
    so that is more than a day of load on a public site. Monthly points are
    enough to see trends and devaluations; recent days answer "курс вчера".
    """
    today = date.today()
    daily_from = today - timedelta(days=daily_days)
    months = [date(y, m, 1) for y in range(since.year, daily_from.year + 1) for m in range(1, 13)
              if since <= date(y, m, 1) < daily_from]
    return months + [daily_from + timedelta(days=i) for i in range((today - daily_from).days + 1)]


def backfill(since: date, daily_days: int = 730) -> int:
    """Load history from the archive feed. Stored days are skipped, so a rerun resumes."""
    have = {r["period"] for r in fetch_all(
        "SELECT DISTINCT period FROM metrics WHERE bin IS NULL AND metric = 'fx_rate_usd'")}
    days = backfill_days(since, daily_days)
    todo = [d for d in days if d not in have]
    print(f"\n  {len(days)} days since {since}, {len(todo)} not stored yet\n")
    stored = failed = 0
    for i, day in enumerate(todo, 1):
        try:
            rates = parse_feed(fetch_feed(day))
        except FeedError as e:
            failed += 1
            log.warning("rates %s: %s", day, e)
        else:
            if store_rates(rates, fingerprint=feed_fingerprint(rates))[0] > 0:
                stored += 1
        if i % 250 == 0:
            print(f"  {i}/{len(todo)}  stored {stored}  failed {failed}", flush=True)
        time.sleep(ARCHIVE_DELAY_S)
    print(f"\n  stored {stored} days, failed {failed}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest NBK exchange rates.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and parse, but write nothing.")
    parser.add_argument("--since", type=date.fromisoformat, default=None,
                        help="Backfill history from this date (YYYY-MM-DD) from the archive feed.")
    args = parser.parse_args()
    setup_logging()
    if args.since:
        return backfill(args.since)

    try:
        rates = parse_feed(fetch_feed())
    except FeedError as e:
        raise SystemExit(f"\n  {e}\n") from e

    on_date = rates[0].on_date
    normalised = [r for r in rates if r.quoted_per != 1]

    print(f"\n  {len(rates)} курсов на {on_date:%d.%m.%Y}")
    print(f"  {len(normalised)} из них котируются не за 1 единицу и были пересчитаны:")
    for rate in normalised:
        print(f"      {rate.code}  {rate.quoted_value:>9.2f} за {rate.quoted_per:>3}"
              f"  ->  {rate.tenge_per_unit:.4f} за 1")

    if args.dry_run:
        print("\n  --dry-run: ничего не записано\n")
        return 0

    document_id, written = store_rates(rates, fingerprint=feed_fingerprint(rates))
    if document_id < 0:
        print("\n  эти курсы уже в базе, пропущено\n")
        return 0

    log.info("nbk stored doc_id=%s rows=%d date=%s", document_id, written, on_date)
    print(f"\n  записано {written} строк в metrics, источник = документ #{document_id}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
