import random

from common import DATA, SMOKE, is_english, parallel_map, parse_json, split_of, teacher, write_jsonl

from fin_assistant.storage.db import connect

SOURCES = [
    "laws",
    "nbk_regulations",
    "company_reports",
    "nbk_publications",
    "stability_reports",
    "basel_standards",
    "deposit_market",
    "worldbank_reports",
    "us_bank_filings",
    "web_official",
]
TOTAL = 60 if SMOKE else 7000
PER_SOURCE_CAP = 10 if SMOKE else 1200

SYSTEM = """You write test questions for a search system used by bank risk analysts in Kazakhstan.
Given one passage, write ONE question that this passage answers specifically.

Rules:
- Language: Russian if the passage is in Russian or Kazakh; English if the passage is in English.
- The question must make sense to someone who has NOT seen the passage: never say "the passage",
  "this document", "данный документ", "в тексте". Name the bank, law, standard or period when the
  answer depends on it.
- Ask what an analyst would really ask (a requirement, a figure, a definition, a condition, a risk),
  not trivia about wording.
- Then give a short answer (1-3 sentences) using only the passage; copy numbers exactly.
- If the passage is a table of contents, boilerplate, a list of names, a fragment with no fact, or
  unreadable, set "skip": true.

Reply with JSON only: {"skip": false, "question": "...", "answer": "..."}"""


def sample_chunks() -> list[dict]:
    rows: list[dict] = []
    with connect() as conn:
        conn.execute("SELECT setseed(0.42)")
        for source in SOURCES:
            cur = conn.execute(
                """SELECT c.id AS chunk_id, c.document_id, c.ordinal, c.text, d.source, d.title
                   FROM chunks c JOIN documents d ON d.id = c.document_id
                   WHERE d.source = %s AND length(c.text) BETWEEN 400 AND 2500
                   ORDER BY random() LIMIT %s""",
                (source, PER_SOURCE_CAP),
            )
            got = [dict(r) for r in cur.fetchall()]
            print(f"  {source}: {len(got)} passages", flush=True)
            rows += got
    rows = [r for r in rows if sum(ch.isdigit() for ch in r["text"]) / len(r["text"]) < 0.25]
    random.Random(0).shuffle(rows)
    return rows[:TOTAL]


def ask(row: dict) -> dict | None:
    reply = teacher(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Source: {row['title']}\n\nPassage:\n{row['text']}"},
        ],
        max_tokens=400,
    )
    parsed = parse_json(reply)
    if not parsed or parsed.get("skip") or not parsed.get("question") or not parsed.get("answer"):
        return None
    q = str(parsed["question"]).strip()
    if len(q) < 15 or any(w in q.lower() for w in ("passage", "фрагмент", "данном тексте", "в тексте")):
        return None
    return {
        **row,
        "question": q,
        "answer": str(parsed["answer"]).strip(),
        "lang": "en" if is_english(q) else "ru",
        "split": split_of(row["document_id"]),
    }


def main() -> None:
    rows = sample_chunks()
    print(f"sampled {len(rows)} passages", flush=True)
    out = [r for r in parallel_map(ask, rows, label="questions") if r]
    n = write_jsonl(DATA / "qa.jsonl", out)
    test = sum(r["split"] == "test" for r in out)
    print(f"wrote {n} questions ({n - test} train / {test} test) to {DATA / 'qa.jsonl'}")


if __name__ == "__main__":
    main()
