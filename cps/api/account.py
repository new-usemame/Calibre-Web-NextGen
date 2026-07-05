# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Self-service account endpoints for /api/v1 (the logged-in user's own profile).

Reuses the same validators the legacy /me form uses (valid_password applies the
configured password policy; valid_email/check_email validate + dedupe), so the
rules can't drift. Unlike the legacy form, the password change requires the
current password (defence against a hijacked session silently changing it) —
flag for /security-review before this branch merges.
"""
import secrets

from flask import jsonify, request
from flask_babel import gettext as _
from werkzeug.security import check_password_hash, generate_password_hash

from . import api_v1
from .. import calibre_db, ub
from ..cw_login import current_user
from ..cw_babel import get_available_locale
from ..helper import valid_password, valid_email, check_email
from .serializers import (SIDEBAR_VISIBILITY_BITS, ORDERABLE_SIDEBAR_KEYS,
                          serialize_sidebar_visibility, serialize_sidebar_order)


def _iso(dt):
    return dt.isoformat() if dt else None


def _app_passwords():
    """Active (non-revoked) app passwords for the current user — token never
    returned here (only once, at creation)."""
    rows = (ub.session.query(ub.UserAppPassword)
            .filter(ub.UserAppPassword.user_id == current_user.id,
                    ub.UserAppPassword.revoked == False)  # noqa: E712
            .order_by(ub.UserAppPassword.created_at.desc())
            .all())
    return [{"id": r.id, "label": r.label,
             "created_at": _iso(r.created_at), "last_used_at": _iso(r.last_used_at)}
            for r in rows]


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _require_real_user():
    """Account endpoints are for a concretely logged-in user — never the
    anonymous-browse guest. Returns an error response, or None when ok."""
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    return None


def _serialize_account():
    locales = [{"id": str(loc), "name": loc.display_name} for loc in get_available_locale()]
    languages = calibre_db.speaking_language()  # sets .name to the display name
    lang_options = [{"id": "all", "name": _("Show All")}]
    lang_options += [{"id": l.lang_code, "name": l.name} for l in languages]
    return {
        "name": current_user.name,
        "email": current_user.email or "",
        "kindle_mail": current_user.kindle_mail or "",
        "kindle_mail_subject": current_user.kindle_mail_subject or "",
        "kobo_only_shelves_sync": bool(current_user.kobo_only_shelves_sync),
        "opds_only_shelves_sync": bool(current_user.opds_only_shelves_sync),
        "locale": current_user.locale,
        "default_language": current_user.default_language,
        "role": {
            "admin": current_user.role_admin(),
            "upload": current_user.role_upload(),
            "edit": current_user.role_edit(),
            "download": current_user.role_download(),
            "delete_books": current_user.role_delete_books(),
            "edit_shelfs": current_user.role_edit_shelfs(),
            "viewer": current_user.role_viewer(),
            "passwd": current_user.role_passwd(),
        },
        "can_change_password": bool(current_user.role_passwd() or current_user.role_admin()),
        # Picker options for the settings form.
        "locales": locales,
        "languages": lang_options,
        "app_passwords": _app_passwords(),
    }


@api_v1.route("/account")
def get_account():
    guard = _require_real_user()
    if guard:
        return guard
    return jsonify(_serialize_account())


@api_v1.route("/account/profile", methods=["POST"])
def update_profile():
    guard = _require_real_user()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}

    try:
        if "email" in data:
            new_email = valid_email(data.get("email") or "")
            if not new_email:
                return _err("invalid_request", "Email can't be empty", 400)
            if new_email != current_user.email:
                # check_email raises if the address is already taken
                current_user.email = check_email(new_email)
        if "kindle_mail" in data:
            current_user.kindle_mail = valid_email(data.get("kindle_mail") or "")
        if "kindle_mail_subject" in data:
            current_user.kindle_mail_subject = (data.get("kindle_mail_subject") or "")[:256]
        if "kobo_only_shelves_sync" in data:
            current_user.kobo_only_shelves_sync = 1 if data.get("kobo_only_shelves_sync") else 0
        if "opds_only_shelves_sync" in data:
            current_user.opds_only_shelves_sync = 1 if data.get("opds_only_shelves_sync") else 0
        if "locale" in data and data["locale"]:
            current_user.locale = data["locale"]
        if "default_language" in data and data["default_language"]:
            current_user.default_language = data["default_language"]
    except Exception as ex:  # validators raise generic Exception with a message
        ub.session.rollback()
        return _err("invalid_request", str(ex), 400)

    try:
        ub.session.commit()
    except Exception as ex:
        ub.session.rollback()
        return _err("db_error", "Could not save profile: %s" % ex, 500)

    return jsonify(_serialize_account())


@api_v1.route("/account/password", methods=["POST"])
def change_password():
    guard = _require_real_user()
    if guard:
        return guard
    if not (current_user.role_passwd() or current_user.role_admin()):
        return _err("forbidden", "You are not allowed to change your password", 403)

    data = request.get_json(silent=True) or {}
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""

    # Verify the current password — never let a session change the password blind.
    if not current_user.password or not check_password_hash(current_user.password, current_password):
        return _err("invalid_credentials", "Current password is incorrect", 400)

    try:
        validated = valid_password(new_password)  # enforces the configured policy
    except Exception as ex:
        return _err("invalid_request", str(ex), 400)

    current_user.password = generate_password_hash(validated)
    try:
        ub.session.commit()
    except Exception as ex:
        ub.session.rollback()
        return _err("db_error", "Could not change password: %s" % ex, 500)

    return "", 204


@api_v1.route("/account/app-passwords", methods=["POST"])
def create_app_password():
    """Create an app password (for OPDS / KOSync HTTP Basic auth). The cleartext
    token is returned ONCE here and never again (only its hash is stored)."""
    guard = _require_real_user()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    label = (data.get("label") or "").strip()
    if not label or len(label) > 64:
        return _err("invalid_request", "Label must be 1-64 characters", 400)
    cleartext = secrets.token_urlsafe(32)
    row = ub.UserAppPassword(user_id=current_user.id, label=label,
                             password_hash=generate_password_hash(cleartext))
    ub.session.add(row)
    try:
        ub.session.commit()
    except Exception as ex:
        ub.session.rollback()
        return _err("db_error", "Could not create app password: %s" % ex, 500)
    # token shown once; the SPA must surface it immediately.
    return jsonify({"id": row.id, "label": row.label, "token": cleartext,
                    "created_at": _iso(row.created_at)}), 201


@api_v1.route("/account/app-passwords/<int:app_password_id>/delete", methods=["POST"])
def revoke_app_password(app_password_id):
    guard = _require_real_user()
    if guard:
        return guard
    row = (ub.session.query(ub.UserAppPassword)
           .filter(ub.UserAppPassword.id == app_password_id,
                   ub.UserAppPassword.user_id == current_user.id)  # scope to caller
           .first())
    if row is None:
        return _err("not_found", "App password not found", 404)
    row.revoked = True
    try:
        ub.session.commit()
    except Exception as ex:
        ub.session.rollback()
        return _err("db_error", "Could not revoke app password: %s" % ex, 500)
    return "", 204


@api_v1.route("/account/sidebar", methods=["POST"])
def update_sidebar():
    """Fork #585 v2 — the logged-in user customizes their own sidebar from the
    new UI: section visibility and entry order.

    Body (both keys optional): ``{"visibility": {key: bool}, "order": [key,...]}``.
      * ``visibility`` flips the user's ``sidebar_view`` bitmask — the SAME
        per-user store the classic UI + OPDS honour, so a toggle here also
        reflects in the classic UI (one config, by design). Keys must be known
        (``SIDEBAR_VISIBILITY_BITS``); unknown → 400.
      * ``order`` persists into ``view_settings['sidebar']['order']`` (per-user,
        no schema change — same mechanism as shelf reorder #237). Must be a list
        of known, unique orderable keys (``ORDERABLE_SIDEBAR_KEYS``); anything
        else → 400.
    Session + CSRF guarded like the other /account mutations (self-service only,
    scoped to ``current_user``).
    """
    guard = _require_real_user()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}

    visibility = data.get("visibility")
    order = data.get("order")
    if visibility is None and order is None:
        return _err("invalid_request", "Nothing to update", 400)

    # ── validate before mutating (all-or-nothing) ──────────────────────────
    if visibility is not None:
        if not isinstance(visibility, dict):
            return _err("invalid_request", "visibility must be an object", 400)
        for key in visibility:
            if key not in SIDEBAR_VISIBILITY_BITS:
                return _err("invalid_request", "Unknown sidebar key: %s" % key, 400)

    if order is not None:
        if not isinstance(order, list):
            return _err("invalid_request", "order must be a list", 400)
        seen = set()
        for key in order:
            if key not in ORDERABLE_SIDEBAR_KEYS:
                return _err("invalid_request", "Unknown sidebar key: %s" % key, 400)
            if key in seen:
                return _err("invalid_request", "Duplicate sidebar key: %s" % key, 400)
            seen.add(key)

    # ── apply ──────────────────────────────────────────────────────────────
    try:
        if visibility is not None:
            view = int(current_user.sidebar_view or 0)
            for key, on in visibility.items():
                bit = SIDEBAR_VISIBILITY_BITS[key]
                if on:
                    view |= bit
                else:
                    view &= ~bit
            current_user.sidebar_view = view
        if order is not None:
            current_user.set_view_property("sidebar", "order", order)
    except Exception as ex:
        ub.session.rollback()
        return _err("invalid_request", str(ex), 400)

    try:
        ub.session.commit()
    except Exception as ex:
        ub.session.rollback()
        return _err("db_error", "Could not save sidebar: %s" % ex, 500)

    return jsonify({
        "sidebar": serialize_sidebar_visibility(current_user),
        "sidebar_order": serialize_sidebar_order(current_user),
    })
