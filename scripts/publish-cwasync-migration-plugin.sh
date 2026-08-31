#!/usr/bin/env bash
set -euo pipefail

# Publish the final cwasync.koplugin release.  Its source is a notice-only
# plugin: it performs no network requests and directs existing installations
# to remove the old directory before installing cwngsync.koplugin.
#
# This script is deliberately one-time and deliberately separate from the
# normal cwngsync publisher.  It refuses to follow the old repository's GitHub
# redirect after the repository has been renamed, and it never replaces the
# application release asset (which must keep serving the full plugin bundled by
# that immutable CWNG tag).

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE="$ROOT/koreader/legacy/cwasync.koplugin"
TARGET_REPO="new-usemame/cwasync.koplugin"
PUBLISH=0
TAG=""

usage() {
    printf 'Usage: %s --tag vX.Y.Z [--publish]\n' "$0"
}

while (($#)); do
    case "$1" in
        --tag)
            TAG=${2:-}
            shift 2
            ;;
        --publish)
            PUBLISH=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf 'ERROR: --tag must look like vX.Y.Z\n' >&2
    exit 2
fi

for command in git gh zip unzip rsync; do
    command -v "$command" >/dev/null || {
        printf 'ERROR: required command is missing: %s\n' "$command" >&2
        exit 1
    }
done

[[ -f "$SOURCE/_meta.lua" && -f "$SOURCE/main.lua" ]] || {
    printf 'ERROR: legacy notice source is incomplete: %s\n' "$SOURCE" >&2
    exit 1
}

active_account=$(gh api user --jq .login)
[[ "$active_account" == "new-usemame" ]] || {
    printf 'ERROR: active GitHub account is %s, expected new-usemame\n' "$active_account" >&2
    exit 1
}

# A renamed repository answers the old URL through a redirect.  Publishing the
# notice after that point would mutate cwngsync's release stream, so require the
# canonical name itself to still be the legacy name.
canonical_repo=$(gh repo view "$TARGET_REPO" --json nameWithOwner --jq .nameWithOwner)
[[ "$canonical_repo" == "$TARGET_REPO" ]] || {
    printf 'ERROR: %s now resolves to %s; the one-time legacy release window is closed\n' \
        "$TARGET_REPO" "$canonical_repo" >&2
    exit 1
}

meta_version=$(sed -n 's/.*version = "\([0-9][0-9.]*\)".*/\1/p' "$SOURCE/_meta.lua" | head -1)
main_version=$(sed -n 's/.*version = "\([0-9][0-9.]*\)".*/\1/p' "$SOURCE/main.lua" | head -1)
expected_version=${TAG#v}
if [[ "$meta_version" != "$expected_version" || "$main_version" != "$expected_version" ]]; then
    printf 'ERROR: legacy _meta.lua / main.lua declare %s / %s instead of %s\n' \
        "$meta_version" "$main_version" "$expected_version" >&2
    exit 1
fi

gh release view "$TAG" --repo new-usemame/Calibre-Web-NextGen >/dev/null || {
    printf 'ERROR: CWNG release %s is not published\n' "$TAG" >&2
    exit 1
}

if gh release view "$TAG" --repo "$TARGET_REPO" >/dev/null 2>&1; then
    printf 'ERROR: final legacy release %s already exists\n' "$TAG" >&2
    exit 1
fi

tmp=$(mktemp -d "${TMPDIR:-/tmp}/cwasync-migration-release.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
git clone --quiet "https://github.com/$TARGET_REPO.git" "$tmp/repo"
mkdir -p "$tmp/repo/cwasync.koplugin"
rsync -a --delete --exclude='.DS_Store' "$SOURCE/" "$tmp/repo/cwasync.koplugin/"
git -C "$tmp/repo" add cwasync.koplugin

if git -C "$tmp/repo" diff --cached --quiet; then
    printf 'ERROR: legacy notice source is unchanged; refusing a no-op release\n' >&2
    exit 1
fi

(
    cd "$tmp/repo"
    rm -f cwasync.koplugin.zip
    zip -qr cwasync.koplugin.zip cwasync.koplugin
    zip_listing=$(unzip -Z1 cwasync.koplugin.zip)
    grep -qx 'cwasync.koplugin/main.lua' <<<"$zip_listing"
    grep -qx 'cwasync.koplugin/_meta.lua' <<<"$zip_listing"
)

if ((PUBLISH == 0)); then
    printf 'DRY RUN: validated final cwasync migration notice for %s\n' "$TAG"
    git -C "$tmp/repo" status --short
    unzip -l "$tmp/repo/cwasync.koplugin.zip"
    exit 0
fi

git -C "$tmp/repo" -c user.name='new-usemame' \
    -c user.email='248195428+new-usemame@users.noreply.github.com' \
    commit -m "release: final cwasync migration notice $TAG"
git -C "$tmp/repo" -c user.name='new-usemame' \
    -c user.email='248195428+new-usemame@users.noreply.github.com' \
    tag -a "$TAG" -m "Final cwasync migration notice $TAG"
git -C "$tmp/repo" push origin HEAD:main "$TAG"
gh release create "$TAG" "$tmp/repo/cwasync.koplugin.zip" \
    --repo "$TARGET_REPO" \
    --title "Final cwasync migration notice $TAG" \
    --notes "This final legacy release performs no synchronization. Remove cwasync.koplugin, install cwngsync.koplugin, and restart KOReader."

printf 'Published the final cwasync migration notice %s to %s\n' "$TAG" "$TARGET_REPO"
