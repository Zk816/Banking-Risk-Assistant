#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

export CUDA_VISIBLE_DEVICES="${FT_GPU:-6}"
export EMBED_DEVICE=cuda:0
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTHONPATH="$PWD:${PYTHONPATH:-}"
unset VIRTUAL_ENV
UV="${UV:-$HOME/.local/bin/uv}"
DATA=../../data/finetune; RES=results; MOD=../../models
if [[ "${SMOKE:-}" == "1" ]]; then DATA=../../data/finetune_smoke; RES=results_smoke; MOD=../../models_smoke; fi
mkdir -p "$DATA" "$RES"
LOG="$DATA/run_$(date +%Y%m%d_%H%M).log"

step() {
    if [[ -s "$1" ]]; then echo "== skip $2 ($1 exists)"; return 0; fi
    echo "== $(date '+%F %T') $2"
    if ! "$UV" run --extra finetune python "$2"; then
        echo "!! $2 failed — stopping here; fix and re-run, finished steps are kept"
        exit 1
    fi
}

{
echo "start $(date)  smoke=${SMOKE:-0}  gpu=$CUDA_VISIBLE_DEVICES"
curl -sf -m 10 localhost:8320/v1/models >/dev/null || { echo "!! vLLM (user7-llm) is not up on :8320"; exit 1; }

step "$DATA/qa.jsonl"              s1_make_questions.py
step "$DATA/candidates.jsonl"      s2_mine_candidates.py
step "$MOD/reranker_merged/config.json" s3_train_reranker.py
step "$RES/reranker_eval.json"     s4_eval_reranker.py

if [[ ! -s "$RES/e2e_holdout_lora_reranker.json" && -z "${SMOKE:-}" ]]; then
    echo "== $(date '+%F %T') end-to-end held-out with the LoRA reranker"
    before=$(ls -t ../../eval/results/holdout_assistant_*.json 2>/dev/null | head -1)
    [[ -n "$before" ]] && cp "$before" "$RES/.holdout_backup.json"
    if (cd ../.. && RERANK_MODEL="$PWD/models/reranker_merged" "$UV" run python eval/run_pro.py --agent assistant --set holdout); then
        newest=$(ls -t ../../eval/results/holdout_assistant_*.json | head -1)
        cp "$newest" "$RES/e2e_holdout_lora_reranker.json"
        if [[ "$newest" == "$before" ]]; then cp "$RES/.holdout_backup.json" "$before"; else rm -f "$newest"; fi
    fi
fi

step "$DATA/sft_test.jsonl"        s5_make_sft_data.py
step "$MOD/qwen3_8b_grounded_lora/adapter_config.json" s6_train_qlora.py
step "$RES/qlora_eval.json"        s7_eval_qlora.py
"$UV" run --extra finetune python s8_report.py
echo "done $(date)"
} 2>&1 | tee -a "$LOG"
