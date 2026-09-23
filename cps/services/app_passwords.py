# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""App passwords: the one place a new one is made.

An app password is a long random secret that a device or client uses for
HTTP Basic auth (OPDS, KOReader sync) instead of the account password. Its
cleartext exists only in the response that creates it; app.db keeps a
werkzeug hash (``ub.UserAppPassword``), checked by
``usermanagement._verify_app_password``. Made from the account page (new and
classic UI), by KOReader pairing, and for the ready-made plugin download.
"""

import secrets

from werkzeug.security import generate_password_hash

from .. import ub

LABEL_MAX = 64


def clip_label(text):
    """``text`` with whitespace collapsed, shortened to fit a label."""
    label = " ".join(str(text or "").split())
    if len(label) > LABEL_MAX:
        label = label[:LABEL_MAX - 1].rstrip() + "…"
    return label


def mint(user_id, label, *, session=None):
    """Add a new app password for ``user_id``; return ``(row, cleartext)``.

    Does not commit: the caller owns the transaction, so minting can be atomic
    with whatever it belongs to (a pairing claim). Raises ``ValueError`` for an
    empty or over-long label.
    """
    label = (label or "").strip()
    if not label or len(label) > LABEL_MAX:
        raise ValueError("App password label must be 1-%d characters" % LABEL_MAX)
    cleartext = secrets.token_urlsafe(32)
    row = ub.UserAppPassword(user_id=user_id, label=label,
                             password_hash=generate_password_hash(cleartext))
    (session or ub.session).add(row)
    return row, cleartext
