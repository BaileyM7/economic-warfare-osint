#!/usr/bin/env bash
# Start (or stop) a local Redis 8 with the Query Engine for developing the
# semantic features. Thin wrapper over docker-compose.yml.
#
#   scripts/dev-redis.sh up      # start Redis 8 on localhost:6379
#   scripts/dev-redis.sh down    # stop it
#   scripts/dev-redis.sh caps    # print what the running instance supports
#
# Then: export REDIS_URL=redis://localhost:6379/0  (+ VOYAGE_API_KEY for embeddings)
set -euo pipefail
cd "$(dirname "$0")/.."

cmd="${1:-up}"
case "$cmd" in
  up)
    docker compose up -d redis
    echo "waiting for Redis…"
    for _ in $(seq 1 20); do
      if docker compose exec -T redis redis-cli ping >/dev/null 2>&1; then break; fi
      sleep 0.5
    done
    echo "Redis 8 up on localhost:6379"
    echo "  export REDIS_URL=redis://localhost:6379/0"
    ;;
  down)
    docker compose down
    ;;
  caps)
    echo -n "search (FT._LIST): "; docker compose exec -T redis redis-cli FT._LIST >/dev/null 2>&1 && echo "yes" || echo "NO"
    echo -n "json   (JSON.SET): "; docker compose exec -T redis redis-cli JSON.SET _probe '$' '{}' >/dev/null 2>&1 && { echo "yes"; docker compose exec -T redis redis-cli DEL _probe >/dev/null; } || echo "NO"
    ;;
  *)
    echo "usage: $0 {up|down|caps}" >&2
    exit 2
    ;;
esac
