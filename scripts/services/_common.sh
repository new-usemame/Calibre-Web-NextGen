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
#   CALIBRE_DBPATH  the app's config dir, as cps reads it; defaults to /config
#   CWA_PYTHON    Python interpreter; defaults to python3 on PATH
#   CWA_RUN_AS    command prefix that drops to the service user; the image sets
#                 it to cwa-as-abc, and it is empty where the supervisor already
#                 starts the service as that user. Scripts use cwa_run_as, or
#                 "${CWA_RUN_AS_ARGV[@]}" after a command such as timeout.

CWA_APP_ROOT="${CWA_APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
CWA_SCRIPTS="$CWA_APP_ROOT/scripts"
CWA_PYTHON="${CWA_PYTHON:-python3}"
export CWA_APP_ROOT

# The app's config dir from CALIBRE_DBPATH (a .db path means its folder), or the
# image's /config when it is unset.
CWA_CONFIG_DIR="${CALIBRE_DBPATH:-/config}"
case "$CWA_CONFIG_DIR" in
    *.db) CWA_CONFIG_DIR="$(dirname "$CWA_CONFIG_DIR")" ;;
esac

# The drop-to-service-user prefix as words, for commands such as timeout that
# cannot run the shell function below.
CWA_RUN_AS_ARGV=()
read -r -a CWA_RUN_AS_ARGV <<< "${CWA_RUN_AS:-}"

# Runs a command as the service user.
cwa_run_as() {
    "${CWA_RUN_AS_ARGV[@]}" "$@"
}
