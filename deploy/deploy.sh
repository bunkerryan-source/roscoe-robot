#!/usr/bin/env bash
# Deploy roscoe-robot v2 to the droplet.
#
# Usage:  ./deploy/deploy.sh
#
# Run from the repo root on your local machine. Requires SSH key access to
# root@64.23.170.115. Pushes the bot/ folder, requirements.txt, and .env to
# /opt/personal-os-v2/, installs deps in a venv there, and reloads systemd.
#
# This does NOT start the v2 service or change the active webhook. After
# this script succeeds, do the cutover steps in Task 12.

set -euo pipefail

DROPLET_HOST="root@64.23.170.115"
REMOTE_DIR="/opt/personal-os-v2"

echo ">> Ensuring remote dir exists..."
ssh "$DROPLET_HOST" "mkdir -p $REMOTE_DIR"

echo ">> Rsyncing code (excludes .venv, .git, __pycache__, tests)..."
rsync -avz \
    --exclude '.venv' \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude 'tests' \
    --exclude 'docs' \
    --exclude 'scripts' \
    --exclude 'migrations' \
    --exclude 'deploy' \
    --exclude '.env' \
    --exclude '.env.example' \
    --exclude 'spec.md' \
    --exclude 'pyproject.toml' \
    bot/ requirements.txt \
    "$DROPLET_HOST:$REMOTE_DIR/"

echo ">> Copying .env (you'll be prompted)..."
scp .env "$DROPLET_HOST:$REMOTE_DIR/.env"
ssh "$DROPLET_HOST" "chmod 600 $REMOTE_DIR/.env"

echo ">> Creating venv and installing deps on droplet..."
ssh "$DROPLET_HOST" "
    set -e
    cd $REMOTE_DIR
    if [ ! -d venv ]; then python3 -m venv venv; fi
    venv/bin/pip install --upgrade pip
    venv/bin/pip install -r requirements.txt
"

echo ">> Copying systemd unit (will overwrite)..."
scp deploy/personal-os-v2.service "$DROPLET_HOST:/etc/systemd/system/personal-os-v2.service"
ssh "$DROPLET_HOST" "systemctl daemon-reload"

echo ">> Done. Cutover steps:"
echo "   ssh $DROPLET_HOST 'systemctl stop personal-os && systemctl start personal-os-v2'"
echo "   then re-register the Telegram webhook to point at the v2 endpoint (same URL,"
echo "   probably a different port — confirm before flipping)."
