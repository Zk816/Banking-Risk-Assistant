#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

export HF_HUB_OFFLINE=1

UV="${UV:-$HOME/.local/bin/uv}"
[[ -x "$UV" ]] || UV="$(command -v uv)" || {
    echo "uv not found; set UV=/path/to/uv" >&2
    exit 127
}
run() { echo "== $*"; "$UV" run python "$@" || echo "!! failed: $*"; }

run scripts/ingest_rates.py
run scripts/ingest_base_rate.py
run scripts/ingest_kdif.py
run scripts/learn_from_web.py
[[ "${1:-}" == "--full" ]] || exit 0

run scripts/ingest_nbk_stats.py --download-all
run scripts/ingest_nbk_stats.py
run scripts/ingest_kase.py
run scripts/ingest_worldbank.py

run scripts/download_kase_docs.py
run scripts/download_kase_docs.py --all-issuers
run scripts/download_nbk_pub.py
run scripts/download_kdif_docs.py
run scripts/download_wb_docs.py
run scripts/download_bis.py
run scripts/ingest.py --source laws
run scripts/ingest.py --source nbk_regulations
for source in stability_reports basel_standards nbk_publications deposit_market worldbank_reports; do
    run scripts/ingest.py --source "$source"
done
run scripts/ingest.py --source company_reports --ocr
run scripts/ingest.py --source nbk_publications --tables
run scripts/ingest.py --source deposit_market --tables

run scripts/link_documents.py
run scripts/data_summary.py
