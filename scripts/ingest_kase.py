import argparse
import hashlib
import sys
import time

import psycopg

from fin_assistant.config import settings
from fin_assistant.ingest.kase import BANKS, SOURCE, KaseError, download, list_reports, parse_700n, to_metrics
from fin_assistant.logging_setup import get_logger, setup_logging
from fin_assistant.storage.db import INSERT_DOCUMENT_SQL, INSERT_METRIC_SQL, connect

log = get_logger(__name__)

VERIFIED_BINS: dict[str, str] = {
    "HSBK": "940140000385",
    "BERK": "930740000137",
    "ATBN": "980740000057",
    "HCBN": "930540000147",
    "TSBN": "920140000084",
    "MFKM": "061240001583",
}


def resolve_bin(code: str, page_bin: str | None) -> tuple[str, str]:
    known = VERIFIED_BINS.get(code)
    if page_bin and known and page_bin != known:
        raise KaseError(f"{code}: KASE page BIN {page_bin} != verified {known}")
    if page_bin:
        return page_bin, "KASE page"
    if known:
        return known, "dfo.kz registry, verified"
    return f"KASE:{code}", "KASE code, BIN not published"


UPSERT_ENTITY_SQL = """
INSERT INTO entities (bin, name_ru, type) VALUES (%s, %s, %s)
ON CONFLICT (bin) DO UPDATE SET name_ru = EXCLUDED.name_ru, type = EXCLUDED.type
"""


DB_RETRIES = 5
DB_RETRY_WAIT_S = 10


def store(bin_: str, name: str, report, path, metrics: dict[str, float], report_date) -> None:
    for attempt in range(1, DB_RETRIES + 1):
        try:
            return _store_once(bin_, name, report, path, metrics, report_date)
        except psycopg.OperationalError as e:
            if attempt == DB_RETRIES:
                raise
            log.warning("db connection failed (attempt %d/%d): %s", attempt, DB_RETRIES, e)
            time.sleep(DB_RETRY_WAIT_S * attempt)


def _store_once(bin_: str, name: str, report, path, metrics: dict[str, float], report_date) -> None:
    content_hash = hashlib.md5(path.read_bytes()).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(UPSERT_ENTITY_SQL, (bin_, name, SOURCE))
        cur.execute(
            INSERT_DOCUMENT_SQL,
            (
                SOURCE,
                f"{name}: {report.name}",
                report.url,
                "ru",
                "700-Н",
                report.published,
                content_hash,
                str(path),
            ),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "SELECT id FROM documents WHERE source = %s AND content_hash = %s", (SOURCE, content_hash)
            )
            row = cur.fetchone()
        for metric, value in metrics.items():
            cur.execute(
                INSERT_METRIC_SQL, (bin_, metric, report_date, value, "KZT", row["id"], report.published)
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", action="append", help="KASE issuer code; repeatable")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    setup_logging()
    raw = settings.raw_dir / "bank_balance_sheets"

    totals = {"files": 0, "downloaded": 0, "stored": 0, "skipped": 0}
    for code in args.bank or list(BANKS):
        name = BANKS.get(code, code)
        try:
            bin_, reports = list_reports(code)
        except KaseError as e:
            print(f"  {code}: ОШИБКА {e}")
            continue
        try:
            bin_, how = resolve_bin(code, bin_)
        except KaseError as e:
            print(f"  {code}: ОШИБКА {e}")
            continue
        xlsx = reports
        done = 0
        for report in xlsx:
            totals["files"] += 1
            try:
                path, fresh = download(report, raw)
                totals["downloaded"] += fresh
                balances = parse_700n(path)
            except KaseError as e:
                totals["skipped"] += 1
                log.warning("kase skip %s: %s", report.url, e)
                continue
            dates = {b.report_date for b in balances}
            if len(dates) != 1:
                totals["skipped"] += 1
                log.warning("kase skip %s: %d report dates in one file", report.url, len(dates))
                continue
            metrics = to_metrics(balances)
            if abs(metrics["assets"] - metrics["liabilities"] - metrics["equity"]) > 0.01 * abs(
                metrics["assets"]
            ):
                totals["skipped"] += 1
                log.warning("kase skip %s: assets != liabilities + equity", report.url)
                continue
            if not args.dry_run:
                store(bin_, name, report, path, metrics, dates.pop())
            done += 1
        totals["stored"] += done
        print(
            f"  {code:<5} {bin_:<14} ({how}) {name[:28]:<28} files={len(xlsx):>3} "
            f"загружено={done:>3} (всего 700-Н: {len(reports)})",
            flush=True,
        )

    print(
        f"\n  файлов {totals['files']}, скачано сейчас {totals['downloaded']}, "
        f"записано {totals['stored']}, пропущено {totals['skipped']}"
        f"{' (dry-run)' if args.dry_run else ''}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
