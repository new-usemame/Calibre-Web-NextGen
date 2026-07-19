# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Per-user duplicate-scan setup-notice dismissal marker.

Both the write side (:mod:`cps.duplicates`) and the read sides
(:mod:`cps.render_template`) resolve the marker here, so the path cannot drift.
See issue #992.

The marker used to live in ``/app``, which ships root-owned and is replaced on
image upgrade: writing it raised EACCES (a 500 on the dismiss endpoint) so the
notice could never be dismissed on a stock container. It now lives in the
application's configured state directory — the same place ``app.db`` lives —
which is ``/config`` in the container image and ``CALIBRE_DBPATH`` elsewhere.
"""

import os

# Pre-#992 location. Read-only fallback so a user who did manage to dismiss the
# notice under the old path (root-run container, custom image, bare metal)
# doesn't get it back after upgrading. Nothing writes here any more, and the
# fallback expires by itself when the app tree is replaced.
LEGACY_NOTICE_DIR = "/app"


def _config_dir():
    """The configured, writable state directory (where ``app.db`` lives).

    Imported lazily from :mod:`cps.constants` so this module stays cheap to
    import and easy to unit-test, while still honouring ``CALIBRE_DBPATH``
    instead of hard-coding the container's ``/config``.
    """
    from .constants import CONFIG_DIR

    return CONFIG_DIR


def _notice_basename(user_id):
    # user_id is the authenticated user's database id (an int), or the
    # "unknown" sentinel the callers pass for the anonymous/no-id case.
    # basename() keeps an unexpected value from escaping the state directory.
    safe_id = os.path.basename(str(user_id)) or "unknown"
    return "cwa_duplicate_index_setup_notice_{}".format(safe_id)


def duplicate_setup_notice_file(user_id):
    """Absolute path of the marker recording that ``user_id`` dismissed the
    duplicate-index setup notice. This is the only path ever written to."""
    return os.path.join(_config_dir(), _notice_basename(user_id))


def legacy_duplicate_setup_notice_file(user_id):
    """Absolute path of the pre-#992 marker. Read-only compatibility."""
    return os.path.join(LEGACY_NOTICE_DIR, _notice_basename(user_id))


def duplicate_setup_notice_dismissed(user_id):
    """True when this user has dismissed the notice, old location or new."""
    if os.path.isfile(duplicate_setup_notice_file(user_id)):
        return True
    return os.path.isfile(legacy_duplicate_setup_notice_file(user_id))
