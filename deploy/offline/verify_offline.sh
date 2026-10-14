#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
NET=user7-offline
OUT=data/offline_check
mkdir -p "$OUT"
docker network inspect "$NET" >/dev/null 2>&1 || docker network create --internal "$NET" >/dev/null

cleanup() {
    docker network disconnect "$NET" user7-db  >/dev/null 2>&1 || true
    docker network disconnect "$NET" user7-llm >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker network connect --alias db  "$NET" user7-db
docker network connect --alias llm "$NET" user7-llm

before=$(ls -t eval/results/holdout_assistant_*.json 2>/dev/null | head -1 || true)
[[ -n "$before" ]] && cp "$before" "$OUT/.backup.json"

run() {
    docker run --rm --network "$NET" --gpus '"device='"${API_GPU:-7}"'"' --user 0:0 \
        -v "${HF_HOME:-$HOME/.cache/huggingface}:/models:ro" \
        -v "$PWD/src:/app/src:ro" -v "$PWD/eval:/app/eval" \
        -e DATABASE_URL=postgresql://kzbank:kzbank@db:5432/kzbank \
        -e LLM_BASE_URL=http://llm:8000/v1 -e LLM_MODEL=qwen3.8-27b \
        -e EMBED_DEVICE=cuda:0 -e SEARXNG_URL= -e LANGFUSE_PUBLIC_KEY= -e LANGFUSE_SECRET_KEY= \
        rag_fin-api:latest "$@"
}

echo "== 1. the container cannot reach the internet"
run python -c "
import socket, urllib.request
for url in ('https://huggingface.co', 'https://pypi.org', 'https://www.google.com'):
    try:
        urllib.request.urlopen(url, timeout=5); print('REACHABLE (bad):', url)
    except Exception as e:
        print('blocked:', url, '-', type(e).__name__)
print('db reachable:', socket.create_connection(('db', 5432), timeout=5) is not None)
print('llm reachable:', socket.create_connection(('llm', 8000), timeout=5) is not None)
" | tee "$OUT/no_internet.txt"
grep -q "REACHABLE (bad)" "$OUT/no_internet.txt" && { echo "!! internet is reachable: not an offline test"; exit 1; }

echo "== 2. held-out questions, offline"
run python eval/run_pro.py --agent assistant --set holdout | tee "$OUT/holdout_offline.log"
newest=$(ls -t eval/results/holdout_assistant_*.json | head -1)
cp "$newest" "$OUT/holdout_offline.json"
if [[ "$newest" == "$before" ]]; then cp "$OUT/.backup.json" "$before"; else rm -f "$newest"; fi
python3 -c "import json; d=json.load(open('$OUT/holdout_offline.json')); print(f\"offline held-out: {d['correct']}/{d['total']}\")" | tee -a "$OUT/no_internet.txt"
