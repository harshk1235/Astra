#!/usr/bin/env bash
# Update the server to the latest main (or to a specific commit, which is what
# the GitHub Actions deploy passes), rebuild, restart, and check the bot comes
# back online. The database keeps running throughout.
#   ./deploy/update.sh            latest main
#   ./deploy/update.sh <commit>   that commit (must be on main)
set -euo pipefail
cd "$(dirname "$0")/.."

git fetch -q origin main
git checkout -q main
if [ $# -ge 1 ]; then
  git merge -q --ff-only "$1"
else
  git merge -q --ff-only origin/main
fi
echo "Code at $(git log -1 --format='%h %s')"

docker compose up -d --build
docker image prune -f >/dev/null          # remove old images so the disk doesn't fill up

# Health check: the bot logs "... is online." once it's connected to Discord.
# If it doesn't within 2 minutes (bad config, crash loop), fail loudly.
echo "Waiting for the bot to come online..."
if timeout 120 bash -c 'until docker compose logs --since 3m bot 2>&1 | grep -q "is online"; do sleep 3; done'; then
  echo "Bot is online."
else
  echo "Bot did not come online. Last log lines:" >&2
  docker compose logs --tail 40 bot >&2
  exit 1
fi
docker compose ps
