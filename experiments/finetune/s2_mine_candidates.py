import torch
from common import DATA, read_jsonl, write_jsonl
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from fin_assistant.config import settings
from fin_assistant.retrieval.hybrid import search_hybrid

DEPTH = 30
MAX_LEN = 512
FALSE_NEG_RATIO = 0.95


@torch.inference_mode()
def score_pairs(model, tok, query: str, texts: list[str]) -> list[float]:
    enc = tok(
        [query] * len(texts), texts, truncation=True, max_length=MAX_LEN, padding=True, return_tensors="pt"
    ).to(model.device)
    return torch.sigmoid(model(**enc).logits.float().view(-1)).tolist()


def main() -> None:
    qa = read_jsonl(DATA / "qa.jsonl")
    tok = AutoTokenizer.from_pretrained(settings.rerank_model)
    model = (
        AutoModelForSequenceClassification.from_pretrained(settings.rerank_model, dtype=torch.bfloat16)
        .to("cuda")
        .eval()
    )

    out = []
    for i, q in enumerate(qa, start=1):
        fused = search_hybrid(q["question"], top_k=DEPTH, candidates=DEPTH * 2)
        cands = [
            {
                "chunk_id": f.hit.chunk_id,
                "document_id": f.hit.document_id,
                "ordinal": f.hit.ordinal,
                "title": f.hit.document_title,
                "text": f.hit.text,
            }
            for f in fused
        ]
        gold_rank = next((r for r, c in enumerate(cands, start=1) if c["chunk_id"] == q["chunk_id"]), None)

        texts = [q["text"]] + [c["text"] for c in cands]
        scores = score_pairs(model, tok, q["question"], texts)
        gold_score = scores[0]
        for c, s in zip(cands, scores[1:]):
            c["base_score"] = s

        def is_negative(c: dict) -> bool:
            if c["chunk_id"] == q["chunk_id"] or c["text"].strip() == q["text"].strip():
                return False
            if c["document_id"] == q["document_id"] and abs(c["ordinal"] - q["ordinal"]) <= 1:
                return False
            return c["base_score"] < FALSE_NEG_RATIO * gold_score

        out.append(
            {
                **q,
                "gold_rank_hybrid": gold_rank,
                "gold_base_score": gold_score,
                "candidates": cands,
                "hard_negatives": [c["chunk_id"] for c in cands if is_negative(c)],
            }
        )
        if i % 200 == 0 or i == len(qa):
            found = sum(r["gold_rank_hybrid"] is not None for r in out)
            print(f"  {i}/{len(qa)}  gold in top-{DEPTH}: {found / len(out):.1%}", flush=True)

    write_jsonl(DATA / "candidates.jsonl", out)
    print(f"wrote {len(out)} rows to {DATA / 'candidates.jsonl'}")


if __name__ == "__main__":
    main()
