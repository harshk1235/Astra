#!/usr/bin/env bash
# Pull the latest code from main and restart the bot (the database keeps running).
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose up -d --build
docker image prune -f >/dev/null          # remove old images so the disk doesn't fill up
docker compose ps
echo "Logs: docker compose logs -f bot"
