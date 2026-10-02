#!/usr/bin/env bash
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
#
# Hourly LRU sweep of the cover-preview disk cache. The sweeper evicts the
# oldest tiles until the cache is under CWA_PREVIEW_CACHE_MAX_MB (default 1024).

# shellcheck source=_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

# One hour between sweeps; the cache grows slowly under normal use.
SWEEP_INTERVAL_SECONDS=${CWA_PREVIEW_CACHE_SWEEP_INTERVAL:-3600}

# Leave first-boot setup and migrations alone; an empty cache has nothing to evict.
INITIAL_DELAY_SECONDS=${CWA_PREVIEW_CACHE_INITIAL_DELAY:-120}

echo "[cwa-preview-cache-cleanup] Initial delay: ${INITIAL_DELAY_SECONDS}s; sweep interval: ${SWEEP_INTERVAL_SECONDS}s; cap (MB): ${CWA_PREVIEW_CACHE_MAX_MB:-1024}"
sleep "$INITIAL_DELAY_SECONDS"

# Python's `-m` only adds CWD to sys.path; cd so `cps` is importable.
cd "$CWA_APP_ROOT" || { echo "[cwa-preview-cache-cleanup] FATAL: $CWA_APP_ROOT missing"; exit 1; }

while true; do
    # As the service user, so it can unlink the tiles the web app wrote.
    cwa_run_as "$CWA_PYTHON" -m cps.services.cover_preview_cache_sweeper \
        || echo "[cwa-preview-cache-cleanup] WARNING: sweeper exited non-zero; continuing schedule"

    sleep "$SWEEP_INTERVAL_SECONDS"
done
