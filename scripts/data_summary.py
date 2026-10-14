import sys

from fin_assistant.storage.db import fetch_all

NUMBERS_SQL = """
SELECT d.source, count(*) AS n_values, count(DISTINCT m.metric) AS n_metrics,
       count(DISTINCT m.bin) AS n_entities, min(m.period) AS first, max(m.period) AS last
FROM metrics m JOIN documents d ON d.id = m.source_doc
GROUP BY d.source ORDER BY n_values DESC
"""

TEXT_SQL = """
SELECT d.source, count(DISTINCT d.id) AS n_docs, count(c.id) AS n_chunks,
       min(d.published_at) AS first, max(d.published_at) AS last
FROM documents d JOIN chunks c ON c.document_id = d.id
GROUP BY d.source ORDER BY n_chunks DESC
"""


def main() -> int:
    print("\nNUMBERS (table `metrics`, answered by exact lookup)\n")
    print(f"  {'source':<12} {'values':>9} {'metrics':>8} {'entities':>9}  period")
    for r in fetch_all(NUMBERS_SQL):
        print(
            f"  {r['source']:<12} {r['n_values']:>9,} {r['n_metrics']:>8} {r['n_entities']:>9}  "
            f"{r['first']} .. {r['last']}"
        )
    print("\nTEXT (table `chunks`, answered by search + LLM)\n")
    print(f"  {'source':<12} {'documents':>9} {'chunks':>9}  published")
    for r in fetch_all(TEXT_SQL):
        print(f"  {r['source']:<12} {r['n_docs']:>9,} {r['n_chunks']:>9,}  {r['first']} .. {r['last']}")
    n = fetch_all("SELECT count(*) AS n FROM metric_info")[0]["n"]
    print(f"\n  metric_info: {n} metrics explained in plain words\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
