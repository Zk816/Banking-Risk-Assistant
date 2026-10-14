import hashlib
import sys
import time

from fin_assistant.ingest.nbk_policy import INDEX, SOURCE, parse_decisions, rubric_ids
from fin_assistant.ingest.nbk_stats import _get
from fin_assistant.logging_setup import setup_logging
from fin_assistant.storage.db import INSERT_DOCUMENT_SQL, INSERT_METRIC_SQL, connect

PAGE_DELAY_S = 1.5


def main() -> int:
    setup_logging()
    total = 0
    for rubric in rubric_ids():
        url = f"{INDEX}/rubrics/{rubric}"
        decisions = parse_decisions(_get(url))
        time.sleep(PAGE_DELAY_S)
        if not decisions:
            continue
        fingerprint = hashlib.sha256(
            "|".join(f"{d.on_date}={d.rate}" for d in decisions).encode()
        ).hexdigest()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                INSERT_DOCUMENT_SQL,
                (
                    SOURCE,
                    f"Решения по базовой ставке ({decisions[0].on_date.year})",
                    url,
                    "ru",
                    None,
                    max(d.on_date for d in decisions),
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
            for d in decisions:
                cur.execute(
                    INSERT_METRIC_SQL, (None, "base_rate", d.on_date, d.rate, "%", row["id"], d.on_date)
                )
        total += len(decisions)
        print(f"  {decisions[0].on_date.year}: {len(decisions)} decisions")
    print(f"\n  stored {total} base-rate decisions\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
