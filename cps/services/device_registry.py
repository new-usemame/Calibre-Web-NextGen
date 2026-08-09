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
        device = ub.Device(user_id=user_id, kind="kobo", display_name=model or "Kobo", model=model,
                           firmware_version=firmware, first_seen_at=now, last_seen_at=now)
        identity = ub.DeviceIdentity(device=device, scheme=SCHEME, key_version=1,
                                     fingerprint=fingerprint, first_seen_at=now, last_seen_at=now)
        session.add(device)
    else:
        device = identity.device
        device.last_seen_at = now
        identity.last_seen_at = now
        if model:
            device.model = model
        if firmware:
            device.firmware_version = firmware
    session.flush()
    return device


def register_kobo_device_best_effort(*, user_id, headers, secret_key=None):
    """Observe in an isolated transaction; every failure is swallowed."""
    owned = None
    try:
        from flask import current_app
        from cps import ub
        key = secret_key if secret_key is not None else current_app.secret_key
        owned = sessionmaker(bind=ub.session.get_bind())()
        device = upsert_kobo_device(owned, user_id=user_id, headers=headers, secret_key=key)
        owned.commit()
        return device.public_id if device else None
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
