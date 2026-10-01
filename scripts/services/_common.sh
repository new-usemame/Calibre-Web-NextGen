# shellcheck shell=bash
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
#
# Shared setup for the service scripts in this directory (#2094).
#
# Each script holds one service body and runs under any supervisor: the s6
# run files in the image exec them, and systemd or a plain shell can too.
# Only these variables connect a script to its environment:
#
#   CWA_APP_ROOT  app install dir; defaults to two levels above this file
#   CWA_PYTHON    Python interpreter; defaults to python3 on PATH
#   CWA_RUN_AS    command prefix that drops to the service user; the image sets
#                 it to cwa-as-abc, and it is empty where the supervisor already
#                 starts the service as that user

CWA_APP_ROOT="${CWA_APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
CWA_SCRIPTS="$CWA_APP_ROOT/scripts"
CWA_PYTHON="${CWA_PYTHON:-python3}"
export CWA_APP_ROOT

# Runs a command as the service user.
cwa_run_as() {
    if [ -n "${CWA_RUN_AS:-}" ]; then
        # shellcheck disable=SC2086 # CWA_RUN_AS is a command prefix and may carry arguments.
        $CWA_RUN_AS "$@"
    else
        "$@"
    fi
}
