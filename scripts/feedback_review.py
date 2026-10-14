import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from fin_assistant.agent.citations import extract_numbers
from fin_assistant.config import settings

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "eval" / "feedback" / "inbox.jsonl"
SEEN = ROOT / "eval" / "feedback" / "seen_ids.txt"
QUESTIONS = ROOT / "eval" / "feedback_questions.jsonl"
REFUSAL = re.compile(r"нет данных|не найден|нет информации|не могу|no data|not found|cannot", re.I)

FEEDBACK_SQL = """
SELECT f.id, f.value, f.comment, f."createdAt" AS at, a.output AS answer, a."threadId" AS thread,
       u.identifier AS user_name,
       (SELECT q.output FROM "Step" q
         WHERE q."threadId" = a."threadId" AND q.type = 'user_message' AND q."createdAt" <= a."createdAt"
         ORDER BY q."createdAt" DESC LIMIT 1) AS question
FROM "Feedback" f
JOIN "Step" a ON a.id = f."stepId"
LEFT JOIN "Thread" t ON t.id = a."threadId"
LEFT JOIN "User" u ON u.id = t."userId"
ORDER BY f."createdAt"
"""


def chat_db_url() -> str:
    return re.sub(r"/[^/]+$", "/chainlit", str(settings.database_url))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()] if path.exists() else []


def append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def answer_text(answer: str) -> str:
    return (answer or "").split("\n---\n")[0].strip()


def flags(answer: str) -> list[str]:
    out = []
    if REFUSAL.search(answer_text(answer)):
        out.append("refused")
    if "Источники данных" not in (answer or "") and "Документы" not in (answer or ""):
        out.append("no_source")
    return out


def collect(conn: psycopg.Connection) -> tuple[list[dict], list[dict], list[str]]:
    seen = set(SEEN.read_text().split()) if SEEN.exists() else set()
    rows = [r for r in conn.execute(FEEDBACK_SQL).fetchall() if r["id"] not in seen and r["question"]]
    drafts, likes = [], []
    for r in rows:
        base = {
            "id": f"fb-{r['id'][:8]}",
            "cat": "feedback",
            "q": r["question"].strip(),
            "user": r["user_name"],
            "date": str(r["at"])[:10],
            "thread": r["thread"],
        }
        if r["value"] >= 1:
            numbers = extract_numbers(answer_text(r["answer"]))[:3]
            if numbers:
                likes.append({**base, "source": "like", "all": [[n] for n in numbers]})
        else:
            drafts.append(
                {
                    **base,
                    "source": "dislike",
                    "comment": r["comment"] or "",
                    "answer": answer_text(r["answer"])[:2000],
                    "flags": flags(r["answer"]),
                    "expect": [],
                    "status": "to_review",
                }
            )
    return drafts, likes, [r["id"] for r in rows]


def promote() -> int:
    inbox = read_jsonl(INBOX)
    ready = [d for d in inbox if d.get("expect")]
    keep = [d for d in inbox if not d.get("expect")]
    append_jsonl(
        QUESTIONS,
        [
            {
                "id": d["id"],
                "cat": "feedback",
                "q": d["q"],
                "expect": d["expect"],
                "source": "dislike",
                "comment": d.get("comment", ""),
            }
            for d in ready
        ],
    )
    INBOX.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in keep), encoding="utf-8")
    print(
        f"promoted {len(ready)} reviewed question(s); {len(keep)} still waiting in {INBOX.relative_to(ROOT)}"
    )
    return 0


def report(drafts: list[dict], likes: list[dict]) -> None:
    total = len(drafts) + len(likes)
    print(
        f"new feedback: {total}  (👍 {len(likes)} with figures -> regression checks, 👎 {len(drafts)} -> inbox)"
    )
    for flag, n in Counter(f for d in drafts for f in d["flags"]).most_common():
        print(f"  👎 flagged {flag}: {n}")
    for d in drafts[:10]:
        print(f"  👎 {d['date']} {d['q'][:70]!r}  comment: {d['comment'][:60]!r}  {d['flags']}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--promote", action="store_true", help="move reviewed inbox drafts into the eval set")
    args = p.parse_args()
    if args.promote:
        return promote()
    with psycopg.connect(chat_db_url(), row_factory=dict_row) as conn:
        drafts, likes, ids = collect(conn)
    append_jsonl(INBOX, drafts)
    append_jsonl(QUESTIONS, likes)
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    with SEEN.open("a") as f:
        f.writelines(i + "\n" for i in ids)
    report(drafts, likes)
    if drafts:
        print(f'\nnext: fill "expect" for each draft in {INBOX.relative_to(ROOT)}, then run with --promote')
    return 0


if __name__ == "__main__":
    sys.exit(main())
