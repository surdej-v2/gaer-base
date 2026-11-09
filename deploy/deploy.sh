#!/usr/bin/env bash
# Runs on the UpCloud server in ~/gaer next to compose.yml, .env and app.env.
# Called by .github/workflows/deploy.yml over SSH.
#
#   ./deploy.sh release <image>   migrate the database (and ClickHouse), start the new version, roll back if /health fails
#   ./deploy.sh rollback          start the previous image again
#
# If the new version does not answer /health within 60 seconds, the previous image is started again.

set -euo pipefail
cd "$(dirname "$0")"

log() { echo "[deploy] $*"; }
compose() { docker compose -f compose.yml "$@"; }

healthy() {
  compose exec -T app python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)" >/dev/null 2>&1
}

start() {
  local tag=${1##*:}
  export IMAGE=$1 GAER_VERSION=${tag::7}
  compose up -d --remove-orphans
  for _ in $(seq 1 12); do
    healthy && return 0
    sleep 5
  done
  return 1
}

release() {
  local image=$1 previous
  previous=$(cat .current_image 2>/dev/null || true)
  export IMAGE=$image
  compose pull --quiet cloudflared
  compose up -d --wait postgres
  # ClickHouse is optional for the app: if it does not come up, migrate prints a warning and the deploy goes on.
  compose up -d --wait clickhouse || log "clickhouse is not healthy; its migrations wait for the next release"

  log "migrating"
  compose run --rm --no-deps app python -m app.migrate

  log "starting $image"
  if start "$image"; then
    echo "$image" > .current_image
    if [ -n "$previous" ] && [ "$previous" != "$image" ]; then echo "$previous" > .previous_image; fi
    # Images are loaded straight from CI; keep only the current and previous one.
    docker images --format '{{.Repository}}:{{.Tag}}' "${image%%:*}" \
      | grep -vxF -e "$image" -e "$previous" | xargs -r docker rmi >/dev/null 2>&1 || true
    docker image prune -f >/dev/null
    log "released $image"
    return 0
  fi

  log "new version did not answer /health within 60 seconds"
  compose logs --tail 80 app || true
  if [ -n "$previous" ]; then
    log "starting previous version $previous"
    start "$previous" || log "previous version is not healthy either"
  fi
  return 1
}

case "${1:-}" in
  release)
    release "${2:?image required}"
    ;;
  rollback)
    [ -f .previous_image ] || { log "no previous image recorded"; exit 1; }
    image=$(cat .previous_image)
    start "$image" && echo "$image" > .current_image && log "rolled back to $image"
    ;;
  *)
    echo "usage: $0 release <image> | rollback" >&2
    exit 2
    ;;
esac
