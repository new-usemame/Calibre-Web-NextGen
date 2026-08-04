# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Versioned JSON API for the NextGen SPA frontend. See notes/FRONTEND-REBUILD-DESIGN.md."""
import os
import traceback
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request, g
from werkzeug.exceptions import HTTPException

from .. import logger, config
from ..cw_login import current_user
from ..usermanagement import load_user_from_reverse_proxy_header

log = logger.create()

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# Endpoints reachable without an authenticated session. Everything else under
# /api/v1 requires auth (or anonymous-browse mode). auth_me/auth_logout handle
# the unauthenticated case gracefully themselves, so they're allowed through too.
_PUBLIC_ENDPOINTS = {
    "api_v1.health",
    "api_v1.auth_csrf",
    "api_v1.auth_login",
    "api_v1.auth_me",
    "api_v1.auth_logout",
    "api_v1.auth_config",
    "api_v1.auth_register",
    "api_v1.auth_forgot",
    # Magic-link (remote) login is for the logged-out device by definition — the
    # endpoints self-gate on config_remote_login and consume a one-time token.
    "api_v1.auth_magic_link_start",
    "api_v1.auth_magic_link_poll",
    "api_v1.i18n_catalog",
}


# Methods that cannot change state. Browsers also omit Origin on a same-origin
# GET, so checking these would reject ordinary reads.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _parse_trusted_origins(raw):
    """Split a comma-separated CWNG_TRUSTED_ORIGINS value, dropping blanks."""
    return tuple(o.strip() for o in (raw or "").split(",") if o.strip())


# Optional escape hatch for a reverse proxy that rewrites Host and sends no
# X-Forwarded-Host, where request.host_url is the internal name and would not
# match the origin the browser actually used. Comma-separated, e.g.
# CWNG_TRUSTED_ORIGINS=https://books.example.com,https://books.lan:8443
# Unset by default — nothing is required to make a normal deployment work.
_EXTRA_TRUSTED_ORIGINS = _parse_trusted_origins(os.environ.get("CWNG_TRUSTED_ORIGINS"))


def _origin_tuple(value):
    """(scheme, host, port) for `value`, default port made explicit, or None when
    it states no usable origin. Comparing the tuple — rather than the raw string —
    is what keeps `cwng.local.evil.example` from matching `cwng.local` and keeps
    `https://h:443` matching `https://h`."""
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:          # malformed port, e.g. "http://h:notaport"
        return None
    if not parts.scheme or not parts.hostname:
        return None
    return (parts.scheme.lower(), parts.hostname.lower(),
            port or _DEFAULT_PORTS.get(parts.scheme.lower()))


@api_v1.before_request
def _reject_cross_site_mutation():
    """Reject a state-changing /api/v1 request that says it came from another site.

    The CSRF token is enforced separately by the global CSRFProtect middleware; this
    is the Origin layer underneath it (#1370). Flask-WTF's own referer check is
    disabled here (WTF_CSRF_SSL_STRICT=False in cps/__init__.py), so before this
    hook a request bearing a valid token plus `Origin: https://evil.example` was
    accepted and performed the write.

    Deliberately "verify only if stated": a browser cannot suppress Origin on a
    cross-site mutation, so this closes the browser CSRF vector in full, while curl
    and native clients that send no such header keep working — they carry no ambient
    credentials, so they were never the vector. `Origin: null` (sandboxed iframe) is
    a stated-but-unusable origin and is refused rather than waved through.

    Registered above _require_api_auth so the verdict does not depend on whether the
    forged request happened to be authenticated, and so it also covers the endpoints
    in _PUBLIC_ENDPOINTS (a cross-site POST to auth_login is login-CSRF).

    Host comparison uses request.host_url, which ProxyFix has already rewritten from
    X-Forwarded-Host/Proto, so this holds behind a reverse proxy and on a subpath. The
    one setup it cannot infer is a proxy that rewrites Host and forwards no
    X-Forwarded-Host — there request.host_url is the internal name; those operators
    name their public origin in CWNG_TRUSTED_ORIGINS. Erring towards accepting is
    deliberate: a false rejection breaks every write for a legitimate user, while the
    vector this closes is already covered by the CSRF token and a SameSite=Lax cookie.
    """
    if request.method in _SAFE_METHODS:
        return None
    stated = request.headers.get("Origin") or request.headers.get("Referer")
    if not stated:
        return None
    stated_tuple = _origin_tuple(stated)
    if stated_tuple is None:
        # Stated but unusable ("null", malformed). Never our SPA.
        accepted = False
    else:
        accepted = stated_tuple == _origin_tuple(request.host_url) or any(
            stated_tuple == _origin_tuple(extra) for extra in _EXTRA_TRUSTED_ORIGINS)
    if accepted:
        return None
    log.warning("Rejected cross-site %s %s (stated origin %r, expected %r)",
                request.method, request.path, stated, request.host_url)
    return jsonify({"error": {"code": "cross_site_request",
                              "message": "Cross-site request rejected"}}), 403


@api_v1.before_request
def _require_api_auth():
    """Gate the whole API surface, returning JSON 401 (never an HTML 302) when
    unauthenticated. Mirrors usermanagement.login_required_if_no_ano so behaviour
    matches the rest of the app (reverse-proxy header login -> anon-browse ->
    session), but an SPA fetch gets a clean 401 it can act on instead of a redirect
    to the HTML login page (which would surface as a JSON parse error on session
    expiry). The per-route @login_required_if_no_ano decorators remain as
    defence-in-depth and per-route documentation."""
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if config.config_allow_reverse_proxy_header_login:
        user = load_user_from_reverse_proxy_header(request)
        if user:
            g.flask_httpauth_user = user
            return None
        g.flask_httpauth_user = None
    if config.config_anonbrowse == 1:
        return None
    if current_user.is_authenticated:
        return None
    return jsonify({"error": {"code": "unauthorized",
                              "message": "Authentication required"}}), 401


@api_v1.errorhandler(HTTPException)
def handle_http_exception(exc):
    """Return JSON instead of HTML for all HTTPExceptions raised inside the API blueprint."""
    return jsonify({"error": {"code": exc.name.lower().replace(" ", "_"),
                              "message": exc.description}}), exc.code


@api_v1.errorhandler(Exception)
def handle_generic_exception(exc):
    """Return a JSON 500 and log the full traceback; never silently swallow."""
    log.error("Unhandled exception in api_v1: %s", traceback.format_exc())
    return jsonify({"error": {"code": "internal_server_error",
                              "message": "An unexpected error occurred"}}), 500


@api_v1.route("/health")
def health():
    return jsonify({"status": "ok", "api": "v1"})


# Route modules attach their views to api_v1 on import; import LAST so api_v1 exists.
from . import auth     # noqa: E402,F401
from . import i18n     # noqa: E402,F401
from . import books    # noqa: E402,F401
from . import actions  # noqa: E402,F401
from . import browse   # noqa: E402,F401
from . import shelves  # noqa: E402,F401
from . import search   # noqa: E402,F401
from . import account  # noqa: E402,F401
from . import reader   # noqa: E402,F401
from . import edit     # noqa: E402,F401
from . import upload   # noqa: E402,F401
from . import admin    # noqa: E402,F401
from . import info     # noqa: E402,F401
from . import duplicates  # noqa: E402,F401
from . import magicshelves  # noqa: E402,F401
from . import comic     # noqa: E402,F401
from . import admin_security  # noqa: E402,F401
