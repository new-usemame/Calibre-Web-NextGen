#!/usr/bin/env bash
set -euo pipefail

# Publish the bundled KOReader plugin to its dedicated repository.
# Dry-run is the default. The first (and every) release must reference an
# already-published CWNG tag whose plugin version matches the tag exactly.

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE="$ROOT/koreader/plugins/cwasync.koplugin"
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
    printf 'ERROR: plugin source is incomplete: %s\n' "$SOURCE" >&2
    exit 1
}

meta_version=$(sed -n 's/.*version = "\([0-9][0-9.]*\)".*/\1/p' "$SOURCE/_meta.lua" | head -1)
main_version=$(sed -n 's/.*version = "\([0-9][0-9.]*\)".*/\1/p' "$SOURCE/main.lua" | head -1)
expected_version=${TAG#v}
if [[ "$meta_version" != "$expected_version" || "$main_version" != "$expected_version" ]]; then
    printf 'ERROR: tag %s requires _meta.lua and main.lua version %s; found %s / %s\n' \
        "$TAG" "$expected_version" "$meta_version" "$main_version" >&2
    exit 1
fi

# Publishing from an unreleased commit breaks the source-of-truth contract.
gh release view "$TAG" --repo new-usemame/Calibre-Web-NextGen >/dev/null || {
    printf 'ERROR: CWNG release %s is not published\n' "$TAG" >&2
    exit 1
}

active_account=$(gh api user --jq .login)
[[ "$active_account" == "new-usemame" ]] || {
    printf 'ERROR: active GitHub account is %s, expected new-usemame\n' "$active_account" >&2
    exit 1
}

if gh release view "$TAG" --repo "$TARGET_REPO" >/dev/null 2>&1; then
    printf 'ERROR: dedicated plugin release %s already exists\n' "$TAG" >&2
    exit 1
fi

tmp=$(mktemp -d "${TMPDIR:-/tmp}/cwasync-release.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
git clone --quiet "https://github.com/$TARGET_REPO.git" "$tmp/repo"
mkdir -p "$tmp/repo/cwasync.koplugin"
rsync -a --delete --exclude='.DS_Store' "$SOURCE/" "$tmp/repo/cwasync.koplugin/"

(
    cd "$tmp/repo"
    rm -f cwasync.koplugin.zip
    zip -qr cwasync.koplugin.zip cwasync.koplugin
    unzip -Z1 cwasync.koplugin.zip | grep -qx 'cwasync.koplugin/main.lua'
    unzip -Z1 cwasync.koplugin.zip | grep -qx 'cwasync.koplugin/_meta.lua'
)

if ((PUBLISH == 0)); then
    printf 'DRY RUN: validated %s for %s\n' "$TAG" "$TARGET_REPO"
    git -C "$tmp/repo" status --short
    unzip -l "$tmp/repo/cwasync.koplugin.zip"
    exit 0
fi

git -C "$tmp/repo" add cwasync.koplugin
if git -C "$tmp/repo" diff --cached --quiet; then
    printf 'ERROR: plugin source is unchanged; refusing a no-op release\n' >&2
    exit 1
fi
git -C "$tmp/repo" -c user.name='new-usemame' \
    -c user.email='248195428+new-usemame@users.noreply.github.com' \
    commit -m "release: $TAG"
git -C "$tmp/repo" -c user.name='new-usemame' \
    -c user.email='248195428+new-usemame@users.noreply.github.com' \
    tag -a "$TAG" -m "NextGen Progress Sync $TAG"
git -C "$tmp/repo" push origin HEAD:main "$TAG"
gh release create "$TAG" "$tmp/repo/cwasync.koplugin.zip" \
    --repo "$TARGET_REPO" \
    --title "NextGen Progress Sync $TAG" \
    --notes "Built from the published Calibre-Web NextGen $TAG source tag."

printf 'Published %s to https://github.com/%s/releases/tag/%s\n' "$TAG" "$TARGET_REPO" "$TAG"
