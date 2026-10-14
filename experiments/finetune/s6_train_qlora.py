import time

import torch
from common import DATA, MODELS, RESULTS, SMOKE, bucket, read_jsonl, save_json
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer, TrainingArguments

BASE = "Qwen/Qwen3-8B"
MAX_LEN = 4096
OUT = MODELS / "qwen3_8b_grounded_lora"


def quant_config() -> BitsAndBytesConfig:
    return BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )


def encode(tok, messages: list[dict], answer: str) -> dict | None:
    prompt = tok.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    a_ids = tok(answer + "<|im_end|>\n", add_special_tokens=False)["input_ids"]
    if len(p_ids) + len(a_ids) > MAX_LEN:
        return None
    return {"input_ids": p_ids + a_ids, "labels": [-100] * len(p_ids) + a_ids}


class Collator:
    def __init__(self, pad_id: int) -> None:
        self.pad_id = pad_id

    def __call__(self, batch: list[dict]) -> dict[str, torch.Tensor]:
        n = max(len(x["input_ids"]) for x in batch)
        ids = torch.full((len(batch), n), self.pad_id)
        labels = torch.full((len(batch), n), -100)
        mask = torch.zeros((len(batch), n), dtype=torch.long)
        for i, x in enumerate(batch):
            k = len(x["input_ids"])
            ids[i, :k] = torch.tensor(x["input_ids"])
            labels[i, :k] = torch.tensor(x["labels"])
            mask[i, :k] = 1
        return {"input_ids": ids, "labels": labels, "attention_mask": mask}


def main() -> None:
    tok = AutoTokenizer.from_pretrained(BASE)
    rows = read_jsonl(DATA / "sft_train.jsonl")
    if SMOKE:
        rows = rows[:40]
    encoded = [(r, encode(tok, r["messages"], r["teacher"])) for r in rows]
    dropped = sum(e is None for _, e in encoded)
    train = [e for r, e in encoded if e and not bucket(f"sftdev{r['id']}", 5)]
    dev = [e for r, e in encoded if e and bucket(f"sftdev{r['id']}", 5)]
    print(f"train {len(train)}, dev {len(dev)}, dropped (too long) {dropped}", flush=True)

    model = AutoModelForCausalLM.from_pretrained(
        BASE, quantization_config=quant_config(), dtype=torch.bfloat16, device_map={"": 0}
    )
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model = get_peft_model(
        model,
        LoraConfig(
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        ),
    )
    model.print_trainable_parameters()

    args = TrainingArguments(
        output_dir=str(MODELS / "qlora_checkpoints"),
        num_train_epochs=1 if SMOKE else 2,
        per_device_train_batch_size=4,
        per_device_eval_batch_size=4,
        gradient_accumulation_steps=4,
        learning_rate=2e-4,
        lr_scheduler_type="cosine",
        warmup_steps=0 if SMOKE else 20,
        bf16=True,
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=1,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        report_to=[],
        remove_unused_columns=False,
        seed=0,
    )
    started = time.time()
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=train,
        eval_dataset=dev,
        data_collator=Collator(tok.pad_token_id),
    )
    trainer.train()
    model.save_pretrained(OUT)
    tok.save_pretrained(OUT)
    logs = trainer.state.log_history
    save_json(
        RESULTS / "qlora_train.json",
        {
            "base": BASE,
            "train_examples": len(train),
            "dev_examples": len(dev),
            "dropped_too_long": dropped,
            "lora": {"r": 16, "alpha": 32, "targets": "all attention + MLP projections"},
            "quant": "nf4 4-bit",
            "epochs": args.num_train_epochs,
            "minutes": round((time.time() - started) / 60, 1),
            "eval_loss": [x["eval_loss"] for x in logs if "eval_loss" in x],
            "train_loss_last": next((x["loss"] for x in reversed(logs) if "loss" in x), None),
        },
    )
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
