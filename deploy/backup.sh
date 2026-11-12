#!/usr/bin/env bash
# Dump the database to ~/backups (compressed), keeping the last 14 days.
# Restore: gunzip -c FILE | docker compose exec -T db psql -U astra -d astra
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p "$HOME/backups"
file="$HOME/backups/astra-$(date +%Y%m%d-%H%M).sql.gz"
docker compose exec -T db pg_dump -U astra -d astra | gzip > "$file"
find "$HOME/backups" -name 'astra-*.sql.gz' -mtime +14 -delete
echo "$(date -Is) backup ok: $file ($(du -h "$file" | cut -f1))"
