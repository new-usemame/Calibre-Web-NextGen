#!/bin/bash
# Deterministic post-release wiki sync for Calibre-Web-NextGen.
#
# The GitHub wiki is a BUILD ARTIFACT of repo/README.md + repo/docs (see
# scripts/generate-wiki.py and wiki-src/*.md). This script regenerates the wiki
# from the current repo docs and pushes it to the .wiki.git remote so the wiki
# never drifts from the README. It is the steady-state engine; the interactive
# twin is the `wiki-sync` skill.
#
# Runs as a post-publish step of the release train (same window as
# post-release-outreach.sh) but is safe to run any time — it only pushes when
# the rendered output actually differs from what's live.
#
# OVERLAY, never mirror: generated pages are copied OVER the live wiki clone;
# pages this generator does not own (e.g. Contributing-Translations.md, written
# by the update-translations CI workflow) are left untouched. We never `git rm`.
#
# Usage:
#   ./scripts/sync-wiki.sh              # dry-run (default): render + show diff
#   ./scripts/sync-wiki.sh --apply      # commit + push the wiki
#
# Hard preconditions (refuse if any fail):
#   1. CWD resolves to the project root (script lives in scripts/)
#   2. gh active account must be new-usemame
#   3. repo/ working tree must be clean
#   4. the generator must succeed (its DRIFT TRIPWIRE is a hard failure:
#      a new README section with nowhere to go stops the sync on purpose)

set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJ"

APPLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --apply)   APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

WIKI_REPO="new-usemame/Calibre-Web-NextGen"
NAME='new-usemame'
EMAIL='248195428+new-usemame@users.noreply.github.com'

# --- Preconditions ---------------------------------------------------------
if [ ! -f "$PROJ/scripts/generate-wiki.py" ] || [ ! -d "$PROJ/wiki-src" ]; then
  echo "ABORT: generator or wiki-src/ not found under $PROJ" >&2; exit 1
fi
ACTIVE_USER="$(gh api user --jq .login 2>/dev/null || echo '')"
if [ "$ACTIVE_USER" != "new-usemame" ]; then
  echo "ABORT: gh active user is '$ACTIVE_USER', expected 'new-usemame'." >&2
  echo "       Run: gh auth switch --user new-usemame" >&2
  exit 1
fi
if [ -d "$PROJ/repo/.git" ] && [ -n "$(git -C "$PROJ/repo" status --porcelain)" ]; then
  echo "ABORT: repo/ working tree is not clean — refusing to sync mid-edit" >&2
  exit 1
fi

mode="DRY-RUN"; [ "$APPLY" = 1 ] && mode="APPLY"
echo "wiki-sync [$mode]"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
OUT="$TMP/out"
CLONE="$TMP/wiki"

# --- Render (tripwire lives here) -----------------------------------------
python3 "$PROJ/scripts/generate-wiki.py" --repo "$PROJ" --src "$PROJ/wiki-src" --out "$OUT"

# --- Clone live wiki -------------------------------------------------------
TOKEN="$(gh auth token)"
if ! git clone -q "https://x-access-token:${TOKEN}@github.com/${WIKI_REPO}.wiki.git" "$CLONE" 2>/dev/null; then
  echo "ABORT: could not clone ${WIKI_REPO}.wiki.git — has the wiki been" >&2
  echo "       initialized? Create one page in the Wiki tab, then re-run." >&2
  exit 1
fi

# --- Overlay generated pages onto the clone (never delete foreign pages) ---
cp "$OUT"/*.md "$CLONE"/

# Warn about live pages we don't manage (informational only; never deleted).
for f in "$CLONE"/*.md; do
  b="$(basename "$f")"
  [ -f "$OUT/$b" ] && continue
  echo "  note: leaving unmanaged wiki page in place: $b"
done

cd "$CLONE"
if [ -z "$(git status --porcelain)" ]; then
  echo "wiki already in sync — nothing to push."
  exit 0
fi

echo "=== changes ==="
git -c color.ui=never diff --stat
echo "==============="

if [ "$APPLY" != 1 ]; then
  echo "DRY-RUN: not pushing. Re-run with --apply to publish."
  exit 0
fi

git -c user.name="$NAME" -c user.email="$EMAIL" add -A
git -c user.name="$NAME" -c user.email="$EMAIL" \
    commit -qm "Sync wiki from repo docs (generate-wiki.py)"
git push -q origin HEAD
echo "pushed wiki update to ${WIKI_REPO}/wiki"

# --- Ledger line so the release train can see it happened -------------------
if [ -f "$PROJ/scripts/log-event.sh" ]; then
  bash "$PROJ/scripts/log-event.sh" wiki-synced from=repo-docs 2>/dev/null || true
elif [ -f "$PROJ/state/completed.log" ]; then
  printf 'wiki-synced\tfrom=repo-docs\n' >> "$PROJ/state/completed.log"
fi
