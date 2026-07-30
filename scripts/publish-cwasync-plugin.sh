#!/usr/bin/env bash
set -euo pipefail

# Publish the bundled KOReader plugin to its dedicated repository.
# Dry-run is the default. The first (and every) release must reference an
# already-published CWNG tag whose plugin version matches the tag exactly.
#
# --auto is the release-workflow entry point. It differs from --publish in one
# way only: when the plugin is byte-identical to what the dedicated repository
# already ships, --auto exits 0 ("nothing owed") instead of failing. A human
# typing --publish asked for a release and deserves an error when there is
# nothing to release; a release-triggered workflow runs on EVERY app tag and
# most of those tags do not touch the plugin.
#
# The owed-check therefore runs BEFORE the version check. That ordering is what
# keeps an unchanged plugin quiet while making a changed-but-unbumped plugin
# loud, instead of the reverse (fork #400: three releases shipped with the
# dedicated repository silently stuck on an older plugin).

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE="$ROOT/koreader/plugins/cwasync.koplugin"
TARGET_REPO="new-usemame/cwasync.koplugin"
PUBLISH=0
AUTO=0
TAG=""

usage() {
    printf 'Usage: %s --tag vX.Y.Z [--publish | --auto]\n' "$0"
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
        --auto)
            PUBLISH=1
            AUTO=1
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

active_account=$(gh api user --jq .login)
[[ "$active_account" == "new-usemame" ]] || {
    printf 'ERROR: active GitHub account is %s, expected new-usemame\n' "$active_account" >&2
    exit 1
}

# Bring the application release's asset up to what the dedicated repository has
# already shipped for this tag. Copies the asset down from the dedicated release
# rather than rebuilding it, so the two streams are byte-identical by
# construction. Skips when the asset is already present: a re-run must not mutate
# a published release it has nothing to change.
reconcile_app_release_asset() {
    local present
    present=$(gh release view "$TAG" --repo new-usemame/Calibre-Web-NextGen \
        --json assets --jq '[.assets[].name] | index("cwasync.koplugin.zip") // "no"' 2>/dev/null \
        || printf 'no')
    if [[ "$present" != "no" && -n "$present" ]]; then
        printf 'Application release %s already carries cwasync.koplugin.zip; nothing to reconcile.\n' \
            "$TAG"
        return 0
    fi
    printf 'Dedicated release %s exists but the application release carries no asset — reconciling.\n' \
        "$TAG"
    mkdir -p "$tmp/reconcile"
    gh release download "$TAG" --repo "$TARGET_REPO" \
        --pattern cwasync.koplugin.zip --dir "$tmp/reconcile"
    gh release upload "$TAG" "$tmp/reconcile/cwasync.koplugin.zip" \
        --repo new-usemame/Calibre-Web-NextGen
    printf 'Attached cwasync.koplugin.zip to application release %s.\n' "$TAG"
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/cwasync-release.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
git clone --quiet "https://github.com/$TARGET_REPO.git" "$tmp/repo"
mkdir -p "$tmp/repo/cwasync.koplugin"
rsync -a --delete --exclude='.DS_Store' "$SOURCE/" "$tmp/repo/cwasync.koplugin/"

# Is a publish owed? The dedicated repository's main branch IS the last thing we
# shipped, so staging the synced tree answers it exactly. `git add <dir>` stages
# additions, modifications and deletions alike, so a removed plugin file counts
# as a change too.
git -C "$tmp/repo" add cwasync.koplugin
publish_owed=1
git -C "$tmp/repo" diff --cached --quiet && publish_owed=0

if ((publish_owed == 0)); then
    if ((AUTO == 1)); then
        # An unchanged plugin means one of two things and the owed-check alone
        # cannot tell them apart: either this tag never touched the plugin — the
        # common case, nothing to publish and nothing to attach — or an earlier
        # run for THIS tag already pushed the plugin and then died before the
        # application asset landed. The second case used to exit 0 right here with
        # the asset still missing, so one transient upload failure stranded the
        # release *and* reported success on every retry: the same silently-missing
        # asset this script exists to prevent. A dedicated release for this exact
        # tag is the discriminator, so reconcile instead of assuming.
        if gh release view "$TAG" --repo "$TARGET_REPO" >/dev/null 2>&1; then
            reconcile_app_release_asset
            exit 0
        fi
        printf 'Nothing owed: plugin is identical to the one %s already ships. Skipping.\n' \
            "$TARGET_REPO"
        exit 0
    fi
    printf 'ERROR: plugin source is unchanged; refusing a no-op release\n' >&2
    exit 1
fi

# Only reached when the plugin genuinely changed. A version that does not match
# the tag is now a hard error rather than a silent skip: the plugin moved, so it
# MUST ship, and shipping it under a mismatched version would lie to Updates
# Manager's installed-vs-latest comparison.
meta_version=$(sed -n 's/.*version = "\([0-9][0-9.]*\)".*/\1/p' "$SOURCE/_meta.lua" | head -1)
main_version=$(sed -n 's/.*version = "\([0-9][0-9.]*\)".*/\1/p' "$SOURCE/main.lua" | head -1)
expected_version=${TAG#v}
if [[ "$meta_version" != "$expected_version" || "$main_version" != "$expected_version" ]]; then
    printf 'ERROR: the plugin changed since %s last shipped, so tag %s owes it a release, but\n' \
        "$TARGET_REPO" "$TAG" >&2
    printf '       _meta.lua / main.lua declare %s / %s instead of %s.\n' \
        "$meta_version" "$main_version" "$expected_version" >&2
    printf '       Bump both to %s and re-run.\n' "$expected_version" >&2
    exit 1
fi

# Publishing from an unreleased commit breaks the source-of-truth contract.
gh release view "$TAG" --repo new-usemame/Calibre-Web-NextGen >/dev/null || {
    printf 'ERROR: CWNG release %s is not published\n' "$TAG" >&2
    exit 1
}

if gh release view "$TAG" --repo "$TARGET_REPO" >/dev/null 2>&1; then
    printf 'ERROR: dedicated plugin release %s already exists\n' "$TAG" >&2
    exit 1
fi

(
    cd "$tmp/repo"
    rm -f cwasync.koplugin.zip
    zip -qr cwasync.koplugin.zip cwasync.koplugin
    # Read the listing once, then match — piping unzip into `grep -q` trips SIGPIPE
    # under `set -o pipefail` (grep exits on first match, unzip dies 141) and aborts.
    zip_listing=$(unzip -Z1 cwasync.koplugin.zip)
    grep -qx 'cwasync.koplugin/main.lua' <<<"$zip_listing"
    grep -qx 'cwasync.koplugin/_meta.lua' <<<"$zip_listing"
)

if ((PUBLISH == 0)); then
    printf 'DRY RUN: validated %s for %s\n' "$TAG" "$TARGET_REPO"
    git -C "$tmp/repo" status --short
    unzip -l "$tmp/repo/cwasync.koplugin.zip"
    exit 0
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

# The application release gets the same validated zip — but only from here, inside
# the publish path, which is reached only when the plugin actually changed. That
# distinction is the whole reason plugin-release-asset.yml was deleted: it
# attached a zip to EVERY app tag, so Updates Manager pointed at the application
# repository reported an update on releases that never touched the plugin (the
# concern @SpookyUSAF raised on fork #400).
#
# Why attach at all, when the dedicated repository is the update source: devices
# configured before the plugin moved are still pointed at the application
# repository, and v4.1.16 publicly restored this download. Without automation that
# restore lasted exactly one release — v4.1.17 through v4.1.25 shipped no asset at
# all, so Updates Manager saw v4.1.16 as the newest release carrying one and
# answered "no new release available" while newer plugins existed (fork #1253).
# A failure here is loud because a silently missing asset is precisely the
# regression being fixed; the retry is handled by reconcile_app_release_asset()
# above, which is what makes a failed upload recoverable — not --clobber. What
# --clobber covers is an asset this script did not place, such as the manual
# v4.1.16 upload. Replacing one is a mutation of a published release, so say so
# rather than doing it quietly.
if [[ -n "$(gh release view "$TAG" --repo new-usemame/Calibre-Web-NextGen \
    --json assets --jq '.assets[] | select(.name=="cwasync.koplugin.zip") | .name' 2>/dev/null)" ]]; then
    printf 'NOTE: replacing the existing cwasync.koplugin.zip on application release %s.\n' "$TAG"
fi
gh release upload "$TAG" "$tmp/repo/cwasync.koplugin.zip" \
    --repo new-usemame/Calibre-Web-NextGen --clobber

printf 'Published %s to https://github.com/%s/releases/tag/%s\n' "$TAG" "$TARGET_REPO" "$TAG"
printf 'Attached cwasync.koplugin.zip to https://github.com/new-usemame/Calibre-Web-NextGen/releases/tag/%s\n' "$TAG"
