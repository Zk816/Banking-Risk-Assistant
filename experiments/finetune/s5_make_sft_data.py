import random
import re

from common import (
    DATA,
    REFUSAL_EN,
    REFUSAL_RU,
    bucket,
    is_refusal,
    parallel_map,
    read_jsonl,
    teacher,
    write_jsonl,
)

from fin_assistant.agent.citations import untraceable_numbers

TOP = 5
UNANSWERABLE_SHARE = 20
PASSAGE_CHARS = 1800

SYSTEM_RU = f"""Ты — ассистент риск-аналитика банка. Отвечай только по пронумерованным источникам.
- После каждого утверждения ставь ссылку на источник: [1], [2]...
- Числа, даты и названия переписывай точно как в источнике. Ничего не вычисляй и не добавляй от себя.
- Если в источниках нет ответа, ответь ровно: «{REFUSAL_RU}» — и больше ничего.
- Кратко: 1–4 предложения."""

SYSTEM_EN = f"""You assist a bank risk analyst. Answer only from the numbered sources.
- After each statement cite the source: [1], [2]...
- Copy numbers, dates and names exactly as in the source. Do not calculate or add anything.
- If the sources do not contain the answer, reply exactly: "{REFUSAL_EN}" and nothing else.
- Be brief: 1-4 sentences."""


def system_for(lang: str) -> str:
    return SYSTEM_EN if lang == "en" else SYSTEM_RU


def build_context(row: dict, rng: random.Random) -> tuple[list[dict], int | None, bool]:
    answerable = not bucket(f"unans{row['chunk_id']}", UNANSWERABLE_SHARE)
    ranked = sorted(row["candidates"], key=lambda c: -c["base_score"])
    gold = {"chunk_id": row["chunk_id"], "title": row["title"], "text": row["text"]}
    if answerable:
        others = [c for c in ranked if c["chunk_id"] != row["chunk_id"]][: TOP - 1]
        passages = others + [gold]
        rng.shuffle(passages)
        gold_idx = next(i for i, p in enumerate(passages, start=1) if p["chunk_id"] == row["chunk_id"])
        return passages, gold_idx, True
    others = [
        c
        for c in ranked
        if c["document_id"] != row["document_id"] and c["text"].strip() != row["text"].strip()
    ][:TOP]
    return others, None, False


def render_user(question: str, passages: list[dict], lang: str) -> str:
    head = "Sources" if lang == "en" else "Источники"
    q = "Question" if lang == "en" else "Вопрос"
    body = "\n\n".join(
        f"[{i}] ({p['title'][:120]})\n{p['text'][:PASSAGE_CHARS]}" for i, p in enumerate(passages, start=1)
    )
    return f"{head}:\n\n{body}\n\n{q}: {question}"


def check(answer: str, question: str, passages: list[dict]) -> dict:
    cited = [int(n) for n in re.findall(r"\[(\d+)\]", answer)]
    return {
        "refused": is_refusal(answer),
        "untraceable": untraceable_numbers(answer, [question] + [p["text"] for p in passages]),
        "cited": sorted(set(cited)),
        "invalid_citation": any(n < 1 or n > len(passages) for n in cited),
    }


def make_example(row: dict) -> dict | None:
    rng = random.Random(row["chunk_id"])
    passages, gold_idx, answerable = build_context(row, rng)
    if len(passages) < 3:
        return None
    user = render_user(row["question"], passages, row["lang"])
    messages = [{"role": "system", "content": system_for(row["lang"])}, {"role": "user", "content": user}]
    answer = teacher(messages, max_tokens=1500, thinking="low")
    c = check(answer, row["question"], passages)
    clean = (
        not c["untraceable"]
        and not c["invalid_citation"]
        and c["refused"] == (not answerable)
        and (not answerable or bool(c["cited"]))
    )
    return {
        "id": row["chunk_id"],
        "split": row["split"],
        "lang": row["lang"],
        "source": row["source"],
        "question": row["question"],
        "reference": row["answer"],
        "answerable": answerable,
        "gold_idx": gold_idx,
        "passages": [p["text"] for p in passages],
        "messages": messages,
        "teacher": answer,
        "teacher_check": c,
        "teacher_clean": clean,
    }


def main() -> None:
    rows = read_jsonl(DATA / "candidates.jsonl")
    out = [x for x in parallel_map(make_example, rows, label="teacher") if x]
    train = [x for x in out if x["split"] == "train" and x["teacher_clean"]]
    test = [x for x in out if x["split"] == "test"]
    write_jsonl(DATA / "sft_train.jsonl", train)
    write_jsonl(DATA / "sft_test.jsonl", test)
    all_train = sum(x["split"] == "train" for x in out)
    print(f"train: kept {len(train)} of {all_train} teacher answers after the checks; test: {len(test)}")


if __name__ == "__main__":
    main()
