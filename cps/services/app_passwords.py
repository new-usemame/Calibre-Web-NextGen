# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""App passwords: the one place a new one is made, and how sign-in finds one.

An app password is a long random secret that a device or client uses for
HTTP Basic auth (OPDS, KOReader sync) instead of the account password. Its
cleartext exists only in the response that creates it. app.db
(``ub.UserAppPassword``) keeps two things derived from it:

``token_digest``
    Its SHA-256. Sign-in finds the row with one indexed lookup of this, and
    no slow hash. A fast digest is safe here, where it would not be for a
    password somebody chose: every app password ever made is 256 random bits
    from :func:`secrets.token_urlsafe`, so there is nothing to guess, and a
    slow hash only protects secrets that can be guessed.

``password_hash``
    A werkzeug hash, as releases before the digest kept. Rows from those
    releases have only this: :func:`find_older` checks them the slow way and
    fills in the digest of the one that matches, so each is slow once. New
    rows keep it too, so that going back to an older release keeps every app
    password working.

A device sends its credentials with every request, so a library sync is
dozens of sign-ins: :func:`note_use` keeps ``last_used_at`` to the minute
instead of writing app.db each time.

Made from the account page (new and classic UI), by KOReader pairing, and for
the ready-made plugin download; checked by ``usermanagement`` for OPDS and by
the KOReader sync sign-in.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from .. import logger, ub

log = logger.create()

LABEL_MAX = 64
# Only secrets at least this long get a fast digest from an older row: every
# app password is 43 characters (``token_urlsafe(32)``), so this never turns
# away a real one, and it keeps a short secret behind its slow hash.
DIGEST_MIN_LENGTH = 32
LAST_USED_RESOLUTION = timedelta(minutes=1)


def utcnow():
    return datetime.now(timezone.utc)


def clip_label(text):
    """``text`` with whitespace collapsed, shortened to fit a label."""
    label = " ".join(str(text or "").split())
    if len(label) > LABEL_MAX:
        label = label[:LABEL_MAX - 1].rstrip() + "…"
    return label


def token_digest(cleartext):
    """The SHA-256 (hex) of an app password, as stored in ``token_digest``."""
    return hashlib.sha256(cleartext.encode("utf-8")).hexdigest()


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
                             password_hash=generate_password_hash(cleartext),
                             token_digest=token_digest(cleartext))
    (session or ub.session).add(row)
    return row, cleartext


def _live(session, user):
    return session.query(ub.UserAppPassword).filter(
        ub.UserAppPassword.user_id == user.id,
        ub.UserAppPassword.revoked == False,  # noqa: E712 - SQLAlchemy idiom
    )


def find(user, password, *, session=None):
    """``user``'s live app password whose digest matches ``password``, or None.

    One indexed lookup and no slow hash, so it can be tried first on every
    request.
    """
    if not user or not password:
        return None
    return _live(session or ub.session, user).filter(
        ub.UserAppPassword.token_digest == token_digest(password)).first()


def find_older(user, password, *, session=None):
    """``user``'s live app password from before digests that matches, or None.

    Checks the werkzeug hash of each such row, which is slow by design, so
    callers try this after everything cheaper. The match gets its digest (the
    caller's :func:`note_use` commits it), and :func:`find` finds it from then
    on.
    """
    if not user or not password:
        return None
    rows = _live(session or ub.session, user).filter(
        ub.UserAppPassword.token_digest.is_(None)).all()
    for row in rows:
        if check_password_hash(row.password_hash, password):
            if len(password) >= DIGEST_MIN_LENGTH:
                row.token_digest = token_digest(password)
            return row
    return None


def note_use(row, *, session=None):
    """Record that ``row`` was just used to sign in, and commit what changed.

    ``last_used_at`` moves only when it is a minute or more behind (or ahead
    of) the clock; nothing is written when nothing changed.
    """
    session = session or ub.session
    now = utcnow()
    last = row.last_used_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last is None or abs(now - last) >= LAST_USED_RESOLUTION:
        row.last_used_at = now
    if not session.is_modified(row):
        return
    try:
        session.commit()
    except Exception as ex:  # a missed stamp must never fail a sign-in
        log.debug("Could not record app password use: %s", ex)
        session.rollback()
