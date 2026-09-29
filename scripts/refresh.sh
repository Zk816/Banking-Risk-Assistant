#!/usr/bin/env bash
# Daily data refresh. Idempotent: same content hash means nothing is written.
#
# Run by the reap timer; safe to run by hand at any time.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

export HF_HUB_OFFLINE=1

# systemd user services get a minimal PATH that does not include ~/.local/bin,
# so `uv` is not on it. Found by running the unit rather than trusting it:
# the first attempt died with `exec: uv: not found`, status 127.
UV="${UV:-$HOME/.local/bin/uv}"
[[ -x "$UV" ]] || UV="$(command -v uv)" || {
    echo "uv not found; set UV=/path/to/uv" >&2
    exit 127
}

exec "$UV" run python scripts/ingest_rates.py
