import math
import random
import time

import torch
from common import DATA, MODELS, RESULTS, SMOKE, bucket, read_jsonl, save_json
from peft import LoraConfig, TaskType, get_peft_model
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_cosine_schedule_with_warmup

from fin_assistant.config import settings

GROUP = 8
BATCH_GROUPS = 8
EPOCHS = 1 if SMOKE else 2
LR = 2e-4
MAX_LEN = 512
DEV_SHARE = 5


def build_groups(rows: list[dict], rng: random.Random) -> list[tuple[str, list[str]]]:
    all_texts = [r["text"] for r in rows]
    groups = []
    for r in rows:
        by_id = {c["chunk_id"]: c["text"] for c in r["candidates"]}
        negs = [by_id[i] for i in r["hard_negatives"] if i in by_id]
        rng.shuffle(negs)
        negs = negs[: GROUP - 1]
        while len(negs) < GROUP - 1:
            t = rng.choice(all_texts)
            if t != r["text"]:
                negs.append(t)
        groups.append((r["question"], [r["text"]] + negs))
    return groups


def group_logits(model, tok, batch: list[tuple[str, list[str]]]) -> torch.Tensor:
    qs = [q for q, ps in batch for _ in ps]
    ps = [p for _, ps_ in batch for p in ps_]
    enc = tok(qs, ps, truncation=True, max_length=MAX_LEN, padding=True, return_tensors="pt").to("cuda")
    return model(**enc).logits.float().view(len(batch), GROUP)


@torch.inference_mode()
def dev_mrr(model, tok, groups: list[tuple[str, list[str]]]) -> float:
    model.eval()
    rr = []
    for i in range(0, len(groups), BATCH_GROUPS):
        logits = group_logits(model, tok, groups[i : i + BATCH_GROUPS])
        ranks = (logits > logits[:, :1]).sum(dim=1) + 1
        rr += (1.0 / ranks.float()).tolist()
    model.train()
    return sum(rr) / max(len(rr), 1)


def main() -> None:
    rng = random.Random(0)
    rows = [r for r in read_jsonl(DATA / "candidates.jsonl") if r["split"] == "train"]
    dev_rows = [r for r in rows if bucket(f"dev{r['chunk_id']}", DEV_SHARE)]
    train_rows = [r for r in rows if not bucket(f"dev{r['chunk_id']}", DEV_SHARE)]
    train_groups, dev_groups = build_groups(train_rows, rng), build_groups(dev_rows, rng)
    print(f"train groups {len(train_groups)}, dev groups {len(dev_groups)}", flush=True)

    tok = AutoTokenizer.from_pretrained(settings.rerank_model)
    base = AutoModelForSequenceClassification.from_pretrained(settings.rerank_model, dtype=torch.bfloat16)
    lora = LoraConfig(
        task_type=TaskType.SEQ_CLS,
        r=32,
        lora_alpha=64,
        lora_dropout=0.1,
        target_modules=["query", "key", "value", "dense"],
    )
    model = get_peft_model(base, lora).to("cuda")
    model.print_trainable_parameters()

    steps = EPOCHS * math.ceil(len(train_groups) / BATCH_GROUPS)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=LR, weight_decay=0.01)
    sched = get_cosine_schedule_with_warmup(opt, int(0.05 * steps), steps)

    history = [{"epoch": 0, "dev_mrr": dev_mrr(model, tok, dev_groups)}]
    print(f"epoch 0 dev MRR {history[-1]['dev_mrr']:.4f}", flush=True)
    best, started, step = history[0]["dev_mrr"], time.time(), 0
    model.train()
    for epoch in range(1, EPOCHS + 1):
        rng.shuffle(train_groups)
        for i in range(0, len(train_groups), BATCH_GROUPS):
            logits = group_logits(model, tok, train_groups[i : i + BATCH_GROUPS])
            loss = torch.nn.functional.cross_entropy(
                logits, torch.zeros(len(logits), dtype=torch.long, device="cuda")
            )
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad()
            step += 1
            if step % 50 == 0:
                print(
                    f"  step {step}/{steps} loss {loss.item():.4f} {time.time() - started:.0f}s", flush=True
                )
        mrr = dev_mrr(model, tok, dev_groups)
        history.append({"epoch": epoch, "dev_mrr": mrr})
        print(f"epoch {epoch} dev MRR {mrr:.4f}", flush=True)
        if mrr > best:
            best = mrr
            model.save_pretrained(MODELS / "reranker_lora")

    if not (MODELS / "reranker_lora").exists():
        model.save_pretrained(MODELS / "reranker_lora")
    from peft import PeftModel

    fresh = AutoModelForSequenceClassification.from_pretrained(settings.rerank_model, dtype=torch.bfloat16)
    merged = PeftModel.from_pretrained(fresh, MODELS / "reranker_lora").merge_and_unload()
    merged.save_pretrained(MODELS / "reranker_merged")
    tok.save_pretrained(MODELS / "reranker_merged")
    save_json(
        RESULTS / "reranker_train.json",
        {
            "train_groups": len(train_groups),
            "dev_groups": len(dev_groups),
            "epochs": EPOCHS,
            "lr": LR,
            "lora": {"r": 32, "alpha": 64},
            "history": history,
            "minutes": round((time.time() - started) / 60, 1),
        },
    )
    print("saved models/reranker_lora and models/reranker_merged")


if __name__ == "__main__":
    main()
