import re
import sys
from pathlib import Path

from fin_assistant.config import settings
from fin_assistant.ingest import kase
from fin_assistant.storage.db import connect, fetch_all

SOURCE_TYPE = {
    "stability_reports": "stability_report",
    "basel_standards": "basel_standard",
    "nbk_regulations": "regulation",
    "laws": "law",
    "country_indicators": "indicator",
    "bank_statistics": "statistics",
    "bank_balance_sheets": "form_700n",
    "bank_ratings": "rating_table",
    "deposit_market": "deposit_market",
    "base_rate": "base_rate",
    "exchange_rates": "fx_rates",
    "us_bank_filings": "us_filing",
    "web_official": "web_page",
}
_KASE_FILE = re.compile(r"company_reports/([A-Z0-9_]+)/(audit|review|fs|rating)_")


def issuer_names() -> dict[str, tuple[str, bool]]:
    path = settings.raw_dir / "company_reports" / "issuers.tsv"
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            code, name, fin = (line.split("\t") + ["", "0"])[:3]
            out[code] = (name, fin == "1")
    return out


AUDITORS = [
    (re.compile(r"KPMG|КПМГ", re.I), "KPMG"),
    (re.compile(r"Deloitte|Делойт", re.I), "Deloitte"),
    (re.compile(r"Pricewaterhouse|ПрайсуотерхаусКуперс|\bPwC\b", re.I), "PwC"),
    (re.compile(r"Ernst\s*&\s*Young|Эрнст энд Янг|\bEY\b"), "EY"),
    (re.compile(r"Grant Thornton|Грант Торнтон", re.I), "Grant Thornton"),
    (re.compile(r"\bCrowe\b|\bКроу\b", re.I), "Crowe"),
    (re.compile(r"Moore Stephens", re.I), "Moore Stephens"),
    (re.compile(r"\bBDO\b", re.I), "BDO"),
    (re.compile(r"\bRSM\b", re.I), "RSM"),
    (re.compile(r"\bUHY\b", re.I), "UHY"),
    (re.compile(r"Mazars|Мазарс", re.I), "Mazars"),
    (re.compile(r"Baker Tilly|Бейкер Тилли", re.I), "Baker Tilly"),
]
AUDIT_PAGES = 12


def add_auditors() -> int:
    rows = fetch_all(
        """SELECT d.id, d.title, string_agg(c.text, ' ') AS head FROM documents d
                        JOIN chunks c ON c.document_id = d.id
                        WHERE d.source = 'company_reports' AND d.doc_type IN ('audit', 'review')
                          AND c.ordinal < %s AND d.title NOT LIKE '%%аудитор:%%'
                        GROUP BY d.id, d.title""",
        (AUDIT_PAGES,),
    )
    updates = []
    for r in rows:
        counts = {name: len(p.findall(r["head"])) for p, name in AUDITORS}
        best = max(counts, key=counts.get)
        if counts[best]:
            updates.append((f"{r['title']} — аудитор: {best}", r["id"]))
    with connect() as conn, conn.cursor() as cur:
        cur.executemany("UPDATE documents SET title = %s WHERE id = %s", updates)
    return len(updates)


def main() -> int:
    bank_bins = {
        r["name_ru"]: r["bin"]
        for r in fetch_all("SELECT bin, name_ru FROM entities WHERE type = 'bank_balance_sheets'")
    }
    names = issuer_names()
    docs = fetch_all("SELECT id, source, raw_path FROM documents")
    updates = []
    for d in docs:
        bin_, doc_type = None, SOURCE_TYPE.get(d["source"])
        raw = d["raw_path"] or ""
        if d["source"] == "company_reports" and (m := _KASE_FILE.search(raw)):
            code, doc_type = m.group(1), m.group(2)
            bin_ = bank_bins.get(kase.BANKS.get(code, ""), f"KASE:{code}")
        elif d["source"] == "nbk_publications":
            doc_type = Path(raw).parent.name
        elif d["source"] == "worldbank_reports":
            doc_type = "world_bank_report"
        updates.append((bin_, doc_type, d["id"]))
    with connect() as conn, conn.cursor() as cur:
        new_issuers = {b for b, _, _ in updates if b and b.startswith("KASE:")}
        cur.executemany(
            "INSERT INTO entities (bin, name_ru, type) VALUES (%s, %s, %s) ON CONFLICT (bin) DO NOTHING",
            [
                (
                    b,
                    names.get(b[5:], (b[5:], False))[0],
                    "kase_financial" if names.get(b[5:], ("", False))[1] else "kase_company",
                )
                for b in new_issuers
            ],
        )
        cur.executemany("UPDATE documents SET bin = %s, doc_type = %s WHERE id = %s", updates)
    linked = sum(1 for b, _, _ in updates if b)
    print(
        f"\n  {len(updates)} documents typed, {linked} linked to a company ({len(new_issuers)} new issuer entities)\n"
    )
    print(f"  auditor added to {add_auditors()} report titles\n")
    for r in fetch_all("SELECT doc_type, count(*) n FROM documents GROUP BY doc_type ORDER BY n DESC"):
        print(f"  {str(r['doc_type']):<45} {r['n']:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
