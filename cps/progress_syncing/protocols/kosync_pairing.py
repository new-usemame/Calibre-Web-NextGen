# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Connect an e-reader with a code: the device's two calls.

The device has no credentials yet, so both are public (and CSRF-exempt like
every call a device makes). They are rate-limited per client address, and the
poll additionally per device code by the service's minimum poll spacing,
which also holds when the admin has switched the rate limiter off.

    POST /kosync/pair/start {device, device_id}
      200 {user_code: "K7M4-QX2P", device_code, expires_in, interval,
           verify_url: "<server>/pair",
           verify_url_complete: "<server>/pair?code=K7M4QX2P"}
      400 invalid_request   409 pairing_unavailable (no website to approve on)
      429 too_many_requests / rate_limit_exceeded   503 sync switched off

    POST /kosync/pair/poll {device_code}
      200 {status: "pending", interval} | {status: "denied"}
          | {status: "approved", server, username, password}   (exactly once)
      400 invalid_request   404 / 410 {status: "expired"}
      429 {status: "slow_down", interval}   503 sync switched off

The approving half is in ``cps/api/koreader_devices.py``; the rules are in
``cps/services/koreader_pairing.py``.
"""

from flask import jsonify, request

from ... import csrf, limiter, logger, spa
from ...services import koreader_pairing
from .kosync import (
    MAX_DEVICE_ID_LENGTH,
    MAX_DEVICE_LENGTH,
    _require_kosync_enabled,
    is_valid_field,
    kosync,
)

try:
    from flask_limiter import RateLimitExceeded
    from flask_limiter.util import get_remote_address
except ImportError:  # flask_limiter is optional outside the container
    class RateLimitExceeded(Exception):
        pass

    def get_remote_address():
        return request.remote_addr or "127.0.0.1"

log = logger.create()

PAIR_PATH = "/pair"


def _json(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    # device_code and the minted password are credentials: never cached.
    response.headers["Cache-Control"] = "no-store"
    return response


def _rate_limited():
    """This endpoint's limiter verdict: True when over the limit.

    Same model as ``cps/api/auth.py``: the app-wide limiter runs with
    ``auto_check=False`` and each public view checks itself. A broken limiter
    store fails open (logged) rather than locking every device out.
    """
    if limiter is None:
        return False
    try:
        limiter.check()
    except RateLimitExceeded:
        return True
    except Exception as ex:  # storage outage: fail open
        log.error("Rate limiter backend error: %s", ex)
    return False


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@csrf.exempt
@kosync.route("/kosync/pair/start", methods=["POST"])
@limiter.limit("40/day", key_func=get_remote_address)
@limiter.limit("6/minute", key_func=get_remote_address)
def pair_start():
    """Open a pairing request and give the device its code."""
    blocked = _require_kosync_enabled()
    if blocked:
        return blocked
    if _rate_limited():
        return _json({"error": "rate_limit_exceeded",
                      "message": "Too many pairing requests. Wait a minute and try again."}, 429)
    if not spa.spa_available():
        # Codes are approved on the web app's e-readers page; without it a
        # code could never be approved.
        return _json({"error": "pairing_unavailable",
                      "message": "This server cannot approve e-readers by code. "
                                 "Sign in with a username and password instead."}, 409)
    body = _body()
    device = body.get("device")
    device_id = body.get("device_id")
    if (not is_valid_field(device) or len(device) > MAX_DEVICE_LENGTH
            or (device_id is not None
                and (not is_valid_field(device_id) or len(device_id) > MAX_DEVICE_ID_LENGTH))):
        return _json({"error": "invalid_request",
                      "message": "device (and device_id, when sent) must be short non-empty strings."},
                     400)
    try:
        started = koreader_pairing.start(device, address=request.remote_addr)
    except koreader_pairing.PairingError as ex:
        return _json({"error": ex.code, "message": ex.message}, ex.status)
    verify_url = request.url_root.rstrip("/") + PAIR_PATH
    return _json({
        "user_code": koreader_pairing.display_user_code(started.user_code),
        "device_code": started.device_code,
        "expires_in": started.expires_in,
        "interval": started.interval,
        "verify_url": verify_url,
        "verify_url_complete": "%s?code=%s" % (verify_url, started.user_code),
    })


@csrf.exempt
@kosync.route("/kosync/pair/poll", methods=["POST"])
# One device polls every 5 seconds for at most 10 minutes: 120 requests. 1000
# an hour leaves room for several devices behind one household address.
@limiter.limit("1000/hour", key_func=get_remote_address)
def pair_poll():
    """Tell the device whether its code was approved, and hand over the password once."""
    blocked = _require_kosync_enabled()
    if blocked:
        return blocked
    slow_down = {"status": "slow_down", "error": "slow_down",
                 "interval": koreader_pairing.POLL_INTERVAL + 5}
    if _rate_limited():
        return _json(slow_down, 429)
    try:
        result = koreader_pairing.poll(_body().get("device_code"))
    except koreader_pairing.PairingError as ex:
        return _json({"error": ex.code, "message": ex.message}, ex.status)
    if result.status == "slow_down":
        return _json(slow_down, 429)
    if result.status == "expired":
        return _json({"status": "expired", "error": "expired_token",
                      "message": "This code has expired or was already used."},
                     result.http_status)
    if result.status == koreader_pairing.APPROVED:
        return _json({
            "status": "approved",
            "server": request.url_root.rstrip("/"),
            "username": result.username,
            "password": result.password,
        })
    payload = {"status": result.status}
    if result.status == koreader_pairing.PENDING:
        payload["interval"] = koreader_pairing.POLL_INTERVAL
    return _json(payload)
