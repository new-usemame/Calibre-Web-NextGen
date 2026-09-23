# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Connect an e-reader with a code (a device authorization grant).

The device asks for a code (:func:`start`), shows it and polls (:func:`poll`).
A signed-in reader types the code on the website, sees which device is asking
and from where, and approves or declines it (:func:`decide`). The first poll
after approval hands the device a newly made app password, exactly once.

Secrets. The ``device_code`` is 256 random bits, given to the device once and
stored only as its SHA-256 digest: a secret that long needs no slow hash, and
a copy of the table lets nobody poll. The app password is made inside the
claim and exists in cleartext only in that one response; app.db keeps its
werkzeug hash like every other app password. The typed ``user_code`` is short
on purpose. It is safe because it lives ten minutes, grants nothing until a
signed-in person approves it, and the number of live codes is capped.

Every state change is a conditional UPDATE, so two approvals, or two polls
racing for one approval, cannot both win.

States: pending -> approved -> claimed, or pending -> denied. A row past
``expires_at`` is dead whatever its state, and is deleted by the next
:func:`start` or by ``ub.clean_database`` at startup.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from .. import logger, ub
from . import app_passwords

log = logger.create()

# Letters and digits that survive being read off an e-ink screen and typed on
# a phone: no vowels (no accidental words), none of 0/O, 1/I/L.
USER_CODE_ALPHABET = "BCDFGHJKMNPQRSTVWXZ23456789"
USER_CODE_LENGTH = 8
LIFETIME = timedelta(minutes=10)
POLL_INTERVAL = 5                         # seconds the device is asked to wait
MIN_POLL_SPACING = timedelta(seconds=2)   # polls closer than this are refused
MAX_PENDING_PER_ADDRESS = 5
MAX_PENDING = 100
DEVICE_NAME_MAX = 100

PENDING = "pending"
APPROVED = "approved"
DENIED = "denied"
CLAIMED = "claimed"


class PairingError(Exception):
    """A request the service refuses: ``code`` for machines, ``status`` for HTTP."""

    def __init__(self, code, status, message):
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Started:
    user_code: str      # as stored: 8 characters, no separator
    device_code: str    # cleartext; given to the device once, never stored
    expires_in: int
    interval: int


@dataclass(frozen=True)
class PollResult:
    status: str         # pending | denied | expired | slow_down | approved
    http_status: int
    username: str = None
    password: str = None


def utcnow():
    """Naive UTC, the form app.db stores."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _digest(device_code):
    return hashlib.sha256(device_code.encode("utf-8")).hexdigest()


def normalize_user_code(text):
    """The stored form of a typed code, or ``None`` when it cannot be one.

    Case, spaces and the dash are forgiven; anything outside the alphabet is
    not, so a malformed code never reaches the database.
    """
    if not isinstance(text, str):
        return None
    code = "".join(ch for ch in text.upper() if not ch.isspace() and ch != "-")
    if len(code) != USER_CODE_LENGTH or any(ch not in USER_CODE_ALPHABET for ch in code):
        return None
    return code


def display_user_code(code):
    """``K7M4QX2P`` -> ``K7M4-QX2P``, the form a person reads and types."""
    half = USER_CODE_LENGTH // 2
    return "%s-%s" % (code[:half], code[half:])


def clean_device_name(text):
    """A device name fit to show a person: printable, one line, bounded."""
    if not isinstance(text, str):
        return ""
    printable = "".join(ch if ch.isprintable() else " " for ch in text)
    return " ".join(printable.split())[:DEVICE_NAME_MAX]


def app_password_label(device_name):
    """How the device's app password is listed on the account page."""
    return app_passwords.clip_label("KOReader: %s" % device_name)


def _sweep(session, now):
    session.query(ub.KOReaderPairing).filter(
        ub.KOReaderPairing.expires_at <= now).delete(synchronize_session=False)


def start(device_name, *, address=None, now=None, session=None):
    """Open a pairing request for a device; return :class:`Started`.

    Raises :class:`PairingError` for a missing device name (400) or when too
    many codes are already waiting, from this address or at all (429).
    """
    session = session or ub.session
    now = now or utcnow()
    name = clean_device_name(device_name)
    if not name:
        raise PairingError("invalid_request", 400, "The device name is missing.")
    address = (address or "")[:64] or None
    try:
        _sweep(session, now)
        waiting = session.query(func.count(ub.KOReaderPairing.id)).filter(
            ub.KOReaderPairing.status == PENDING)
        if address is not None and waiting.filter(
                ub.KOReaderPairing.requester_ip == address).scalar() >= MAX_PENDING_PER_ADDRESS:
            session.commit()
            raise PairingError("too_many_requests", 429,
                               "Too many codes are waiting from this network. "
                               "Use one of them or wait ten minutes.")
        if waiting.scalar() >= MAX_PENDING:
            session.commit()
            raise PairingError("too_many_requests", 429,
                               "Too many codes are waiting. Try again in a few minutes.")
        user_code = None
        for _attempt in range(8):
            candidate = "".join(secrets.choice(USER_CODE_ALPHABET)
                                for _ in range(USER_CODE_LENGTH))
            taken = session.query(ub.KOReaderPairing.id).filter(
                ub.KOReaderPairing.user_code == candidate).first()
            if taken is None:
                user_code = candidate
                break
        if user_code is None:
            session.commit()
            raise PairingError("unavailable", 503, "Could not allocate a code. Try again.")
        device_code = secrets.token_urlsafe(32)
        session.add(ub.KOReaderPairing(
            user_code=user_code,
            device_code_hash=_digest(device_code),
            device_name=name,
            requester_ip=address,
            status=PENDING,
            created_at=now,
            expires_at=now + LIFETIME,
        ))
        session.commit()
    except PairingError:
        raise
    except Exception:
        session.rollback()
        log.exception("KOReader pairing: could not open a request")
        raise PairingError("unavailable", 503, "Could not start pairing. Try again.")
    return Started(user_code=user_code, device_code=device_code,
                   expires_in=int(LIFETIME.total_seconds()), interval=POLL_INTERVAL)


def poll(device_code, *, now=None, session=None):
    """Answer one poll from the device; return :class:`PollResult`.

    The poll that finds the request approved claims it and receives the new
    app password; every later poll finds it claimed and is told it expired.
    """
    if (not isinstance(device_code, str) or not 20 <= len(device_code) <= 128
            or not device_code.isascii()):
        raise PairingError("invalid_request", 400, "The device code is missing or malformed.")
    session = session or ub.session
    now = now or utcnow()
    row = session.query(ub.KOReaderPairing).filter(
        ub.KOReaderPairing.device_code_hash == _digest(device_code)).first()
    if row is None:
        return PollResult("expired", 404)
    if row.expires_at <= now or row.status == CLAIMED:
        return PollResult("expired", 410)
    too_soon = row.last_poll_at is not None and now - row.last_poll_at < MIN_POLL_SPACING
    row.last_poll_at = now
    if too_soon:
        session.commit()
        return PollResult("slow_down", 429)
    if row.status != APPROVED:
        session.commit()
        return PollResult(DENIED if row.status == DENIED else PENDING, 200)
    return _claim(session, row, now)


def _claim(session, row, now):
    try:
        won = session.query(ub.KOReaderPairing).filter(
            ub.KOReaderPairing.id == row.id,
            ub.KOReaderPairing.status == APPROVED,
        ).update({"status": CLAIMED, "claimed_at": now, "last_poll_at": now},
                 synchronize_session=False)
        if won != 1:
            session.rollback()
            return PollResult("expired", 410)
        user = session.query(ub.User).filter(ub.User.id == row.user_id).first()
        if user is None or user.role_anonymous():
            session.commit()
            return PollResult("expired", 410)
        password_row, cleartext = app_passwords.mint(
            user.id, app_password_label(row.device_name), session=session)
        session.flush()
        session.query(ub.KOReaderPairing).filter(ub.KOReaderPairing.id == row.id).update(
            {"app_password_id": password_row.id}, synchronize_session=False)
        session.commit()
    except Exception:
        session.rollback()
        log.exception("KOReader pairing: could not complete an approved request")
        raise PairingError("unavailable", 503, "Could not finish pairing. Try again.")
    log.info("KOReader pairing: %s connected to %s", row.device_name, user.name)
    return PollResult(APPROVED, 200, username=user.name, password=cleartext)


def find_waiting(user_code, *, now=None, session=None):
    """The live, undecided request for a typed code.

    Raises :class:`PairingError`: 404 when no live request has this code, 409
    when it was already approved or declined.
    """
    session = session or ub.session
    now = now or utcnow()
    code = normalize_user_code(user_code)
    row = None
    if code is not None:
        row = session.query(ub.KOReaderPairing).filter(
            ub.KOReaderPairing.user_code == code).first()
    if row is None or row.expires_at <= now:
        raise PairingError("not_found", 404,
                           "No e-reader is waiting with this code. Check it, or "
                           "get a new code on the e-reader.")
    if row.status != PENDING:
        raise PairingError("already_decided", 409,
                           "This code has already been approved or declined.")
    return row


def decide(user_code, user, *, approve, now=None, session=None):
    """Approve or decline a waiting request on behalf of ``user``."""
    session = session or ub.session
    now = now or utcnow()
    row = find_waiting(user_code, now=now, session=session)
    try:
        won = session.query(ub.KOReaderPairing).filter(
            ub.KOReaderPairing.id == row.id,
            ub.KOReaderPairing.status == PENDING,
            ub.KOReaderPairing.expires_at > now,
        ).update({"status": APPROVED if approve else DENIED,
                  "user_id": user.id, "decided_at": now},
                 synchronize_session=False)
        session.commit()
    except Exception:
        session.rollback()
        log.exception("KOReader pairing: could not record a decision")
        raise PairingError("unavailable", 503, "Could not save your answer. Try again.")
    if won != 1:
        # Someone else answered between the lookup and this update.
        raise PairingError("already_decided", 409,
                           "This code has already been approved or declined.")
    session.refresh(row)
    return row


def forget_user(user_id, *, session=None):
    """Delete every request a user answered (account deletion). No commit."""
    (session or ub.session).query(ub.KOReaderPairing).filter(
        ub.KOReaderPairing.user_id == user_id).delete(synchronize_session=False)
