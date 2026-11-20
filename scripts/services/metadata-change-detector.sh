#!/usr/bin/env bash
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
#
# Watches the metadata change-log folder and runs the cover and metadata
# enforcer once per saved change.

# shellcheck source=_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

# The dispatcher runs cover_enforcer through cwa-as-abc (#2518): a root enforcer
# cannot write a library on a root-squashed network share. cwa-as-abc changes
# only uid/gid, not HOME, so point its calibredb/ebook-polish children at the
# abc-owned, plugin-free runtime config prepared by cwa-init, like the ingest
# service. The explicit user-plugin opt-in can still override this in
# cover_enforcer's per-subprocess environment.
export CALIBRE_CONFIG_DIRECTORY="${CALIBRE_CONFIG_DIRECTORY:-$CWA_CONFIG_DIR/.config/calibre-runtime}"

# Folder to monitor. This has to resolve to the same path the writer uses, or the writer
# writes where nobody is watching and metadata enforcement stops with nothing in the logs
# to say why.
#
# Deliberately does NOT read CALIBRE_DBPATH. Both units that decide where the app actually
# writes -- cwa-init and svc-calibre-web-automated -- `export CALIBRE_DBPATH=/config`
# unconditionally before doing anything, so the app's CONFIG_DIR is /config no matter what
# the operator set. This unit does not clobber it, so deriving from it here would make this
# the single place that could disagree with everyone else: set CALIBRE_DBPATH=/foo in
# compose and the app would still write /config while this watched /foo.
WATCH_FOLDER="${CWA_METADATA_CHANGE_LOGS_DIR:-/config/metadata_change_logs}"
echo "[metadata-change-detector] Watching folder: $WATCH_FOLDER"

# Create the folder if it doesn't exist
mkdir -p "$WATCH_FOLDER"

# Both watcher backends feed change-log *filenames* (one per line) into the
# debouncing dispatcher, which coalesces the burst of duplicate events a single
# save produces into at most one enforcement pass per file and then runs the
# enforcer. This unifies the inotify and polling paths behind one dedup so one
# save == one enforcement pass, with no "not found after 3 attempts" spam
# (fork #802). See scripts/metadata_change_dispatch.py.
DISPATCHER="$CWA_SCRIPTS/metadata_change_dispatch.py"

# Monitor the folder for new files; on inotify errors, fall back to polling
run_fallback() {
        echo "[metadata-change-detector] Falling back to polling watcher (inotify unavailable or out of watches)" >&2
        "$CWA_PYTHON" "$CWA_SCRIPTS/watch_fallback.py" \
                --path "$WATCH_FOLDER" \
                --interval 5 \
                --exts "json,log" |
        while read -r events filepath; do
                basename -- "$filepath"
        done |
        "$CWA_PYTHON" "$DISPATCHER" --watch-folder "$WATCH_FOLDER"
}

# Detect if running under Docker Desktop (Windows/macOS) and prefer polling
is_docker_desktop() {
        local osr mounts
        osr=$(cat /proc/sys/kernel/osrelease 2>/dev/null || true)
        if echo "$osr" | grep -qi 'microsoft'; then
                return 0
        fi
        if echo "$osr" | grep -qi 'linuxkit'; then
                return 0
        fi
        mounts=$(cat /proc/self/mountinfo 2>/dev/null || true)
        if echo "$mounts" | grep -Eqi '/host_mnt/|/Users/|osxfs|virtiofs.*docker'; then
                return 0
        fi
        return 1
}

# Prefer polling when running over network shares to better handle NFS/SMB semantics
if [ "${NETWORK_SHARE_MODE,,}" = "true" ] || [ "${NETWORK_SHARE_MODE}" = "1" ] || [ "${NETWORK_SHARE_MODE,,}" = "yes" ] || [ "${NETWORK_SHARE_MODE,,}" = "on" ]; then
        echo "[metadata-change-detector] NETWORK_SHARE_MODE=true detected; using polling watcher instead of inotify"
        run_fallback
        exit 0
fi

# Allow admins to force polling mode explicitly (defaults to inotify)
if [ "${CWA_WATCH_MODE:-inotify}" = "poll" ]; then
        run_fallback
        exit 0
fi

# Prefer polling on Docker Desktop (Windows/macOS) for reliable detection on host-mounted paths
if is_docker_desktop; then
        echo "[metadata-change-detector] Docker Desktop environment detected; using polling watcher instead of inotify"
        run_fallback
        exit 0
fi

(
        set -o pipefail
        cwa_run_as inotifywait -m -e close_write -e moved_to --format '%f' --exclude '^.*\.(swp)$' "$WATCH_FOLDER" |
        "$CWA_PYTHON" "$DISPATCHER" --watch-folder "$WATCH_FOLDER"
) || run_fallback
