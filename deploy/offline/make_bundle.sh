#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
DEST="${1:?usage: make_bundle.sh <destination folder>}"
mkdir -p "$DEST"/{images,models,db}
HF="${HF_HOME:-$HOME/.cache/huggingface}/hub"

echo "== images"
docker save rag_fin-api:latest pgvector/pgvector:pg16 vllm/vllm-openai:v0.28.0 searxng/searxng:latest \
    | gzip -1 > "$DEST/images/images.tar.gz"
echo "== models"
for m in models--Qwen--Qwen3.8-27B-FP8 models--BAAI--bge-m3 models--BAAI--bge-reranker-v2-m3; do
    cp -rL "$HF/$m" "$DEST/models/"
done
echo "== database"
docker exec user7-db pg_dump -U kzbank -Fc kzbank > "$DEST/db/fin_assistant.dump"
cp deploy/docker-compose.yml "$DEST/"
cp .env.example "$DEST/.env.example" 2>/dev/null || true
cat > "$DEST/install.sh" <<'INSTALL'
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
gunzip -c images/images.tar.gz | docker load
mkdir -p "$HOME/.cache/huggingface/hub" && cp -r models/* "$HOME/.cache/huggingface/hub/"
[[ -f .env ]] || cp .env.example .env
docker compose up -d db && sleep 10
docker exec -i user7-db pg_restore -U kzbank -d kzbank --clean --if-exists < db/fin_assistant.dump || true
docker compose up -d db llm api ui
echo "installed; set SEARXNG_URL= in .env to keep web search off on an offline host"
INSTALL
chmod +x "$DEST/install.sh"
( cd "$DEST" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS )
du -sh "$DEST"
