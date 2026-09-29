"""Load company financials from SEC EDGAR into `entities` and `metrics`.

The tables were designed for Kazakh banks keyed by BIN. A CIK plays exactly the
same role — a registry identifier that survives name changes — so it goes in the
same column. The alternative, a second parallel schema, would mean every query
and every tool learns which of two tables to read.

Run: uv run python scripts/ingest_edgar.py
     uv run python scripts/ingest_edgar.py --cik 19617 --dry-run
"""

import argparse
import sys
from datetime import date

from kzbank.ingest.edgar import SOURCE, CompanyFact, EdgarError, extract_facts, fetch_company_facts
from kzbank.logging_setup import get_logger, setup_logging
from kzbank.storage.db import INSERT_DOCUMENT_SQL, INSERT_METRIC_SQL, connect

log = get_logger(__name__)

# A starter set of large US banks. CIK is stable; tickers and names are not.
DEFAULT_BANKS: dict[int, str] = {
    19617: "JPMorgan Chase & Co",
    70858: "Bank of America Corp",
    72971: "Wells Fargo & Co",
    831001: "Citigroup Inc",
    713676: "PNC Financial Services Group",
    36104: "U.S. Bancorp",
    1390777: "Truist Financial Corp",
    40729: "Ally Financial Inc",
    92230: "Truist Bank predecessor (BB&T)",
    927628: "Capital One Financial Corp",
    1075706: "Regions Financial Corp",
    35527: "Fifth Third Bancorp",
    49196: "Huntington Bancshares",
    91576: "KeyCorp",
    310522: "Fannie Mae",
    895421: "Morgan Stanley",
    886982: "Goldman Sachs Group",
    1393612: "Discover Financial Services",
    719739: "SVB Financial Group",
    1601712: "Synchrony Financial",
    # Second wave: regional and custody banks, so the corpus covers the size
    # range that capital rules actually bite on, not only the six giants.
    93751: "State Street Corp",
    73124: "Northern Trust Corp",
    316709: "Charles Schwab Corp",
    4962: "American Express Co",
    36270: "M&T Bank Corp",
    759944: "Citizens Financial Group",
    798941: "First Citizens BancShares",
    28412: "Comerica Inc",
    109380: "Zions Bancorporation",
    763901: "Popular Inc",
    1069157: "East West Bancorp",
    1212545: "Western Alliance Bancorporation",
    801337: "Webster Financial Corp",
    39263: "Cullen/Frost Bankers",
    1015328: "Wintrust Financial",
    910073: "Flagstar Financial (NYCB)",
    875357: "BOK Financial",
    18349: "Synovus Financial",
    36966: "First Horizon Corp",
    720005: "Raymond James Financial",
    811830: "Santander Holdings USA",
    714310: "Valley National Bancorp",
    101382: "UMB Financial",
    707179: "Old National Bancorp",
    37808: "F.N.B. Corp",
    1115055: "Pinnacle Financial Partners",
    1068851: "Prosperity Bancshares",
    887343: "Columbia Banking System",
    7789: "Associated Banc-Corp",
    750577: "Hancock Whitney Corp",
}

# Where the API's entity name is not the company the figures belong to. For CIK
# 70858 SEC reports "BofA Finance LLC", a co-registrant; the facts are Bank of
# America Corp's consolidated statements (assets ~$3.3 trillion).
NAME_OVERRIDES: dict[str, str] = {"CIK0000070858": "BANK OF AMERICA CORP"}

UPSERT_ENTITY_SQL = """
INSERT INTO entities (bin, name_en, type)
VALUES (%s, %s, %s)
ON CONFLICT (bin) DO UPDATE SET name_en = EXCLUDED.name_en, type = EXCLUDED.type
"""


def store(facts: list[CompanyFact]) -> tuple[int, int]:
    """Store one company's facts. Returns (entity_rows, metric_rows).

    Each filing becomes a `documents` row, so every figure points at the form it
    came from — the same provenance rule the NBK rates follow.
    """
    if not facts:
        return 0, 0

    cik = facts[0].cik
    company = NAME_OVERRIDES.get(cik, facts[0].company)
    written = 0

    with connect() as conn, conn.cursor() as cur:
        cur.execute(UPSERT_ENTITY_SQL, (cik, company, SOURCE))

        # One document per accession number, created on first use.
        documents: dict[str, int] = {}
        for fact in facts:
            document_id = documents.get(fact.accession)
            if document_id is None:
                cur.execute(
                    INSERT_DOCUMENT_SQL,
                    (SOURCE, f"{company} {fact.form} {fact.fiscal_period} {fact.fiscal_year}",
                     f"https://www.sec.gov/Archives/edgar/data/{int(cik[3:])}/"
                     f"{fact.accession.replace('-', '')}/",
                     "en", fact.form, fact.filed, fact.accession, None),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute("SELECT id FROM documents WHERE source=%s AND content_hash=%s",
                                (SOURCE, fact.accession))
                    row = cur.fetchone()
                document_id = int(row["id"])
                documents[fact.accession] = document_id

            cur.execute(
                INSERT_METRIC_SQL,
                (cik, fact.concept, fact.period_end, fact.value, fact.unit,
                 document_id, fact.filed),
            )
            written += 1

    return 1, written


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest SEC EDGAR financials.")
    parser.add_argument("--cik", type=int, action="append", dest="ciks",
                        help="CIK to load; repeatable. Defaults to six large US banks.")
    parser.add_argument("--since", type=int, default=2020,
                        help="Earliest fiscal year to keep.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    setup_logging()

    ciks = args.ciks or list(DEFAULT_BANKS)
    print(f"\n  {len(ciks)} компаний, с {args.since} года\n")

    total_entities = total_metrics = 0
    for cik in ciks:
        try:
            facts = extract_facts(fetch_company_facts(cik), since=args.since)
        except EdgarError as e:
            print(f"    ОШИБКА cik={cik}: {e}")
            continue

        if not facts:
            print(f"    cik={cik}: ни одного подходящего факта")
            continue

        name = facts[0].company
        concepts = sorted({f.concept for f in facts})
        periods = sorted({f.period_end for f in facts})
        print(f"    {name[:40]:<40} фактов={len(facts):>4} "
              f"показателей={len(concepts):>2} {periods[0]}…{periods[-1]}")

        if not args.dry_run:
            e, m = store(facts)
            total_entities += e
            total_metrics += m

    if args.dry_run:
        print("\n  --dry-run: ничего не записано\n")
        return 0

    log.info("edgar stored entities=%d metrics=%d", total_entities, total_metrics)
    print(f"\n  записано: {total_entities} компаний, {total_metrics} показателей\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
