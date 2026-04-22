#!/usr/bin/env bash
# Smoke-test a deployed swarm backend from Emissary's perspective.
#
# Usage:
#   scripts/check-swarm-connection.sh https://swarm-backend.fly.dev https://emissary.example.com
#
# The first arg is the swarm backend URL; the second is the Emissary frontend
# origin that swarm's CORS allowlist must include. Both are required.
#
# Exit codes:
#   0 — all checks pass
#   1 — at least one check failed; details printed

set -u

SWARM=${1:-}
EMISSARY_ORIGIN=${2:-}

if [[ -z "$SWARM" || -z "$EMISSARY_ORIGIN" ]]; then
  echo "usage: $0 <swarm-backend-url> <emissary-frontend-origin>"
  echo "example: $0 https://swarm-backend.fly.dev https://emissary.example.com"
  exit 1
fi

SWARM="${SWARM%/}"   # strip trailing slash

pass=0
fail=0

check() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  PASS  $name"
    pass=$((pass+1))
  else
    echo "  FAIL  $name"
    fail=$((fail+1))
  fi
}

echo "Swarm backend: $SWARM"
echo "Emissary origin: $EMISSARY_ORIGIN"
echo

# 1. Reachable + healthy
health_status=$(curl -s -o /dev/null -w "%{http_code}" "$SWARM/healthz" || echo "000")
if [[ "$health_status" == "200" ]]; then
  echo "  PASS  /healthz returns 200"
  pass=$((pass+1))
else
  echo "  FAIL  /healthz returns $health_status (expected 200)"
  fail=$((fail+1))
fi

# 2. Countries populated (migrations + seed ran)
countries=$(curl -s "$SWARM/api/countries" 2>/dev/null || echo "")
count=$(echo "$countries" | grep -o '"iso3"' | wc -l | tr -d ' ')
if [[ "$count" -ge 10 ]]; then
  echo "  PASS  /api/countries returns $count countries"
  pass=$((pass+1))
else
  echo "  FAIL  /api/countries returned $count countries (expected ≥10) — migrations may not have seeded"
  fail=$((fail+1))
fi

# 3. CORS preflight allows Emissary origin
cors_header=$(curl -s -X OPTIONS "$SWARM/api/scenarios" \
  -H "Origin: $EMISSARY_ORIGIN" \
  -H "Access-Control-Request-Method: POST" \
  -D - -o /dev/null 2>/dev/null | grep -i "^access-control-allow-origin:" | tr -d '\r')
if echo "$cors_header" | grep -q "$EMISSARY_ORIGIN"; then
  echo "  PASS  CORS allows $EMISSARY_ORIGIN"
  pass=$((pass+1))
else
  echo "  FAIL  CORS preflight did not echo back $EMISSARY_ORIGIN (got: '$cors_header')"
  fail=$((fail+1))
fi

# 4. WebSocket endpoint upgradeable (HTTP 101 or 426)
ws_url="${SWARM/https:/wss:}"
ws_url="${ws_url/http:/ws:}"
ws_status=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGVzdA==" \
  "$SWARM/ws/simulations/smoketest" 2>/dev/null || echo "000")
# 101 = upgraded, 426 = upgrade required (expected without proper handshake), 403 = auth
if [[ "$ws_status" =~ ^(101|426|403|404)$ ]]; then
  echo "  PASS  WebSocket endpoint responds (HTTP $ws_status)"
  pass=$((pass+1))
else
  echo "  FAIL  WebSocket endpoint returned $ws_status — expected 101/426/403/404"
  fail=$((fail+1))
fi

echo
echo "  $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
