# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Auth endpoints for /api/v1 — reuse the existing cw_login session + CSRF."""
import json
from datetime import datetime

from flask import jsonify, request, url_for
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from . import api_v1
from .serializers import serialize_user
from .. import ub, config, constants, limiter
from ..cw_login import current_user, login_user
from ..logout import cleanup_local_logout
from ..ui_themes import config_theme_code
from ..helper import (
    check_username, check_email, check_valid_domain, reset_password,
    send_registration_mail, generate_random_password,
)


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


# Display labels for the fixed GitHub/Google providers, mirroring the strings the
# classic login page hardcodes (cps/templates/login.html). oauth_check maps a
# provider id to its *internal* name ("github"/"google"/"generic"), which is NOT
# what the button should read — hence this explicit label map. The generic (OIDC)
# provider's label is admin-configurable, so it's resolved separately from the
# single source of truth in oauth_bb (fork issue #807).
_OAUTH_STATIC_LABELS = {1: "Login with GitHub", 2: "Login with Google"}
_GENERIC_OAUTH_ID = 3


def _oauth_providers():
    """OAuth/OIDC providers for the SPA login buttons, as {id, name, url}.

    Matches the classic login page EXACTLY: providers are only offered when the
    instance's login type is OAuth (config_login_type == LOGIN_OAUTH) AND the
    provider is registered in oauth_check. The ``name`` is the human-facing button
    label the classic page renders — a fixed "Login with GitHub"/"Login with
    Google" for those two, and the admin-configured "Button label" for the generic
    OIDC provider (falling back to "OpenID Connect") — NOT the internal provider
    name stored in oauth_check. Without the login-type gate the buttons would
    appear on a standard- or LDAP-login instance and error on click (the provider
    isn't configured). URLs are built with url_for so they're correct behind a
    reverse-proxy subpath. Endpoints map to the same oauth.* routes the classic
    template links to.

    SECURITY: this feeds an UNAUTHENTICATED endpoint (/api/v1/auth/config). Only
    the display label, numeric id and login URL are exposed here — never the
    client secret, client id, or any other provider configuration. Build the
    output dict explicitly; do not serialise the OAuthProvider row.
    """
    if config.config_login_type != constants.LOGIN_OAUTH:
        return []
    endpoints = {1: "oauth.github_login", 2: "oauth.google_login", 3: "oauth.generic_login"}
    try:
        from ..oauth_bb import oauth_check, generic_oauth_login_button
        out = []
        for cid in oauth_check:
            ep = endpoints.get(cid)
            if not ep:
                continue
            if cid == _GENERIC_OAUTH_ID:
                label = generic_oauth_login_button()
            else:
                label = _OAUTH_STATIC_LABELS.get(cid, oauth_check[cid])
            try:
                out.append({"id": cid, "name": label, "url": url_for(ep)})
            except Exception:
                pass
        return out
    except Exception:
        return []

try:
    from flask_wtf.csrf import generate_csrf
except ImportError:  # flask_wtf is optional/container-only
    generate_csrf = None

try:
    from flask_limiter.util import get_remote_address
except ImportError:  # flask_limiter is optional/container-only
    get_remote_address = lambda: "127.0.0.1"  # noqa: E731


def _login_key_func():
    """Rate-limit key: posted username (lower-stripped), falling back to remote IP."""
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip().lower()
    return username or get_remote_address()


@api_v1.route("/auth/csrf")
def auth_csrf():
    token = generate_csrf() if generate_csrf else ""
    return jsonify({"csrf_token": token})


def _instance_name():
    """The admin-configured site title (config_calibre_web_title). The classic
    UI shows it in the navbar and <title> on every page (render_template.py
    passes instance=…); the SPA reads it from here. Blank falls back to the
    stock name."""
    return getattr(config, "config_calibre_web_title", None) or "Calibre-Web NextGen"


def _server_features():
    """Instance-level capability flags the SPA gates UI off (mirrors the Jinja
    template gates: hide-books button, send-to-e-reader, register link, …).
    Authoritative enforcement stays server-side on each endpoint."""
    try:
        mail_ok = bool(config.get_mail_server_configured())
    except Exception:
        mail_ok = False
    return {
        "hide_books": bool(getattr(config, "config_user_hide_enabled", False)),
        "mail_configured": mail_ok,
        "public_registration": bool(getattr(config, "config_public_reg", False)),
        "anon_browse": bool(getattr(config, "config_anonbrowse", False)),
        "kobo_sync": bool(getattr(config, "config_kobo_sync", False)),
    }


# Written by cps.cwa_functions.set_profile_picture (the profile_pictures
# blueprint): a {username: "data:image/…;base64,…"} map. The classic UI reads
# the whole map via /profile_pictures/user_profiles.json and looks the name up
# client-side; the SPA gets only the current user's picture on /me instead, so
# it never downloads every user's avatar. Path is kept in sync with that writer.
_USER_PROFILES_JSON = "/config/user_profiles.json"


def _user_avatar(name):
    """Return the profile-picture data-URI set for ``name`` in the classic
    profile-pictures panel, or None. A missing file, malformed JSON, absent
    user, or non-image value must never fault the /me response — the SPA falls
    back to a neutral glyph. The ``data:image/`` guard keeps a corrupted entry
    from becoming an arbitrary URL the frontend would render."""
    try:
        with open(_USER_PROFILES_JSON, "r") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    value = data.get(name) if isinstance(data, dict) else None
    return value if isinstance(value, str) and value.startswith("data:image/") else None


def _me_payload(user):
    """Single source of truth for the /me-shaped payload the SPA consumes across
    login, magic-link, and /auth/me. Keeps the three sites from drifting."""
    payload = serialize_user(user)
    payload["features"] = _server_features()
    payload["instance_name"] = _instance_name()
    payload["avatar"] = _user_avatar(user.name)
    catalog_settings = (getattr(user, "view_settings", None) or {}).get("catalog", {})
    default_filter = catalog_settings.get("default_filter") if isinstance(catalog_settings, dict) else None
    payload["catalog"] = {
        "default_filter": default_filter if isinstance(default_filter, dict) else None,
    }
    payload["display"] = {
        # Some auth tests and bootstrap paths intentionally provide a minimal
        # config object. Keep /me available there with the schema defaults.
        "books_per_page": int(getattr(config, "config_books_per_page", 60) or 60),
        "random_books": int(getattr(config, "config_random_books", 4) or 4),
    }
    return payload


@api_v1.route("/auth/me")
def auth_me():
    if not current_user.is_authenticated:
        return jsonify({"error": {"code": "unauthenticated", "message": "Login required"}}), 401
    return jsonify(_me_payload(current_user))


@api_v1.route("/auth/login", methods=["POST"])
@limiter.limit("40/day", key_func=_login_key_func)
@limiter.limit("3/minute", key_func=_login_key_func)
def auth_login():
    # I2: Honour config_disable_standard_login.
    # LDAP/OAuth login routing is deferred to the auth-bridge sub-project (sub-project 2).
    if config.config_disable_standard_login:
        return jsonify({"error": {"code": "standard_login_disabled",
                                  "message": "Standard login is disabled"}}), 403

    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    user = ub.session.query(ub.User).filter(func.lower(ub.User.name) == username).first()
    if user and not user.role_anonymous() and check_password_hash(str(user.password), password):
        login_user(user, remember=bool(data.get("remember")))
        return jsonify(_me_payload(user))
    return jsonify({"error": {"code": "invalid_credentials",
                              "message": "Invalid username or password"}}), 401


@api_v1.route("/auth/logout", methods=["POST"])
def auth_logout():
    cleanup_local_logout()
    return "", 204


@api_v1.route("/auth/config")
def auth_config():
    """Public: what the login screen needs to render register / forgot / OAuth."""
    try:
        mail_ok = bool(config.get_mail_server_configured())
    except Exception:
        mail_ok = False
    # Magic-link ("remote") login — admin toggle config_remote_login. Same gate as
    # the classic login page; URL via url_for so it's reverse-proxy-subpath safe.
    remote_login = bool(getattr(config, "config_remote_login", False))
    try:
        remote_login_url = url_for("remotelogin.remote_login") if remote_login else ""
    except Exception:
        remote_login_url = ""
    return jsonify({
        "instance_name": _instance_name(),
        "public_registration": bool(getattr(config, "config_public_reg", False)),
        "register_email": bool(getattr(config, "config_register_email", False)),
        "mail_configured": mail_ok,
        "standard_login_disabled": bool(getattr(config, "config_disable_standard_login", False)),
        "oauth_providers": _oauth_providers(),
        "remote_login": remote_login,
        "remote_login_url": remote_login_url,
    })


def _build_qr_data_url(verify_url):
    """Return a data: URL for a QR encoding verify_url, or "" if qrcode/PIL isn't
    available. Mirrors remotelogin.remote_login's QR build (single source of the
    same dependency surface) so the SPA page matches the classic one."""
    try:
        import qrcode
        from base64 import b64encode
        from io import BytesIO
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H,
                           box_size=5, border=4)
        qr.add_data(verify_url)
        qr.make(fit=True)
        img = qr.make_image()
        buf = BytesIO()
        img.save(buf, format="jpeg")
        return "data:image/jpeg;base64, %s" % b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


@api_v1.route("/auth/magic-link/start", methods=["POST"])
@limiter.limit("40/day", key_func=lambda: get_remote_address())
@limiter.limit("6/minute", key_func=lambda: get_remote_address())
def auth_magic_link_start():
    """Begin a magic-link (remote) login: mint a RemoteAuthToken and return the
    verify URL + QR for the SPA to render. The waiting (logged-out) device then
    polls /auth/magic-link/poll while an already-signed-in device authorises the
    token by visiting verify_url (/verify/<token>, login-gated). Same mechanism
    the classic /remote/login page uses; gated on the same config_remote_login."""
    if not bool(getattr(config, "config_remote_login", False)):
        return _err("magic_link_disabled", "Magic-link login is disabled", 403)
    if current_user.is_authenticated:
        return _err("already_authenticated", "You're already signed in", 400)
    auth_token = ub.RemoteAuthToken()
    ub.session.add(auth_token)
    ub.session_commit()
    try:
        verify_url = url_for("remotelogin.verify_token", token=auth_token.auth_token, _external=True)
    except Exception:
        verify_url = ""
    return jsonify({
        "token": auth_token.auth_token,
        "verify_url": verify_url,
        "qrcode": _build_qr_data_url(verify_url),
        "expires_in_minutes": 10,
    })


@api_v1.route("/auth/magic-link/poll", methods=["POST"])
@limiter.limit("240/hour", key_func=lambda: get_remote_address())
def auth_magic_link_poll():
    """Poll a magic-link token. Returns one of: not_verified | success | expired |
    not_found. On success the waiting device is logged in (session cookie set) and
    the token is consumed. Mirrors remotelogin.token_verified, JSON-shaped for the
    SPA (serialized user instead of a flash + redirect)."""
    if not bool(getattr(config, "config_remote_login", False)):
        return _err("magic_link_disabled", "Magic-link login is disabled", 403)
    data = request.get_json(silent=True) or request.form
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"status": "not_found"})
    auth_token = ub.session.query(ub.RemoteAuthToken).filter(
        ub.RemoteAuthToken.auth_token == token,
        ub.RemoteAuthToken.token_type == 0,
    ).first()
    if auth_token is None:
        return jsonify({"status": "not_found"})
    if datetime.now() > auth_token.expiration:
        ub.session.delete(auth_token)
        ub.session_commit()
        return jsonify({"status": "expired"})
    if not auth_token.verified:
        return jsonify({"status": "not_verified"})
    user = ub.session.query(ub.User).filter(ub.User.id == auth_token.user_id).first()
    if user is None or user.role_anonymous():
        ub.session.delete(auth_token)
        ub.session_commit()
        return jsonify({"status": "not_found"})
    login_user(user)
    ub.session.delete(auth_token)
    ub.session_commit("User {} logged in via SPA magic-link, token deleted".format(user.name))
    return jsonify({"status": "success", "user": _me_payload(user)})


@api_v1.route("/auth/register", methods=["POST"])
@limiter.limit("40/day", key_func=lambda: get_remote_address())
@limiter.limit("3/minute", key_func=lambda: get_remote_address())
def auth_register():
    """Public self-registration. Mirrors web.register_post: gated on
    config_public_reg, requires a configured mail server, validates the
    username/email + allowed-domain, then emails the generated password."""
    if not config.config_public_reg:
        return _err("registration_disabled", "Public registration is disabled", 403)
    if not config.get_mail_server_configured():
        return _err("mail_not_configured", "The server's email settings aren't configured", 400)
    if current_user.is_authenticated:
        return _err("already_authenticated", "You're already signed in", 400)

    data = request.get_json(silent=True) or request.form
    email = (data.get("email") or "").strip()
    name = (data.get("name") or "").strip()
    nickname = email if config.config_register_email else name
    if not nickname or not email:
        return _err("invalid_request", "Please complete all fields", 400)
    try:
        nickname = check_username(nickname)
        email = check_email(email)
    except Exception as ex:  # validators raise generic Exception with a message
        return _err("invalid_request", str(ex), 400)
    if not check_valid_domain(email):
        return _err("email_not_allowed", "That email domain isn't allowed to register", 403)

    content = ub.User()
    content.name = nickname
    content.email = email
    password = generate_random_password(config.config_password_min_length)
    content.password = generate_password_hash(password)
    content.role = config.config_default_role
    content.locale = config.config_default_locale
    content.sidebar_view = config.config_default_show
    # Seed the account with the instance default theme (Admin -> Theme), the
    # same as every other create path. Normalising matters: a legacy
    # config_theme of 0 means light, but stored raw as User.theme it reads back
    # as dark (#921).
    try:
        content.theme = config_theme_code(getattr(config, "config_theme", None))
    except Exception:
        pass
    try:
        ub.session.add(content)
        ub.session.commit()
        try:
            from ..oauth_bb import register_user_with_oauth
            register_user_with_oauth(content)
        except Exception:
            pass  # oauth optional
        send_registration_mail(email, nickname, password)
    except Exception:
        ub.session.rollback()
        return _err("server_error", "Could not complete registration. Try again later.", 500)
    return jsonify({"ok": True, "message": "Confirmation email sent"})


@api_v1.route("/auth/forgot", methods=["POST"])
@limiter.limit("40/day", key_func=lambda: get_remote_address())
@limiter.limit("3/minute", key_func=lambda: get_remote_address())
def auth_forgot():
    """Email a reset password. Always returns ok (never reveals whether the
    account exists) — an improvement over the legacy flash that leaked it."""
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip().lower()
    if username:
        user = ub.session.query(ub.User).filter(func.lower(ub.User.name) == username).first()
        if user is not None and user.name != "Guest":
            try:
                reset_password(user.id)
            except Exception:
                pass
    return jsonify({"ok": True,
                    "message": "If that account exists, a reset email has been sent."})
