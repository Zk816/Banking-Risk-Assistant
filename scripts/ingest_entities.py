import argparse
import sys

from fin_assistant.ingest.dfo import SOURCE, DfoError, Organisation, search_many
from fin_assistant.logging_setup import get_logger, setup_logging
from fin_assistant.storage.db import connect

log = get_logger(__name__)

DEFAULT_QUERIES = ["банк", "bank"]

UPSERT_SQL = """
INSERT INTO entities (bin, name_ru, type)
VALUES (%s, %s, %s)
ON CONFLICT (bin) DO UPDATE
SET name_ru = EXCLUDED.name_ru, type = EXCLUDED.type
"""


def store(organisations: list[Organisation]) -> int:
    with connect() as conn, conn.cursor() as cur:
        for organisation in organisations:
            cur.execute(UPSERT_SQL, (organisation.bin, organisation.name_ru, SOURCE))
    return len(organisations)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest entities from opi.dfo.kz.")
    parser.add_argument(
        "--query", action="append", dest="queries", help="Search term; repeatable. Defaults to банк/bank."
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch and parse, write nothing.")
    args = parser.parse_args()
    setup_logging()

    queries = args.queries or DEFAULT_QUERIES
    print(f"\n  поиск по: {', '.join(queries)}")

    try:
        organisations = search_many(queries)
    except DfoError as e:
        raise SystemExit(f"\n  {e}\n") from e

    if not organisations:
        raise SystemExit("\n  ничего не найдено\n")

    banks = [o for o in organisations if o.is_bank]
    print(f"  найдено {len(organisations)} организаций, из них похожи на банк: {len(banks)}\n")
    for organisation in organisations:
        print(f"    {organisation.bin}  {organisation.name_ru[:62]}")

    if args.dry_run:
        print("\n  --dry-run: ничего не записано\n")
        return 0

    written = store(organisations)
    log.info("dfo stored %d entities", written)
    print(f"\n  записано {written} в entities\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
