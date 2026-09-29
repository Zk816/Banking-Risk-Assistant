"""Load per-bank statistics from the National Bank's statistics section.

Two file kinds, one sheet per month, every bank in the country:
  prudential     - "Сведения о выполнении пруденциальных нормативов" (2007-2026):
                   regulatory capital, capital ratios k1/k1-1/k1-2/k2, liquidity k4
  capital_assets - "Сведения о собственном капитале, обязательствах и активах"
                   (2005-2026): assets, loans, overdue and NPL 90+, provisions,
                   deposits, equity, profit

Run: uv run python scripts/ingest_nbk_stats.py
"""

import hashlib
import sys
import time

import psycopg

from kzbank.config import settings
from kzbank.ingest import kase
from kzbank.ingest.nbk_stats import (
    BASE, KASE_CODE, SOURCE, NbkStatsError, canonical_bank, download, list_files,
    parse_capital_assets, parse_prudential,
)
from kzbank.logging_setup import get_logger, setup_logging
from kzbank.storage.db import INSERT_DOCUMENT_SQL, INSERT_METRIC_SQL, connect, fetch_all

log = get_logger(__name__)

# Capital and liquidity only. The same files also hold exposure limits (k3,
# k5-k9) and currency liquidity (k4-1..k4-6), left out until a rule uses them.
KEEP = {"reg_capital", "k1", "k1_1", "k1_2", "k2", "k4"}
PARSERS = {"prudential": parse_prudential, "capital_assets": parse_capital_assets}
DB_RETRIES = 5


def entity_ids() -> dict[str, str]:
    """Canonical bank name -> stored entity id, reusing the KASE entity where one exists."""
    kase_bins = {r["name_ru"]: r["bin"] for r in fetch_all("SELECT bin, name_ru FROM entities WHERE type = 'kase'")}
    return {canon: kase_bins[kase.BANKS[code]] for canon, code in KASE_CODE.items()
            if kase.BANKS[code] in kase_bins}


def store(file_title: str, file_id: str, kind: str, path, rows) -> int:
    ids = entity_ids()
    content_hash = hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324  identity, not security
    for attempt in range(1, DB_RETRIES + 1):
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(INSERT_DOCUMENT_SQL, (SOURCE, file_title, f"{BASE}/file/download/{file_id}",
                                                  "ru", kind, max(r.period for r in rows),
                                                  content_hash, str(path)))
                row = cur.fetchone()
                if row is None:
                    cur.execute("SELECT id FROM documents WHERE source = %s AND content_hash = %s",
                                (SOURCE, content_hash))
                    row = cur.fetchone()
                doc_id = row["id"]
                n = 0
                for r in rows:
                    if r.metric not in KEEP and not r.metric.startswith("nbk_"):
                        continue
                    canon = canonical_bank(r.bank)
                    bin_ = ids.get(canon, f"NBK:{canon}")
                    cur.execute("INSERT INTO entities (bin, name_ru, type) VALUES (%s, %s, 'nbk_bank') "
                                "ON CONFLICT (bin) DO NOTHING", (bin_, canon))
                    cur.execute(INSERT_METRIC_SQL, (bin_, r.metric, r.period, r.value, r.unit, doc_id, None))
                    n += 1
                return n
        except psycopg.OperationalError as e:
            if attempt == DB_RETRIES:
                raise
            log.warning("db connection failed (attempt %d): %s", attempt, e)
            time.sleep(10 * attempt)
    return 0


def main() -> int:
    setup_logging()
    raw = settings.raw_dir / "nbk_stats"
    files = [f for f in list_files() if f.kind in PARSERS]
    print(f"\n  {len(files)} files\n")
    total = 0
    for f in files:
        try:
            path = download(f, raw)
            rows = PARSERS[f.kind](path)
        except NbkStatsError as e:
            print(f"  skip {f.file_id}: {e}")
            continue
        n = store(f.title, f.file_id, f.kind, path, rows)
        dates = sorted({r.period for r in rows})
        print(f"  {path.name:<28} {dates[0]}..{dates[-1]}  {n:>5} values")
        total += n
    print(f"\n  stored {total} values\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
