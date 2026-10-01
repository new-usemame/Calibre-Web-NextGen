#!/usr/bin/env bash
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
#
# Zips the day's retained originals just before midnight, every night.

# shellcheck source=_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

WAKEUP="23:59"

while :
do
    SECS=$(( $(date -d "$WAKEUP" +%s) - $(date -d "now" +%s) ))
    if [[ $SECS -lt 0 ]]
    then
        SECS=$(( $(date -d "tomorrow $WAKEUP" +%s) - $(date -d "now" +%s) ))
    fi
    echo "[cwa-auto-zipper] Next run in $SECS seconds."
    sleep $SECS &  # In the background so SIGTERM interrupts the wait
    wait $!
    # As the service user, so the archives get the same owner as the books
    # beside them (#162).
    cwa_run_as "$CWA_PYTHON" "$CWA_SCRIPTS/auto_zip.py"
    rc=$?
    if [[ $rc == 1 ]]
    then
        echo "[cwa-auto-zipper] Error occurred during script initialisation (see errors above)."
    elif [[ $rc == 2 ]]
    then
        echo "[cwa-auto-zipper] Error occurred while zipping today's files (see errors above)."
    elif [[ $rc == 3 ]]
    then
        echo "[cwa-auto-zipper] Error occurred while trying to removed the files that have been zipped (see errors above)."
    fi
    sleep 60
done
