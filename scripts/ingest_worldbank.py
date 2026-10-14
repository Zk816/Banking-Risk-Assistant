import hashlib
import sys
import time

from fin_assistant.ingest.worldbank import COUNTRIES, INDICATORS, SOURCE, WorldBankError, fetch, period
from fin_assistant.logging_setup import setup_logging
from fin_assistant.storage.db import INSERT_DOCUMENT_SQL, INSERT_METRIC_SQL, connect


def main() -> int:
    setup_logging()
    with connect() as conn, conn.cursor() as cur:
        for iso, name in COUNTRIES.items():
            cur.execute(
                "INSERT INTO entities (bin, name_en, type) VALUES (%s, %s, 'country') "
                "ON CONFLICT (bin) DO NOTHING",
                (f"WB:{iso}", name),
            )
    total = 0
    for code, (metric, _, meaning) in INDICATORS.items():
        try:
            values = fetch(code)
        except WorldBankError as e:
            print(f"  skip {code}: {e}")
            continue
        time.sleep(1.0)
        if not values:
            print(f"  {code:<20} no data")
            continue
        fingerprint = hashlib.sha256(
            "|".join(f"{v.country}{v.year}={v.value}" for v in values).encode()
        ).hexdigest()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                INSERT_DOCUMENT_SQL,
                (
                    SOURCE,
                    f"World Bank: {meaning} ({code})",
                    f"https://data.worldbank.org/indicator/{code}",
                    "en",
                    None,
                    period(max(v.year for v in values)),
                    fingerprint,
                    None,
                ),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "SELECT id FROM documents WHERE source = %s AND content_hash = %s", (SOURCE, fingerprint)
                )
                row = cur.fetchone()
            for v in values:
                cur.execute(
                    INSERT_METRIC_SQL,
                    (f"WB:{v.country}", v.metric, period(v.year), v.value, v.unit, row["id"], period(v.year)),
                )
        kz = sorted((v for v in values if v.country == "KAZ"), key=lambda v: v.year)
        last = f"KZ {kz[-1].year}: {kz[-1].value:.2f}" if kz else "no KZ"
        print(f"  {metric:<32} {len(values):>5} values  {last}")
        total += len(values)
    print(f"\n  stored {total} values\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
