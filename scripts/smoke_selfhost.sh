#!/usr/bin/env bash
set -Eeuo pipefail

# End-to-end smoke test for the public self-host images. Every resource gets a
# per-process project name and is removed on exit, so local stacks are never
# reused or modified accidentally.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="$repo_root/infra/docker/compose.selfhost.yml"
project="${EDECAN_SMOKE_PROJECT:-edecan-oss-smoke-$$}"

if [[ ! "$project" =~ ^[a-z0-9][a-z0-9_-]{0,48}$ ]]; then
  echo "EDECAN_SMOKE_PROJECT must match [a-z0-9][a-z0-9_-]{0,48}" >&2
  exit 2
fi

api_container="${project}-api-smoke"
web_container="${project}-web-smoke"
network="${project}_default"
compose=(docker compose -p "$project" -f "$compose_file")

cleanup() {
  docker rm -f "$api_container" "$web_container" >/dev/null 2>&1 || true
  "${compose[@]}" down -v --remove-orphans --rmi local >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

wait_for_health() {
  local container="$1"
  local status=""
  local attempt

  for attempt in {1..60}; do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
    case "$status" in
      healthy)
        return 0
        ;;
      exited|dead)
        docker logs "$container" >&2 || true
        return 1
        ;;
    esac
    sleep 1
  done

  echo "Timed out waiting for $container (last status: ${status:-missing})" >&2
  docker logs "$container" >&2 || true
  return 1
}

command -v docker >/dev/null 2>&1 || {
  echo "Docker is required for the self-host smoke test." >&2
  exit 2
}
docker info >/dev/null

"${compose[@]}" build migrate api worker web
"${compose[@]}" up -d --wait --wait-timeout 60 postgres redis
"${compose[@]}" run --rm \
  -e DATABASE_URL=postgresql+asyncpg://edecan:edecan@postgres:5432/edecan \
  migrate

docker run --rm -d \
  --name "$api_container" \
  --network "$network" \
  -e ENV=dev \
  -e DATABASE_URL=postgresql+asyncpg://edecan:edecan@postgres:5432/edecan \
  -e REDIS_URL=redis://redis:6379/0 \
  -e JWT_SECRET=smoke-test-secret-not-for-production-0123456789 \
  -e LOCAL_MASTER_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= \
  "${project}-api:latest" >/dev/null

docker run --rm -d \
  --name "$web_container" \
  --network "$network" \
  "${project}-web:latest" >/dev/null

wait_for_health "$api_container"
wait_for_health "$web_container"

docker exec "$api_container" python -c \
  "import json, urllib.request; response = urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3); assert response.status == 200; assert json.load(response) == {'status': 'ok'}"

docker exec "$web_container" node -e \
  "fetch('http://127.0.0.1:3000/').then((response) => { if (!response.ok || !response.headers.get('content-security-policy')) process.exit(1) }).catch(() => process.exit(1))"

test "$(docker exec "$api_container" id -u)" != "0"
test "$(docker exec "$web_container" id -u)" != "0"

docker run --rm "${project}-worker:latest" python -c \
  "import edecan_worker.main; print('worker import: ok')"

echo "Self-host smoke test passed: migrations, API readiness, web CSP, worker import, non-root users."
