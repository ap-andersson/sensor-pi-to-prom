#!/usr/bin/env bash
# Copy the working tree to a Pi, build the image there and (re)start it.
#
#   scripts/deploy-dev.sh <host> [remote-dir]
#
# The Pi needs a .env in the remote dir (see .env.example); it is never
# overwritten or deleted by this script.
set -euo pipefail

host="${1:?usage: $0 <host> [remote-dir]}"
remote_dir="${2:-sensor-pi-to-prom}"
here="$(cd "$(dirname "$0")/.." && pwd)"

rsync -az --delete \
    --exclude .git --exclude .env --exclude __pycache__ --exclude .venv \
    "$here/" "$host:$remote_dir/"

ssh "$host" "set -e
    cd '$remote_dir'
    if [ ! -f .env ]; then echo 'No .env in $remote_dir on $host, copy .env.example and edit it' >&2; exit 1; fi
    docker build -t sensor-pi-to-prom:dev .
    IMAGE=sensor-pi-to-prom:dev docker compose up -d
    sleep 3
    docker compose logs --tail 20"
