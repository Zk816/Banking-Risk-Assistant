import hashlib
import sys
import time

import psycopg

from fin_assistant.config import settings
from fin_assistant.ingest import kase
from fin_assistant.ingest.nbk_stats import (
    BASE,
    KASE_CODE,
    MARGIN_COLUMNS,
    SOURCE,
    NbkStatsError,
    canonical_bank,
    download,
    list_files,
    parse_balances,
    parse_capital_assets,
    parse_margin,
    parse_prudential,
)
from fin_assistant.logging_setup import get_logger, setup_logging
from fin_assistant.storage.db import (
    INSERT_DOCUMENT_SQL,
    INSERT_METRIC_SQL,
    UPSERT_METRIC_INFO_SQL,
    connect,
    fetch_all,
)

log = get_logger(__name__)

KEEP = {"reg_capital", "k1", "k1_1", "k1_2", "k2", "k4"}
PARSERS = {
    "prudential": parse_prudential,
    "capital_assets": parse_capital_assets,
    "margin": parse_margin,
    "balances": lambda path: parse_balances(path)[0],
}

METRIC_INFO: dict[str, tuple[str, str, str]] = {
    "nbk_assets": ("Активы", "Total assets", "KZT"),
    "nbk_loans": ("Ссудный портфель", "Loan portfolio (gross)", "KZT"),
    "nbk_npl90": ("Кредиты с просрочкой свыше 90 дней", "Loans overdue more than 90 days (NPL 90+)", "KZT"),
    "nbk_overdue30": ("Кредиты с просрочкой свыше 30 дней", "Loans overdue more than 30 days", "KZT"),
    "nbk_overdue7": ("Кредиты с просрочкой свыше 7 дней", "Loans overdue more than 7 days", "KZT"),
    "nbk_overdue_any": ("Кредиты с просрочкой платежей", "Loans with any overdue payment", "KZT"),
    "nbk_stage3": ("Кредиты 3 стадии и ПСКО", "Stage 3 (credit-impaired) loans under IFRS 9", "KZT"),
    "nbk_provisions": ("Провизии", "Loan loss provisions", "KZT"),
    "nbk_liabilities": ("Обязательства", "Total liabilities", "KZT"),
    "nbk_equity": ("Собственный капитал (баланс)", "Equity on the balance sheet", "KZT"),
    "nbk_deposits_individuals": ("Вклады физических лиц", "Deposits of individuals", "KZT"),
    "nbk_deposits_legal": ("Вклады юридических лиц", "Deposits of companies", "KZT"),
    "nbk_deposits": ("Вклады, всего", "Deposits, total (individuals + companies)", "KZT"),
    "nbk_net_income_ytd": ("Чистый доход с начала года", "Net income since 1 January", "KZT"),
    "reg_capital": ("Собственный капитал (регуляторный)", "Regulatory capital", "KZT"),
    "k1": ("Коэффициент достаточности основного капитала k1", "CET1 capital adequacy ratio", "ratio"),
    "k1_1": ("Коэффициент k1-1", "Core capital to risk-weighted assets (k1-1)", "ratio"),
    "k1_2": ("Коэффициент достаточности капитала 1 уровня k1-2", "Tier 1 capital adequacy ratio", "ratio"),
    "k2": ("Коэффициент достаточности собственного капитала k2", "Total capital adequacy ratio", "ratio"),
    "k4": ("Коэффициент текущей ликвидности k4", "Current liquidity ratio", "ratio"),
    **{m: (None, meaning, unit) for m, unit, meaning in MARGIN_COLUMNS.values()},
}
DB_RETRIES = 5


def entity_ids() -> dict[str, str]:
    kase_bins = {
        r["name_ru"]: r["bin"]
        for r in fetch_all("SELECT bin, name_ru FROM entities WHERE type = 'bank_balance_sheets'")
    }
    return {
        canon: kase_bins[kase.BANKS[code]]
        for canon, code in KASE_CODE.items()
        if kase.BANKS[code] in kase_bins
    }


def store(file_title: str, file_id: str, kind: str, path, rows) -> int:
    ids = entity_ids()
    content_hash = hashlib.md5(path.read_bytes()).hexdigest()
    for attempt in range(1, DB_RETRIES + 1):
        try:
            with connect() as conn, conn.cursor() as cur:
                cur.execute(
                    INSERT_DOCUMENT_SQL,
                    (
                        SOURCE,
                        file_title,
                        f"{BASE}/file/download/{file_id}",
                        "ru",
                        kind,
                        max(r.period for r in rows),
                        content_hash,
                        str(path),
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        "SELECT id FROM documents WHERE source = %s AND content_hash = %s",
                        (SOURCE, content_hash),
                    )
                    row = cur.fetchone()
                doc_id = row["id"]
                kept = [r for r in rows if r.metric in KEEP or r.metric.startswith("nbk_")]
                bins = {
                    r.bank: ids.get(canonical_bank(r.bank), f"NBK:{canonical_bank(r.bank)}") for r in kept
                }
                cur.executemany(
                    "INSERT INTO entities (bin, name_ru, type) VALUES (%s, %s, 'nbk_bank') "
                    "ON CONFLICT (bin) DO NOTHING",
                    [(b, canonical_bank(bank)) for bank, b in bins.items()],
                )
                cur.executemany(
                    INSERT_METRIC_SQL,
                    [(bins[r.bank], r.metric, r.period, r.value, r.unit, doc_id, None) for r in kept],
                )
                return len(kept)
        except psycopg.OperationalError as e:
            if attempt == DB_RETRIES:
                raise
            log.warning("db connection failed (attempt %d): %s", attempt, e)
            time.sleep(10 * attempt)
    return 0


def store_metric_info(raw) -> None:
    rows = [(m, ru, en, unit, SOURCE, None) for m, (ru, en, unit) in METRIC_INFO.items()]
    newest = sorted(raw.glob("balances_*"), key=lambda p: int(p.stem.split("_")[-1]))
    if newest:
        _, names = parse_balances(newest[-1])
        rows += [
            (
                m,
                name,
                None,
                "KZT",
                SOURCE,
                f"Balance of account group {m[-4:]} of the banking chart of accounts",
            )
            for m, name in names.items()
        ]
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(UPSERT_METRIC_INFO_SQL, rows)


def main() -> int:
    setup_logging()
    raw = settings.raw_dir / "bank_statistics"
    if "--download-all" in sys.argv:
        files = [f for f in list_files() if f.kind]
        for f in files:
            try:
                print(f"  {download(f, raw).name}")
            except NbkStatsError as e:
                print(f"  skip {f.file_id}: {e}")
        return 0
    kinds = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--kind=")] or list(PARSERS)
    store_metric_info(raw)
    files = [f for f in list_files() if f.kind in kinds]
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
