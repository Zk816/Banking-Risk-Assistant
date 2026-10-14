import time

import torch
from common import (
    DATA,
    RESULTS,
    SMOKE,
    parallel_map,
    parse_json,
    read_jsonl,
    save_json,
    strip_think,
    teacher,
    write_jsonl,
)
from peft import PeftModel
from s5_make_sft_data import check
from s6_train_qlora import BASE, OUT, quant_config
from transformers import AutoModelForCausalLM, AutoTokenizer

MAX_TEST = 40 if SMOKE else 900
BATCH = 16
MAX_NEW = 400

JUDGE = """You grade answers of a banking assistant. You get a question, the gold passage, a reference
answer and a candidate answer. Is the candidate factually correct and does it answer the question?
Extra correct detail is fine; a wrong number, a wrong entity or a missing key fact is not.
Reply with JSON only: {"verdict": "correct" | "partial" | "wrong"}"""


@torch.inference_mode()
def generate(model, tok, rows: list[dict]) -> list[str]:
    tok.padding_side = "left"
    out: list[str] = []
    started = time.time()
    for i in range(0, len(rows), BATCH):
        prompts = [
            tok.apply_chat_template(
                r["messages"], tokenize=False, add_generation_prompt=True, enable_thinking=False
            )
            for r in rows[i : i + BATCH]
        ]
        enc = tok(prompts, return_tensors="pt", padding=True, add_special_tokens=False).to(model.device)
        gen = model.generate(**enc, max_new_tokens=MAX_NEW, do_sample=False, pad_token_id=tok.pad_token_id)
        out += [
            strip_think(t)
            for t in tok.batch_decode(gen[:, enc["input_ids"].shape[1] :], skip_special_tokens=True)
        ]
        print(f"  generated {len(out)}/{len(rows)} {time.time() - started:.0f}s", flush=True)
    return out


def judge(item: tuple[dict, str]) -> str:
    row, answer = item
    gold = row["passages"][row["gold_idx"] - 1]
    reply = teacher(
        [
            {"role": "system", "content": JUDGE},
            {
                "role": "user",
                "content": f"Question: {row['question']}\n\nGold passage:\n{gold}\n\n"
                f"Reference answer: {row['reference']}\n\nCandidate answer: {answer}",
            },
        ],
        max_tokens=50,
    )
    return (parse_json(reply) or {}).get("verdict", "wrong")


def score(rows: list[dict], answers: list[str]) -> tuple[dict[str, float], list[dict]]:
    checks = [check(a, r["question"], [{"text": p} for p in r["passages"]]) for r, a in zip(rows, answers)]
    ans_ix = [i for i, r in enumerate(rows) if r["answerable"]]
    una_ix = [i for i, r in enumerate(rows) if not r["answerable"]]
    to_judge = [i for i in ans_ix if not checks[i]["refused"]]
    verdicts = dict(
        zip(to_judge, parallel_map(judge, [(rows[i], answers[i]) for i in to_judge], label="judge"))
    )
    rate = lambda ix, f: sum(bool(f(i)) for i in ix) / max(len(ix), 1)
    behaves = lambda i: checks[i]["refused"] != rows[i]["answerable"]
    m = {
        "untraceable_rate": rate(range(len(rows)), lambda i: checks[i]["untraceable"]),
        "invalid_cite_rate": rate(range(len(rows)), lambda i: checks[i]["invalid_citation"]),
        "no_cite_rate": rate(ans_ix, lambda i: not checks[i]["refused"] and not checks[i]["cited"]),
        "gold_cite_rate": rate(ans_ix, lambda i: rows[i]["gold_idx"] in checks[i]["cited"]),
        "refusal_recall": rate(una_ix, lambda i: checks[i]["refused"]),
        "false_refusal_rate": rate(ans_ix, lambda i: checks[i]["refused"]),
        "correct_rate": rate(ans_ix, lambda i: verdicts.get(i) == "correct"),
        "correct_or_partial_rate": rate(ans_ix, lambda i: verdicts.get(i) in ("correct", "partial")),
        "guard_pass_rate": rate(
            range(len(rows)),
            lambda i: behaves(i) and not checks[i]["untraceable"] and not checks[i]["invalid_citation"],
        ),
        "mean_answer_chars": sum(len(a) for a in answers) / max(len(answers), 1),
    }
    details = [{"check": c, "verdict": verdicts.get(i)} for i, c in enumerate(checks)]
    return m, details


def main() -> None:
    rows = read_jsonl(DATA / "sft_test.jsonl")[:MAX_TEST]
    print(f"{len(rows)} test items ({sum(not r['answerable'] for r in rows)} unanswerable)", flush=True)
    tok = AutoTokenizer.from_pretrained(BASE)
    model = AutoModelForCausalLM.from_pretrained(
        BASE, quantization_config=quant_config(), dtype=torch.bfloat16, device_map={"": 0}
    ).eval()
    base_answers = generate(model, tok, rows)
    model = PeftModel.from_pretrained(model, OUT).eval()
    lora_answers = generate(model, tok, rows)
    del model
    torch.cuda.empty_cache()

    results, gens = {}, [dict(r) for r in rows]
    for name, answers in (
        ("qwen3_8b_base", base_answers),
        ("qwen3_8b_qlora", lora_answers),
        ("teacher_qwen3.8_27b", [r["teacher"] for r in rows]),
    ):
        results[name], details = score(rows, answers)
        for g, a, d in zip(gens, answers, details):
            g[name] = {"answer": a, **d}
        print(name, {k: round(v, 3) for k, v in results[name].items()}, flush=True)
    write_jsonl(DATA / "qlora_generations.jsonl", gens)
    save_json(
        RESULTS / "qlora_eval.json",
        {"n_test": len(rows), "n_unanswerable": sum(not r["answerable"] for r in rows), "models": results},
    )


if __name__ == "__main__":
    main()
