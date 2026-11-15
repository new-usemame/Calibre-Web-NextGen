#!/usr/bin/env bash
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
#
# Seeds app.db and the Calibre library on first start and records where the library is.

# shellcheck source=_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

# auto_library conditionally invokes calibre-customize. Its root-run default
# must not share config files with abc, while the plugin helper remains free to
# override this path when the operator explicitly enables user plugins.
if [ -z "${CALIBRE_CONFIG_DIRECTORY:-}" ]; then
    export CALIBRE_CONFIG_DIRECTORY="/tmp/cwa-calibre-config-$(id -u)"
    install -d -m 0700 "$CALIBRE_CONFIG_DIRECTORY"
fi

# Optionally skip auto-library based on DISABLE_LIBRARY_AUTOMOUNT env var
if [[ "${DISABLE_LIBRARY_AUTOMOUNT,,}" == "true" || "${DISABLE_LIBRARY_AUTOMOUNT,,}" == "yes" || "${DISABLE_LIBRARY_AUTOMOUNT}" == "1" ]]; then
    echo "[cwa-auto-library] DISABLE_LIBRARY_AUTOMOUNT=${DISABLE_LIBRARY_AUTOMOUNT:-unset}. Skipping auto-library service as requested."
    exit 0
fi

"$CWA_PYTHON" "$CWA_SCRIPTS/auto_library.py"
exit_code=$?

if [[ ${exit_code} -eq 0 ]]; then
    echo "[cwa-auto-library] Service completed successfully! Ending service..."
else
    echo "[cwa-auto-library] Service did not complete successfully (exit code: ${exit_code}). Ending service..."
fi

exit "${exit_code}"
