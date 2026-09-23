# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""KOReader on the website: approve a device's code, download a ready-made plugin.

Session + CSRF like every /api/v1 write, for a signed-in account (never the
anonymous-browse guest), and only while KOReader sync is switched on.

    GET  /api/v1/devices/koreader/pair/<user_code>          who is asking
    POST /api/v1/devices/koreader/pair/<user_code>/approve   connect it to me
    POST /api/v1/devices/koreader/pair/<user_code>/deny      turn it away
    POST /api/v1/devices/koreader/setup-bundle {server?}     the plugin zip

The device's half of pairing is ``cps/progress_syncing/protocols/kosync_pairing.py``.
"""

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import jsonify, make_response, request
from werkzeug.exceptions import HTTPException

from . import api_v1
from .. import limiter, logger, ub
from ..cw_login import current_user
from ..progress_syncing.settings import is_koreader_sync_enabled
from ..services import app_passwords, koreader_bundle, koreader_pairing
from ..services.koreader_library import iso_z

try:
    from flask_limiter import RateLimitExceeded
except ImportError:  # flask_limiter is optional outside the container
    class RateLimitExceeded(Exception):
        pass

log = logger.create()

BUNDLE_FILENAME = "cwngsync-ready-made.zip"
_SERVER_MAX = 255
_HOST_RE = re.compile(r"^[A-Za-z0-9._~%-]+$|^\[[0-9A-Fa-f:.]+\]$")


def _err(code, message, status):
    response = jsonify({"error": {"code": code, "message": message}})
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _user_key():
    return "koreader-devices:%s" % getattr(current_user, "id", "")


def _guard():
    """The error response for this request, or ``None`` when it may proceed."""
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not is_koreader_sync_enabled():
        return _err("koreader_sync_disabled",
                    "KOReader sync is not enabled on this server", 409)
    if limiter is not None:
        try:
            limiter.check()
        except RateLimitExceeded:
            return _err("rate_limit_exceeded",
                        "Too many attempts. Wait a minute and try again.", 429)
        except HTTPException:
            raise
        except Exception as ex:  # a broken limiter store fails open, logged
            log.error("Rate limiter backend error: %s", ex)
    return None


def _request_payload(row):
    return {
        "user_code": koreader_pairing.display_user_code(row.user_code),
        "device_name": row.device_name,
        "requested_at": iso_z(row.created_at),
        "expires_at": iso_z(row.expires_at),
        "ip": row.requester_ip,
        "status": row.status,
    }


def _pairing_error(ex):
    return _err(ex.code, ex.message, ex.status)


@api_v1.route("/devices/koreader/pair/<user_code>", methods=["GET"])
@limiter.limit("60/hour", key_func=_user_key)
@limiter.limit("10/minute", key_func=_user_key)
def koreader_pair_lookup(user_code):
    """The waiting device behind a typed code: its name, when and from where."""
    blocked = _guard()
    if blocked:
        return blocked
    try:
        row = koreader_pairing.find_waiting(user_code)
    except koreader_pairing.PairingError as ex:
        return _pairing_error(ex)
    return jsonify(_request_payload(row))


def _decide(user_code, approve):
    blocked = _guard()
    if blocked:
        return blocked
    try:
        row = koreader_pairing.decide(user_code, current_user, approve=approve)
    except koreader_pairing.PairingError as ex:
        return _pairing_error(ex)
    log.info("KOReader pairing: %s %s %s", current_user.name,
             "approved" if approve else "declined", row.device_name)
    return jsonify(_request_payload(row))


@api_v1.route("/devices/koreader/pair/<user_code>/approve", methods=["POST"])
@limiter.limit("60/hour", key_func=_user_key)
@limiter.limit("10/minute", key_func=_user_key)
def koreader_pair_approve(user_code):
    """Connect the waiting device to this account."""
    return _decide(user_code, True)


@api_v1.route("/devices/koreader/pair/<user_code>/deny", methods=["POST"])
@limiter.limit("60/hour", key_func=_user_key)
@limiter.limit("10/minute", key_func=_user_key)
def koreader_pair_deny(user_code):
    """Turn the waiting device away."""
    return _decide(user_code, False)


def normalize_server(text):
    """The address a device should use, as a person might type it, or ``None``.

    Mirrors the plugin's own reading: a bare host gets ``http://``, a trailing
    slash or ``/kosync`` is dropped. Anything that is not a plain http(s)
    address (credentials, a query, another scheme) is refused.
    """
    if not isinstance(text, str):
        return None
    value = text.strip()
    if not value or len(value) > _SERVER_MAX or any(ch.isspace() for ch in value):
        return None
    if not re.match(r"^https?://", value, re.IGNORECASE):
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):
            return None
        value = "http://" + value
    value = re.sub(r"/+$", "", value)
    value = re.sub(r"/kosync$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"/+$", "", value)
    try:
        parts = urlsplit(value)
        parts.port  # raises on a malformed port
    except ValueError:
        return None
    host = parts.netloc.rsplit(":", 1)[0] if not parts.netloc.endswith("]") else parts.netloc
    if (not parts.hostname or "@" in parts.netloc or parts.query or parts.fragment
            or not _HOST_RE.match(host)):
        return None
    return value


@api_v1.route("/devices/koreader/setup-bundle", methods=["POST"])
@limiter.limit("30/day", key_func=_user_key)
@limiter.limit("10/hour", key_func=_user_key)
def koreader_setup_bundle():
    """The plugin with this account's sign-in inside, ready to copy to a device.

    Makes a new app password, listed on the account page as "KOReader
    (ready-made download <date>)", so it can be revoked on its own.
    """
    blocked = _guard()
    if blocked:
        return blocked
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    server = request.url_root.rstrip("/")
    if data.get("server") not in (None, ""):
        server = normalize_server(data.get("server"))
        if server is None:
            return _err("invalid_server",
                        "Enter the address as http://host:port or https://host", 400)
    now = datetime.now(timezone.utc)
    label = "KOReader (ready-made download %s)" % now.strftime("%Y-%m-%d")
    try:
        _row, password = app_passwords.mint(current_user.id, label)
        archive = koreader_bundle.build({
            "server": server,
            "username": current_user.name,
            "password": password,
            "created_at": iso_z(now),
        })
        ub.session.commit()
    except koreader_bundle.PluginUnavailable as ex:
        ub.session.rollback()
        return _err("plugin_unavailable", str(ex), 503)
    except Exception:
        ub.session.rollback()
        log.exception("KOReader ready-made plugin: could not build the download")
        return _err("db_error", "Could not prepare the download", 500)
    response = make_response(archive)
    response.headers["Content-Type"] = "application/zip"
    response.headers["Content-Disposition"] = 'attachment; filename="%s"' % BUNDLE_FILENAME
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
