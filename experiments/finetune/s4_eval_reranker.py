import math
import random
import time
from collections import defaultdict

import torch
from common import DATA, MODELS, RESULTS, read_jsonl, save_json
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from fin_assistant.config import settings

MAX_LEN = 512


def metrics(ranks: list[int | None]) -> dict[str, float]:
    n = len(ranks)
    r = [x if x is not None else 10**9 for x in ranks]
    return {
        "recall@1": sum(x <= 1 for x in r) / n,
        "recall@5": sum(x <= 5 for x in r) / n,
        "mrr@10": sum(1 / x for x in r if x <= 10) / n,
        "ndcg@10": sum(1 / math.log2(x + 1) for x in r if x <= 10) / n,
    }


@torch.inference_mode()
def rerank_ranks(path: str, rows: list[dict]) -> tuple[list[int | None], float]:
    tok = AutoTokenizer.from_pretrained(path)
    model = AutoModelForSequenceClassification.from_pretrained(path, dtype=torch.bfloat16).to("cuda").eval()
    ranks: list[int | None] = []
    started = time.time()
    for r in rows:
        cands = r["candidates"]
        enc = tok(
            [r["question"]] * len(cands),
            [c["text"] for c in cands],
            truncation=True,
            max_length=MAX_LEN,
            padding=True,
            return_tensors="pt",
        ).to("cuda")
        scores = model(**enc).logits.float().view(-1).tolist()
        order = [c["chunk_id"] for _, c in sorted(zip(scores, cands), key=lambda t: -t[0])]
        ranks.append(order.index(r["chunk_id"]) + 1 if r["chunk_id"] in order else None)
    ms_per_query = (time.time() - started) / max(len(rows), 1) * 1000
    del model
    torch.cuda.empty_cache()
    return ranks, ms_per_query


def bootstrap_gain(a: list[int | None], b: list[int | None], n_boot: int = 2000) -> tuple[float, float]:
    rr = lambda x: 1 / x if x is not None and x <= 10 else 0.0
    diffs = [rr(y) - rr(x) for x, y in zip(a, b)]
    rng, n, means = random.Random(0), len(diffs), []
    for _ in range(n_boot):
        means.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot)]


def main() -> None:
    rows = [r for r in read_jsonl(DATA / "candidates.jsonl") if r["split"] == "test"]
    print(
        f"{len(rows)} test questions from {len({r['document_id'] for r in rows})} held-out documents",
        flush=True,
    )
    ranks = {"hybrid": [r["gold_rank_hybrid"] for r in rows]}
    ranks["base"], base_ms = rerank_ranks(settings.rerank_model, rows)
    ranks["lora"], lora_ms = rerank_ranks(str(MODELS / "reranker_merged"), rows)

    in_pool = [i for i, r in enumerate(rows) if r["gold_rank_hybrid"] is not None]
    by_source: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_source[r["source"]].append(i)

    lo, hi = bootstrap_gain(ranks["base"], ranks["lora"])
    out = {
        "n_test": len(rows),
        "gold_in_pool": len(in_pool) / len(rows),
        "overall": {k: metrics(v) for k, v in ranks.items()},
        "gold_in_pool_only": {k: metrics([v[i] for i in in_pool]) for k, v in ranks.items()},
        "by_language": {
            lang: {
                k: metrics([v[i] for i, r in enumerate(rows) if r["lang"] == lang]) for k, v in ranks.items()
            }
            for lang in sorted({r["lang"] for r in rows})
        },
        "by_source": {
            s: {"n": len(ix), **{k: metrics([v[i] for i in ix])["mrr@10"] for k, v in ranks.items()}}
            for s, ix in sorted(by_source.items())
        },
        "mrr_gain_lora_vs_base_95ci": [lo, hi],
        "ms_per_query_30_candidates": {"base": base_ms, "lora": lora_ms},
    }
    save_json(RESULTS / "reranker_eval.json", out)
    for k, m in out["overall"].items():
        print(f"{k:7s} " + "  ".join(f"{name} {v:.3f}" for name, v in m.items()))
    print(f"MRR gain lora vs base, 95% CI: [{lo:+.3f}, {hi:+.3f}]")


if __name__ == "__main__":
    main()
