#!/usr/bin/env bash
# One-time setup after the first ./deploy.sh up, on the mail server:
#   - the mail domain (Stalwart then knows which mail is local)
#   - the admin principal "gaer-api" the app uses (STALWART_API_KEY=gaer-api:<secret> in the app's app.env)
# Idempotent: existing principals are left as they are.
#
#   ./bootstrap.sh kunde.dk
set -euo pipefail
cd "$(dirname "$0")"
domain=${1:?usage: bootstrap.sh <mail domain>}
set -a; . ./stalwart.env; set +a
api() { curl -fsSk -u "admin:$STALWART_ADMIN_SECRET" -H 'Content-Type: application/json' "$@"; }
base=https://localhost:8443/api

exists() { api -o /dev/null "$base/principal/$1" 2>/dev/null; }

if exists "$domain"; then echo "[mail] domænet $domain findes"; else
  api -X POST "$base/principal" -d "{\"type\":\"domain\",\"name\":\"$domain\",\"description\":\"$domain\"}" >/dev/null
  echo "[mail] domænet $domain er oprettet"
fi

if exists gaer-api; then
  echo "[mail] gaer-api findes; brug den adgangskode, du gemte første gang"
else
  secret=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  api -X POST "$base/principal" -d "{\"type\":\"individual\",\"name\":\"gaer-api\",\"description\":\"Gær-appen\",\"roles\":[\"admin\"],\"secrets\":[\"$secret\"],\"emails\":[]}" >/dev/null
  echo "[mail] gaer-api er oprettet. Sæt i appens app.env (vises kun nu):"
  echo "STALWART_API_KEY=gaer-api:$secret"
fi

echo "[mail] DKIM-, SPF- og DMARC-poster: se /admin/mail → Domæner i appen, efter du har tilføjet $domain der."
