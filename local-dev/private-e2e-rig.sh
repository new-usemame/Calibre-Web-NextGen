#!/usr/bin/env bash
# Private E2E rig: build one worktree into an isolated app container, then run
# Playwright from its version-matched Linux container. No shared cwn-local
# container, host browser, or host Playwright installation participates.
#
#   ./local-dev/private-e2e-rig.sh up   <worktree> [port]
#   ./local-dev/private-e2e-rig.sh test <worktree> [playwright arguments...]
#   ./local-dev/private-e2e-rig.sh down <worktree>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:?up|test|down}"
WT="${2:?worktree path}"
[ -d "$WT" ] || WT="$ROOT/.worktrees/$WT"
[ -d "$WT/cps" ] || { echo "not a worktree: $WT" >&2; exit 2; }
WT="$(cd "$WT" && pwd)"

SLUG="$(basename "$WT" | tr -cd 'A-Za-z0-9_.-')"
[ -n "$SLUG" ] || { echo "worktree basename has no safe container-name characters" >&2; exit 2; }
NAME="cwn-rig-$SLUG"
RUNNER_NAME="$NAME-playwright"
TAG="calibre-web-nextgen:rig-$SLUG"
PLAYWRIGHT_IMAGE="mcr.microsoft.com/playwright:v1.62.1-noble"
STATE="$ROOT/local-dev/.rig-$SLUG"
PORTFILE="$STATE/port"
IMAGEFILE="$STATE/image-id"

served_bundle() {
  docker exec "$NAME" sh -c \
    "grep -o 'index-[A-Za-z0-9_-]*\.js' /app/calibre-web-automated/cps/static/app/index.html | head -1"
}

served_bundle_sha() {
  local bundle="$1"
  docker exec "$NAME" sha256sum "/app/calibre-web-automated/cps/static/app/assets/$bundle" | awk '{print $1}'
}

assert_identity() {
  [ -f "$IMAGEFILE" ] || { echo "IDENTITY FAIL: missing recorded image id; run up first" >&2; exit 1; }
  local expected_id running_id running_tag bundle
  expected_id="$(cat "$IMAGEFILE")"
  running_id="$(docker inspect "$NAME" --format '{{.Image}}')"
  running_tag="$(docker inspect "$NAME" --format '{{.Config.Image}}')"
  [ "$running_id" = "$expected_id" ] || {
    echo "IDENTITY FAIL: container image id [$running_id] != built image id [$expected_id]" >&2; exit 1; }
  [ "$running_tag" = "$TAG" ] || {
    echo "IDENTITY FAIL: container runs [$running_tag], not worktree image [$TAG]" >&2; exit 1; }

  bundle="$(served_bundle)"
  [ -n "$bundle" ] || { echo "IDENTITY FAIL: served SPA bundle was not found" >&2; exit 1; }
  docker exec "$NAME" grep -Fq "catalog-grid" "/app/calibre-web-automated/cps/static/app/assets/$bundle" || {
    echo "IDENTITY FAIL: served bundle $bundle lacks marker [catalog-grid]" >&2; exit 1; }
  printf '%s\n' "$bundle"
}

private_seed_root() {
  local common_root candidate
  common_root="$(dirname "$(git -C "$WT" rev-parse --path-format=absolute --git-common-dir)")"
  for candidate in "$ROOT" "$common_root" "$(dirname "$common_root")"; do
    if [ -f "$candidate/local-dev/config/app.db" ]; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  while IFS= read -r candidate; do
    if [ -f "$candidate/local-dev/config/app.db" ]; then
      printf '%s\n' "$candidate"
      return
    fi
  done < <(git -C "$WT" worktree list --porcelain | sed -n 's/^worktree //p')
  echo "private rig seed not found in this checkout or the primary worktree" >&2
  exit 1
}

case "$ACTION" in
up)
  PORT="${3:-}"
  if [ -z "$PORT" ]; then
    for candidate in $(seq 18086 18120); do
      if ! lsof -i ":$candidate" >/dev/null 2>&1; then PORT="$candidate"; break; fi
    done
  fi
  [ -n "$PORT" ] || { echo "no free private-rig port" >&2; exit 1; }

  rm -rf "$STATE"
  mkdir -p "$STATE"
  echo "== building $TAG from the requested worktree =="
  docker build -t "$TAG" "$WT" >"$STATE/build.log" 2>&1 || {
    tail -30 "$STATE/build.log"; exit 1; }
  docker image inspect "$TAG" --format '{{.Id}}' > "$IMAGEFILE"

  SEED_ROOT="$(private_seed_root)"
  for directory in config library ingest; do
    mkdir -p "$STATE/$directory"
    cp -a "$SEED_ROOT/local-dev/$directory/." "$STATE/$directory/" 2>/dev/null || true
  done
  echo "$PORT" > "$PORTFILE"

  docker rm -f "$RUNNER_NAME" "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" -p "$PORT:8083" \
    -e CWNG_SPA=1 -e PUID=501 -e PGID=20 \
    -v "$STATE/config:/config" \
    -v "$STATE/library:/calibre-library" \
    -v "$STATE/ingest:/cwa-book-ingest" \
    "$TAG" >/dev/null

  echo -n "== waiting for health "
  for _ in $(seq 1 180); do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null || echo starting)"
    if [ "$status" = healthy ]; then echo "ok (private port $PORT)"; break; fi
    if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" != true ]; then
      echo; echo "CONTAINER DIED — boot log:"; docker logs "$NAME" 2>&1 | tail -40; exit 1
    fi
    echo -n .; sleep 2
  done
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$NAME")" = healthy ] || {
    echo; echo "NEVER HEALTHY — do not retry blindly:"; docker logs "$NAME" 2>&1 | tail -40; exit 1; }

  BOOTS="$(docker logs "$NAME" 2>&1 | grep -c 'Starting Calibre-Web' || true)"
  echo "== boots: $BOOTS (>1 means a restart loop; investigate before trusting results)"
  BUNDLE="$(assert_identity)"
  echo "== identity ok — image=$(cat "$IMAGEFILE") bundle=$BUNDLE sha256=$(served_bundle_sha "$BUNDLE")"
  ;;

test)
  shift 2
  [ -f "$PORTFILE" ] || { echo "private rig is not up for $WT" >&2; exit 1; }
  PORT="$(cat "$PORTFILE")"
  BUNDLE="$(assert_identity)"
  BUNDLE_SHA="$(served_bundle_sha "$BUNDLE")"
  echo "== identity ok before run — image=$(cat "$IMAGEFILE") bundle=$BUNDLE sha256=$BUNDLE_SHA"

  docker image inspect "$PLAYWRIGHT_IMAGE" >/dev/null 2>&1 || docker pull "$PLAYWRIGHT_IMAGE"
  mkdir -p "$STATE/node_modules" "$STATE/npm-cache"
  docker rm -f "$RUNNER_NAME" >/dev/null 2>&1 || true
  set +e
  docker run --rm --name "$RUNNER_NAME" \
    --network "container:$NAME" \
    -e NPM_CONFIG_CACHE=/rig-state/npm-cache \
    -e E2E_BASE_URL="http://localhost:8083" \
    -e E2E_USER="${E2E_USER:-admin}" \
    -e E2E_PASS="${E2E_PASS:-admin123}" \
    -e E2E_HOSTILE_LOAD="${E2E_HOSTILE_LOAD:-}" \
    -e E2E_VISUAL_REGRESSION="${E2E_VISUAL_REGRESSION:-}" \
    -v "$WT:/worktree" \
    -v "$STATE/node_modules:/worktree/frontend/node_modules" \
    -v "$STATE:/rig-state" \
    -w /worktree/frontend \
    "$PLAYWRIGHT_IMAGE" bash -lc \
      'npm ci --prefer-offline && test "$(npx playwright --version)" = "Version 1.62.1" && npx playwright test "$@"' \
      -- "$@"
  RC=$?
  set -e

  BUNDLE_AFTER="$(assert_identity)"
  BUNDLE_SHA_AFTER="$(served_bundle_sha "$BUNDLE_AFTER")"
  [ "$BUNDLE" = "$BUNDLE_AFTER" ] && [ "$BUNDLE_SHA" = "$BUNDLE_SHA_AFTER" ] || {
    echo "IDENTITY FAIL AFTER RUN: served bundle changed; discard this result" >&2; exit 1; }
  echo "== identity still ok after run — image=$(cat "$IMAGEFILE") bundle=$BUNDLE_AFTER sha256=$BUNDLE_SHA_AFTER"
  exit "$RC"
  ;;

down)
  docker rm -f "$RUNNER_NAME" "$NAME" >/dev/null 2>&1 || true
  rm -rf "$STATE"
  echo "removed private rig $NAME"
  ;;

*) echo "up|test|down" >&2; exit 2 ;;
esac
