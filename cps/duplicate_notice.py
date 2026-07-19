# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Path helper for the per-user duplicate-scan setup-notice dismissal marker.

Kept in its own dependency-free module so both the write side
(:mod:`cps.duplicates`) and the read side (:mod:`cps.render_template`) share a
single source of truth for the path — and so it can be unit-tested without
Flask/DB bootstrap. See issue #992.
"""

import os

# Per-user dismissal state belongs on the writable, persistent /config volume.
# The previous location, /app, ships root-owned, so the runtime user could not
# write the marker (EACCES surfaced as a 500 on the dismiss endpoint) and the
# notice could never be dismissed on a stock container; /app is also wiped on
# image upgrade. This is the sibling of the /config marker files in
# cps.duplicate_index (INGEST_BATCH_DIRTY_FILE, INGEST_BATCH_ACTIVE_FILE).
DUPLICATE_SETUP_NOTICE_DIR = "/config"


def duplicate_setup_notice_file(user_id):
    """Absolute path to the marker recording that ``user_id`` dismissed the
    duplicate-index setup notice.

    ``user_id`` is the authenticated user's id (or the ``"unknown"`` sentinel
    the callers pass for the anonymous/no-id case).
    """
    return os.path.join(
        DUPLICATE_SETUP_NOTICE_DIR,
        "cwa_duplicate_index_setup_notice_{}".format(user_id),
    )
