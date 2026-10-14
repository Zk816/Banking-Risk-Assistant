import hashlib
import sys

from fin_assistant.config import settings
from fin_assistant.ingest import kase
from fin_assistant.ingest.kdif import SOURCE, URL, KdifError, fetch_page, parse_ratings
from fin_assistant.ingest.nbk_stats import KASE_CODE, canonical_bank
from fin_assistant.storage.db import INSERT_DOCUMENT_SQL, connect, fetch_all


def main() -> int:
    try:
        page = fetch_page()
    except KdifError as e:
        raise SystemExit(f"  {e}") from e
    raw = settings.raw_dir / "bank_ratings"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "banks-rating.html").write_text(page, encoding="utf-8")
    ratings = parse_ratings(page)
    known = {
        r["name_ru"]: r["bin"] for r in fetch_all("SELECT bin, name_ru FROM entities WHERE type = 'nbk_bank'")
    }
    kase_bins = {
        r["name_ru"]: r["bin"]
        for r in fetch_all("SELECT bin, name_ru FROM entities WHERE type = 'bank_balance_sheets'")
    }
    known.update(
        {
            canon: kase_bins[kase.BANKS[code]]
            for canon, code in KASE_CODE.items()
            if kase.BANKS[code] in kase_bins
        }
    )
    digest = hashlib.sha256(page.encode("utf-8")).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            INSERT_DOCUMENT_SQL,
            (
                SOURCE,
                "КФГД: рейтинги банков-участников",
                URL,
                "ru",
                None,
                max(r.on_date for r in ratings if r.on_date),
                digest,
                str(raw / "banks-rating.html"),
            ),
        )
        row = (
            cur.fetchone()
            or fetch_all("SELECT id FROM documents WHERE source=%s AND content_hash=%s", (SOURCE, digest))[0]
        )
        for r in ratings:
            canon = canonical_bank(r.bank)
            bin_ = known.get(canon, f"NBK:{canon}")
            cur.execute(
                "INSERT INTO entities (bin, name_ru, type) VALUES (%s, %s, 'nbk_bank') "
                "ON CONFLICT (bin) DO NOTHING",
                (bin_, canon),
            )
            cur.execute(
                "INSERT INTO ratings (bin, agency, rating, previous, outlook, rated_on, source_doc) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (bin, agency, rated_on) DO UPDATE "
                "SET rating = EXCLUDED.rating, outlook = EXCLUDED.outlook",
                (bin_, r.agency, r.rating, r.previous, r.outlook, r.on_date, row["id"]),
            )
            print(f"  {canon:<28} {r.agency:<8} {r.rating:<6} {r.outlook or '':<12} {r.on_date}")
    print(f"\n  stored {len(ratings)} ratings\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
