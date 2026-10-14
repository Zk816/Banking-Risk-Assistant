#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
KEEP=3
DIR=data/backups
mkdir -p "$DIR"
stamp=$(date +%Y%m%d_%H%M)
for db in kzbank chainlit; do
    tmp="$DIR/.${db}_${stamp}.dump.part"
    docker exec user7-db pg_dump -U kzbank -Fc "$db" > "$tmp"
    mv "$tmp" "$DIR/${db}_${stamp}.dump"
    ls -1t "$DIR/${db}_"*.dump | tail -n +$((KEEP + 1)) | xargs -r rm --
    echo "$db: $(du -h "$DIR/${db}_${stamp}.dump" | cut -f1)"
done
