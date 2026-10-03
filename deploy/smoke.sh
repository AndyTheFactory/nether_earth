#!/usr/bin/env bash
# Production-like deployment smoke test (CI release gate, release checklist).
#
# Builds (unless SMOKE_NO_BUILD=1) and starts the real Compose stack under a
# separate project name and port, then verifies through the Nginx gateway:
# health/readiness, static frontend, security headers, Origin policy, a full
# two-client WebSocket match flow, replay persistence on the host mount, and
# recovery after a backend restart. Tears everything down on exit.
#
#   deploy/smoke.sh                       # build + test images tagged "smoke"
#   NETHER_EARTH_VERSION=1.0.0 SMOKE_NO_BUILD=1 deploy/smoke.sh   # test existing images
#
# Needs: docker compose v2, curl, node >= 24 (frontend/scripts/live-two-client.mjs).
set -euo pipefail

cd "$(dirname "$0")"
VERSION="${NETHER_EARTH_VERSION:-smoke}"
PORT="${SMOKE_PORT:-18090}"
PROJECT="${SMOKE_PROJECT:-nether-earth-smoke}"
BASE="http://localhost:${PORT}"
WORK="$(mktemp -d)"
ENV_FILE="${WORK}/env"

compose() { docker compose -p "${PROJECT}" --env-file "${ENV_FILE}" -f docker-compose.yml "$@"; }
step() { printf '\n==> %s\n' "$*"; }
fail() { printf 'SMOKE FAIL: %s\n' "$*" >&2; exit 1; }

cleanup() {
  status=$?
  if [ "${status}" -ne 0 ]; then
    compose ps || true
    compose logs --tail 80 || true
  fi
  compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  # The replay directory is owned by the container user (uid 10001).
  docker run --rm -v "${WORK}:/w" alpine:3 rm -rf /w/replays >/dev/null 2>&1 || true
  rm -rf "${WORK}"
  exit "${status}"
}
trap cleanup EXIT

mkdir -p "${WORK}/replays"
docker run --rm -v "${WORK}/replays:/d" alpine:3 chown 10001:10001 /d
cat >"${ENV_FILE}" <<EOF
NETHER_EARTH_VERSION=${VERSION}
NETHER_EARTH_PUBLIC_BASE_URL=${BASE}
NETHER_EARTH_REPLAY_HOST_DIR=${WORK}/replays
NETHER_EARTH_HTTP_PORT=${PORT}
EOF

step "start stack (version ${VERSION})"
if [ "${SMOKE_NO_BUILD:-0}" = "1" ]; then
  compose up -d --wait --wait-timeout 180
else
  compose up -d --build --wait --wait-timeout 300
fi

step "health, readiness, static frontend"
[ "$(curl -fsS "${BASE}/healthz")" = "ok" ] || fail "gateway /healthz"
curl -fsS "${BASE}/api/health" | grep -q '"status":"ok"' || fail "backend /api/health"
curl -fsS "${BASE}/api/ready" | grep -q '"status":"ready"' || fail "backend /api/ready"
curl -fsS "${BASE}/" | grep -q '<div id="app">' || fail "frontend index"
headers="$(curl -fsSI "${BASE}/" | tr -d '\r')"
for header in content-security-policy x-frame-options x-content-type-options; do
  grep -qi "^${header}:" <<<"${headers}" || fail "missing ${header} header"
done
grep -qi '^server: nginx$' <<<"${headers}" || fail "server version not hidden"
[ "$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/api/docs")" = "404" ] || fail "API docs exposed"
[ "$(curl -s -o /dev/null -w '%{http_code}' -X POST "${BASE}/api/health")" = "403" ] || fail "non-GET API allowed"

step "WebSocket origin policy"
ws_status() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 3 -H "Origin: $1" \
    -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
    -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "${BASE}/ws" || true
}
[ "$(ws_status "${BASE}")" = "101" ] || fail "own-origin WebSocket upgrade"
[ "$(ws_status "https://evil.example")" = "403" ] || fail "foreign-origin WebSocket accepted"

step "two-client match through the gateway"
NE_WS_URL="ws://localhost:${PORT}/ws" node ../frontend/scripts/live-two-client.mjs

step "replay artifact persisted on the host"
replays() { docker run --rm -v "${WORK}/replays:/r:ro" alpine:3 sh -c 'ls /r/*/meta.json 2>/dev/null | wc -l'; }
count="$(replays)"
[ "${count}" -ge 1 ] || fail "no replay artifact written"
backend_logs="$(compose logs --no-log-prefix backend)"
grep -q '"event": "match_started"' <<<"${backend_logs}" || fail "no structured match_started log"
grep -q '"event": "replay_started"' <<<"${backend_logs}" || fail "no structured replay_started log"

step "restart backend: health recovers, replays survive"
compose restart backend >/dev/null
compose up -d --wait --wait-timeout 120 >/dev/null
# The restarted container can still report its previous health for a moment
# and the gateway needs a new upstream connection: poll instead of one probe.
ready=0
for _ in $(seq 1 60); do
  if curl -fsS "${BASE}/api/ready" 2>/dev/null | grep -q '"status":"ready"'; then ready=1; break; fi
  sleep 1
done
[ "${ready}" = 1 ] || fail "not ready after restart"
[ "$(replays)" -ge "${count}" ] || fail "replay artifacts lost on restart"
NE_WS_URL="ws://localhost:${PORT}/ws" node ../frontend/scripts/live-two-client.mjs >/dev/null \
  || fail "match flow after restart"

step "SMOKE OK"
