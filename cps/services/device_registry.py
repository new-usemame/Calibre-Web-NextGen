# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Best-effort device observation without retaining raw hardware identifiers."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

log = logging.getLogger(__name__)
SCHEME = "kobo-header-hmac-sha256-v1"
KOREADER_SCHEME = "koreader-client-hmac-sha256-v1"


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
        if observed_is_newer:
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
    """Observe in an isolated transaction; every failure is swallowed."""
    owned = None
    try:
        from flask import current_app
        from cps import ub
        key = secret_key if secret_key is not None else current_app.secret_key
        owned = sessionmaker(bind=ub.session.get_bind())()
        device = upsert_kobo_device(owned, user_id=user_id, headers=headers, secret_key=key)
        owned.commit()
        return (device.id if return_internal else device.public_id) if device else None
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


def ensure_webreader_device_best_effort(*, user_id):
    """Return the logical web-reader device id without risking the save session."""
    owned = None
    try:
        from cps import ub
        owned = sessionmaker(bind=ub.session.get_bind())()
        device = owned.query(ub.Device).filter_by(
            user_id=user_id, kind="webreader", created_by="auto",
        ).order_by(ub.Device.id.asc()).first()
        if device is None:
            now = datetime.now(timezone.utc)
            device = ub.Device(
                user_id=user_id, kind="webreader",
                display_name=_deduplicated_label(owned, ub, user_id=user_id, base="Web reader"),
                model="CWNG web reader", platform="epub.js",
                first_seen_at=now, last_seen_at=now, last_metadata_at=now,
                active=True, created_by="auto",
            )
            owned.add(device)
        owned.commit()
        return device.id
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
