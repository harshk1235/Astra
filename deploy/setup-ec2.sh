#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu (24.04 or 26.04 LTS) EC2 instance for Astra.
#   curl -fsSL https://raw.githubusercontent.com/harshk1235/Astra/main/deploy/setup-ec2.sh | bash
# Safe to re-run. Afterwards: fill in ~/astra/.env,
# then run ~/astra/deploy/update.sh
set -euo pipefail

REPO_URL="https://github.com/harshk1235/Astra.git"
APP_DIR="$HOME/astra"

echo "==> Installing Docker"
sudo apt-get update -y
sudo apt-get install -y git curl
if ! command -v docker >/dev/null 2>&1; then
  if apt-cache show docker-compose-v2 >/dev/null 2>&1; then
    sudo apt-get install -y docker.io docker-compose-v2          # Ubuntu's own packages
  else
    curl -fsSL https://get.docker.com | sudo sh                 # Docker's installer (includes compose)
  fi
fi
sudo systemctl enable --now docker
sudo docker compose version >/dev/null || { echo "Docker Compose isn't available" >&2; exit 1; }
sudo usermod -aG docker "$USER"          # run docker without sudo (after re-login)

echo "==> Adding 2 GB swap (a micro instance has 1 GB RAM; Postgres + bot fit, swap is a safety net)"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> Getting the code"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi
[ -f "$APP_DIR/.env" ] || cp "$APP_DIR/.env.example" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

echo "==> Nightly database backup at 03:30 server time (keeps 14 days)"
chmod +x "$APP_DIR"/deploy/*.sh
( crontab -l 2>/dev/null | grep -v 'deploy/backup.sh' ; echo "30 3 * * * $APP_DIR/deploy/backup.sh >> $HOME/backup.log 2>&1" ) | crontab -

cat <<MSG

Done. Next:
  1. Log out and back in (so docker works without sudo).
  2. nano $APP_DIR/.env        -> fill in TOKEN, GUILD_ID, CREDENTIALS_JSON, GOOGLE_SHEET_URL,
                                 and a strong POSTGRES_PASSWORD
  3. $APP_DIR/deploy/update.sh -> builds and starts the bot and database
MSG
