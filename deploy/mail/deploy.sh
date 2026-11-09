#!/usr/bin/env bash
# Runs on the mail server in /opt/gaer-mail next to compose.yml, config.toml and stalwart.env.
#
#   ./deploy.sh up          pull the pinned image, start or update Stalwart, wait until it is ready
#   ./deploy.sh status      container state, readiness and the outbound queue size
#   ./deploy.sh logs        follow Stalwart's log
#
# Copy the files first, e.g.: scp compose.yml config.toml deploy.sh bootstrap.sh root@mail.kunde.dk:/opt/gaer-mail/
set -euo pipefail
cd "$(dirname "$0")"

log() { echo "[mail] $*"; }
compose() { docker compose -f compose.yml "$@"; }

check_env() {
  [[ -f stalwart.env ]] || { log "stalwart.env mangler (se stalwart.env.example)"; exit 1; }
  local missing
  missing=$(grep -E '^[A-Z_]+=$' stalwart.env | cut -d= -f1 | tr '\n' ' ')
  [[ -z "$missing" ]] || { log "tomme værdier i stalwart.env: $missing"; exit 1; }
  chmod 600 stalwart.env
  set -a; . ./stalwart.env; set +a
}

ready() { curl -fsSk -o /dev/null "https://localhost:8443/healthz/ready"; }

up() {
  check_env
  compose pull --quiet
  compose up -d --remove-orphans
  for _ in $(seq 1 36); do
    if ready; then log "Stalwart svarer på https://$MAIL_HOSTNAME"; return 0; fi
    sleep 5
  done
  log "Stalwart blev ikke klar inden for 3 minutter"; compose logs --tail 80 stalwart; exit 1
}

status() {
  check_env
  compose ps
  ready && log "klar" || log "ikke klar"
  curl -fsSk -u "admin:$STALWART_ADMIN_SECRET" "https://localhost:8443/api/queue/messages?limit=1" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin).get("data",{}); print("[mail] udgående kø:", d.get("total"))' || true
}

case "${1:-}" in
  up) up ;;
  status) status ;;
  logs) compose logs -f --tail 100 stalwart ;;
  *) echo "usage: $0 up|status|logs"; exit 2 ;;
esac
