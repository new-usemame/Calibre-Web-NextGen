# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Best-effort device observation without retaining raw hardware identifiers."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker

log = logging.getLogger(__name__)
SCHEME = "kobo-header-hmac-sha256-v1"
KOREADER_SCHEME = "koreader-client-hmac-sha256-v1"
WEBREADER_SCHEME = "webreader-cookie-hmac-sha256-v2"
WEBREADER_SCHEME_PREFIX = "webreader-cookie-hmac-sha256-"
WEBREADER_INSTALLATION_ID_HEADER = "X-CWNG-Webreader-Installation-Id"
LAST_SEEN_WRITE_INTERVAL = timedelta(minutes=5)
# A hard cap over identity-backed browser rows (including retired rows) is
# deliberately stronger than an active-only cap: soft-delete/recreate churn
# must not turn a client-controlled header into unbounded persistent rows.
MAX_WEBREADER_DEVICES_PER_USER = 20
_webreader_cap_logged_users = set()
# Kobo hardware identifiers are client-controlled too. Count every retained
# identity, including retired devices, so identity rotation cannot grow the
# device, delivery-ledger, and pending-response tables without bound.
MAX_KOBO_DEVICES_PER_USER = MAX_WEBREADER_DEVICES_PER_USER
KOBO_DEVICE_LIMIT_MESSAGE = (
    "Kobo device limit reached; use an already registered device"
)
_kobo_cap_logged_users = set()


class KoboDeviceLimitReached(RuntimeError):
    """A new hardware identity would exceed the user's durable Kobo bound."""


def _bounded_header(value, limit):
    if value is None:
        return None
    value = str(value).strip()
    if not value or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    return value


def _fingerprint(raw_id, secret_key):
    raw_id = _bounded_header(raw_id, 128)
    if not raw_id or not re.fullmatch(r"[0-9A-Fa-f]{64}", raw_id):
        return None
    key = secret_key.encode() if isinstance(secret_key, str) else secret_key
    if not key:
        return None
    return hmac.new(key, b"cwng-device:kobo:v1\0" + raw_id.lower().encode(), hashlib.sha256).hexdigest()


def _opaque_fingerprint(raw_id, secret_key, *, namespace):
    raw_id = _bounded_header(raw_id, 100)
    key = secret_key.encode() if isinstance(secret_key, str) else secret_key
    if not raw_id or not key:
        return None
    return hmac.new(key, namespace + b"\0" + raw_id.encode(), hashlib.sha256).hexdigest()


def _webreader_fingerprint(user_id, installation_id, secret_key):
    """Key a browser-held installation id without retaining the raw value."""
    installation_id = _bounded_header(installation_id, 100)
    key = secret_key.encode() if isinstance(secret_key, str) else secret_key
    try:
        user_id_bytes = str(int(user_id)).encode("ascii")
    except (TypeError, ValueError):
        return None
    if not installation_id or not key:
        return None
    # The namespace stays v1 because it identifies the message format; the
    # DeviceIdentity scheme is v2 because user-domain separation changed the
    # preimage. v1 rows existed only on the unreleased #1942 feature branch.
    message = (
        b"cwng-device:webreader:v1\0"
        + user_id_bytes
        + b"\0"
        + installation_id.encode()
    )
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _log_webreader_cap_once(user_id):
    """Emit one privacy-safe process-lifetime diagnostic for each capped user."""
    if user_id in _webreader_cap_logged_users:
        return
    _webreader_cap_logged_users.add(user_id)
    log.info("Web-reader device limit reached; using the legacy device bucket")


def _webreader_identity_count(session, ub, *, user_id):
    """Count all browser identities, active or retired, to bound stored rows."""
    return (
        session.query(ub.Device.id)
        .join(ub.DeviceIdentity, ub.DeviceIdentity.device_id == ub.Device.id)
        .filter(
            ub.Device.user_id == user_id,
            ub.Device.kind == "webreader",
            ub.DeviceIdentity.scheme.like(f"{WEBREADER_SCHEME_PREFIX}%"),
        )
        .distinct()
        .count()
    )


def _kobo_identity_count(session, ub, *, user_id):
    """Count all Kobo identities, active or retired, to bound stored rows."""
    return (
        session.query(ub.Device.id)
        .join(ub.DeviceIdentity, ub.DeviceIdentity.device_id == ub.Device.id)
        .filter(
            ub.Device.user_id == user_id,
            ub.Device.kind == "kobo",
            ub.DeviceIdentity.scheme == SCHEME,
        )
        .distinct()
        .count()
    )


def _log_kobo_cap_once(user_id):
    """Emit one privacy-safe process-lifetime diagnostic for each capped user."""
    if user_id in _kobo_cap_logged_users:
        return
    _kobo_cap_logged_users.add(user_id)
    log.warning(KOBO_DEVICE_LIMIT_MESSAGE)


def _ensure_legacy_webreader_device(session, ub, *, user_id, seen_at=None):
    """Return the bounded historical fallback, reactivating it if retired."""
    device = (
        session.query(ub.Device)
        .filter_by(user_id=user_id, kind="webreader", created_by="auto")
        .filter(~ub.Device.identities.any(
            ub.DeviceIdentity.scheme.like(f"{WEBREADER_SCHEME_PREFIX}%"),
        ))
        .order_by(ub.Device.id.asc())
        .first()
    )
    now = seen_at or datetime.now(timezone.utc)
    if device is None:
        device = ub.Device(
            user_id=user_id,
            kind="webreader",
            display_name=_deduplicated_label(
                session, ub, user_id=user_id, base="Web reader",
            ),
            model="CWNG web reader",
            platform="epub.js",
            first_seen_at=now,
            last_seen_at=now,
            last_metadata_at=now,
            active=True,
            created_by="auto",
        )
        session.add(device)
        session.flush()
    elif not device.active:
        # This row is the logical fallback bucket, not a physical browser.
        # Reactivating the same singleton prevents delete/recreate cycles from
        # becoming another persistent-row growth path.
        device.active = True
        device.last_seen_at = now
        session.flush()
    return device


def _deduplicated_label(session, ub, *, user_id, base):
    used = {row[0] for row in session.query(ub.Device.display_name).filter_by(user_id=user_id)}
    label = base
    suffix = 2
    while label in used:
        label = f"{base} {suffix}"
        suffix += 1
    return label


def upsert_kobo_device(session, *, user_id, headers, secret_key, seen_at=None):
    from cps import ub
    fingerprint = _fingerprint(headers.get("x-kobo-deviceid"), secret_key)
    if not fingerprint:
        return None
    now = seen_at or datetime.now(timezone.utc)
    identity = session.query(ub.DeviceIdentity).filter_by(scheme=SCHEME, fingerprint=fingerprint).first()
    if identity and identity.device.user_id != user_id:
        log.warning("Ignoring Kobo device identity already bound to another user")
        return None
    model = _bounded_header(headers.get("x-kobo-devicemodel"), 160)
    firmware = _bounded_header(headers.get("x-kobo-appversion"), 64)
    if identity is None:
        if _kobo_identity_count(session, ub, user_id=user_id) >= MAX_KOBO_DEVICES_PER_USER:
            _log_kobo_cap_once(user_id)
            raise KoboDeviceLimitReached(KOBO_DEVICE_LIMIT_MESSAGE)
        # User-editable labels are capped at 60 by the API. Keep generated
        # labels inside the same contract without silently truncating a
        # suspiciously long client-controlled model header.
        label_base = model if model and len(model) <= 55 else "Kobo"
        label = _deduplicated_label(session, ub, user_id=user_id, base=label_base)
        device = ub.Device(user_id=user_id, kind="kobo", display_name=label, model=model,
                           platform="nickel", firmware_version=firmware,
                           first_seen_at=now, last_seen_at=now, last_metadata_at=now,
                           active=True, created_by="auto")
        identity = ub.DeviceIdentity(device=device, scheme=SCHEME, key_version=1,
                                     fingerprint=fingerprint, first_seen_at=now, last_seen_at=now)
        session.add(device)
    else:
        device = identity.device
        observed_is_newer = device.last_seen_at is None or now >= device.last_seen_at.replace(tzinfo=now.tzinfo)
        metadata_changed = bool(
            (model and model != device.model)
            or (firmware and firmware != device.firmware_version)
        )
        last_seen_due = (
            device.last_seen_at is None
            or now - device.last_seen_at.replace(tzinfo=now.tzinfo) >= LAST_SEEN_WRITE_INTERVAL
        )
        # The registry is on every authenticated Kobo request. A SELECT is
        # cheap and non-blocking; an UPDATE competes for SQLite's writer lock.
        # Persist a coarse heartbeat, unless changed metadata makes this
        # observation materially different and worth writing immediately.
        if observed_is_newer and (last_seen_due or metadata_changed):
            device.last_seen_at = now
            identity.last_seen_at = now
            if model:
                if device.model and device.model != model:
                    log.warning(
                        "Kobo device model changed for known identity: %r -> %r",
                        device.model, model,
                    )
                device.model = model
            if firmware:
                device.firmware_version = firmware
            device.last_metadata_at = now
    session.flush()
    return device


def register_kobo_device_best_effort(*, user_id, headers, secret_key=None, return_internal=False):
    """Observe in an isolated transaction; surface only the intentional cap."""
    owned = None
    try:
        from flask import current_app
        from cps import ub
        key = secret_key if secret_key is not None else current_app.secret_key
        owned = sessionmaker(bind=ub.session.get_bind())()
        device = upsert_kobo_device(owned, user_id=user_id, headers=headers, secret_key=key)
        owned.commit()
        return (device.id if return_internal else device.public_id) if device else None
    except KoboDeviceLimitReached:
        if owned is not None:
            try:
                owned.rollback()
            except Exception:
                pass
        raise
    except Exception:
        if owned is not None:
            try:
                owned.rollback()
            except Exception:
                pass
        log.warning("Best-effort Kobo device registration failed", exc_info=True)
        return None
    finally:
        if owned is not None:
            try:
                owned.close()
            except Exception:
                pass


def upsert_webreader_device(session, *, user_id, installation_id, secret_key, seen_at=None):
    """Resolve one browser installation to one Device without storing its id."""
    from cps import ub

    fingerprint = _webreader_fingerprint(user_id, installation_id, secret_key)
    if not fingerprint:
        return None
    identity = session.query(ub.DeviceIdentity).filter_by(
        scheme=WEBREADER_SCHEME,
        key_version=1,
        fingerprint=fingerprint,
    ).first()
    if identity and identity.device.user_id != user_id:
        # Defensive only: v2 fingerprints are user-domain-separated.
        log.warning("Ignoring web-reader device identity with inconsistent ownership")
        return None

    now = seen_at or datetime.now(timezone.utc)
    if identity is not None and not identity.device.active:
        # Device deletion is explicit in the management UI. Do not silently
        # undo it; the write uses the legacy bucket until the user restores the
        # browser, at which point this identity resolves normally again.
        return None

    if identity is None:
        if _webreader_identity_count(session, ub, user_id=user_id) >= MAX_WEBREADER_DEVICES_PER_USER:
            _log_webreader_cap_once(user_id)
            return None
        device = ub.Device(
            user_id=user_id,
            kind="webreader",
            display_name=_deduplicated_label(
                session, ub, user_id=user_id, base="Web reader",
            ),
            model="CWNG web reader",
            platform="epub.js",
            first_seen_at=now,
            last_seen_at=now,
            last_metadata_at=now,
            active=True,
            created_by="auto",
        )
        identity = ub.DeviceIdentity(
            device=device,
            scheme=WEBREADER_SCHEME,
            key_version=1,
            fingerprint=fingerprint,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(device)
    else:
        device = identity.device
        observed_is_newer = (
            device.last_seen_at is None
            or now >= device.last_seen_at.replace(tzinfo=now.tzinfo)
        )
        last_seen_due = (
            device.last_seen_at is None
            or now - device.last_seen_at.replace(tzinfo=now.tzinfo)
            >= LAST_SEEN_WRITE_INTERVAL
        )
        # Browser position writes can arrive every 800ms. Keep the identity
        # observation read-only until the same coarse heartbeat Kobo uses is
        # due, so those saves do not add another SQLite writer-lock contender.
        if observed_is_newer and last_seen_due:
            device.last_seen_at = now
            identity.last_seen_at = now
    session.flush()
    return device


def ensure_webreader_device_best_effort(*, user_id, installation_id=None,
                                        secret_key=None):
    """Return a per-browser device id, or the historical singleton fallback."""
    owned = None
    try:
        from flask import current_app
        from cps import ub
        key = secret_key if secret_key is not None else current_app.secret_key
        owned = sessionmaker(bind=ub.session.get_bind())()
        if installation_id:
            device = upsert_webreader_device(
                owned,
                user_id=user_id,
                installation_id=installation_id,
                secret_key=key,
            )
            if device is None:
                device = _ensure_legacy_webreader_device(
                    owned, ub, user_id=user_id,
                )
        else:
            device = _ensure_legacy_webreader_device(
                owned, ub, user_id=user_id,
            )
        if device is None:
            return None
        device_id = device.id
        owned.commit()
        return device_id
    except Exception:
        if owned is not None:
            try:
                owned.rollback()
            except Exception:
                pass
        log.warning("Best-effort web-reader device registration failed", exc_info=True)
        return None
    finally:
        if owned is not None:
            try:
                owned.close()
            except Exception:
                pass


def resolve_owned_device_best_effort(*, user_id, public_id):
    """Resolve a user-visible device id in an isolated, fail-open session."""
    if not _bounded_header(public_id, 36):
        return None
    owned = None
    try:
        from cps import ub
        owned = sessionmaker(bind=ub.session.get_bind())()
        row = owned.query(ub.Device.id).filter_by(user_id=user_id, public_id=public_id).first()
        return row[0] if row else None
    except Exception:
        log.warning("Best-effort annotation device resolution failed", exc_info=True)
        return None
    finally:
        if owned is not None:
            try:
                owned.close()
            except Exception:
                pass


def register_koreader_device_best_effort(*, user_id, device_id, device_name=None,
                                          secret_key=None):
    """Observe an optional kosync device id without retaining its raw value."""
    owned = None
    try:
        from flask import current_app
        from cps import ub
        key = secret_key if secret_key is not None else current_app.secret_key
        fingerprint = _opaque_fingerprint(
            device_id, key, namespace=b"cwng-device:koreader:v1",
        )
        if not fingerprint:
            return None
        owned = sessionmaker(bind=ub.session.get_bind())()
        identity = owned.query(ub.DeviceIdentity).filter_by(
            scheme=KOREADER_SCHEME, key_version=1, fingerprint=fingerprint,
        ).first()
        if identity and identity.device.user_id != user_id:
            log.warning("Ignoring KOReader device identity already bound to another user")
            return None
        now = datetime.now(timezone.utc)
        label_base = _bounded_header(device_name, 55) or "KOReader"
        if identity is None:
            device = ub.Device(
                user_id=user_id, kind="koreader",
                display_name=_deduplicated_label(owned, ub, user_id=user_id, base=label_base),
                model=_bounded_header(device_name, 160), platform="koreader",
                first_seen_at=now, last_seen_at=now, last_metadata_at=now,
                active=True, created_by="auto",
            )
            identity = ub.DeviceIdentity(
                device=device, scheme=KOREADER_SCHEME, key_version=1,
                fingerprint=fingerprint, first_seen_at=now, last_seen_at=now,
            )
            owned.add(device)
        else:
            device = identity.device
            device.last_seen_at = now
            identity.last_seen_at = now
        owned.commit()
        return device.id
    except Exception:
        if owned is not None:
            try:
                owned.rollback()
            except Exception:
                pass
        log.warning("Best-effort KOReader device registration failed", exc_info=True)
        return None
    finally:
        if owned is not None:
            try:
                owned.close()
            except Exception:
                pass
