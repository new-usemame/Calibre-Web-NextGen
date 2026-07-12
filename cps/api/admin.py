# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Admin user-management endpoints for /api/v1 (admin-only).

Reuses cps/admin.py's _delete_user (last-admin + Guest guards + the D4 per-user
data purge) for deletion, and the canonical helper validators
(check_username / valid_password / check_email) for creation, so the SPA and the
legacy admin page enforce identical rules. Role changes guard against demoting
the last admin so an admin can't lock everyone out.
"""
from flask import jsonify, request
from sqlalchemy.exc import IntegrityError

from . import api_v1
from .. import ub, constants, config, calibre_db
from ..cw_login import current_user
from ..cw_babel import get_available_locale
from ..usermanagement import login_required_if_no_ano
from ..helper import (valid_email, check_email, check_username, valid_password,
                      generate_password_hash, reset_password)
from ..admin import _delete_user

# UI-configuration fields the SPA admin form can read/write natively. Scoped to
# the safe, high-traffic display settings — the deep security config (LDAP,
# OAuth, SMTP, SSL, external binaries) stays on the legacy pages.
_UI_CONFIG_INT = ("config_books_per_page", "config_random_books",
                  "config_authors_max", "config_theme")
_UI_CONFIG_STR = ("config_calibre_web_title", "config_default_language",
                  "config_default_locale", "config_server_announcement")

# SPA role key -> the User.role bitmask bit. ROLE_ANONYMOUS is intentionally
# excluded — it's not an admin-assignable permission.
ROLE_BITS = {
    "admin": constants.ROLE_ADMIN,
    "download": constants.ROLE_DOWNLOAD,
    "upload": constants.ROLE_UPLOAD,
    "edit": constants.ROLE_EDIT,
    "passwd": constants.ROLE_PASSWD,
    "edit_shelfs": constants.ROLE_EDIT_SHELFS,
    "delete_books": constants.ROLE_DELETE_BOOKS,
    "viewer": constants.ROLE_VIEWER,
}


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _require_admin():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_admin():
        return _err("forbidden", "Admin access required", 403)
    return None


def _serialize_user(u):
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email or "",
        "kindle_mail": u.kindle_mail or "",
        "locale": u.locale,
        "default_language": u.default_language,
        "is_guest": u.name == "Guest",
        "roles": {key: bool(u.role & bit) for key, bit in ROLE_BITS.items()},
    }


def _other_admin_count(exclude_id):
    return (ub.session.query(ub.User)
            .filter(ub.User.role.op('&')(constants.ROLE_ADMIN) == constants.ROLE_ADMIN,
                    ub.User.id != exclude_id)
            .count())


@api_v1.route("/admin/users")
@login_required_if_no_ano
def admin_list_users():
    guard = _require_admin()
    if guard:
        return guard
    users = ub.session.query(ub.User).order_by(ub.User.id.asc()).all()
    # Hide the anonymous/guest row unless anon browsing is on (matches the legacy
    # admin table behaviour).
    items = [_serialize_user(u) for u in users
             if (u.role & constants.ROLE_ANONYMOUS) != constants.ROLE_ANONYMOUS]
    return jsonify({"items": items})


@api_v1.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@login_required_if_no_ano
def admin_reset_user_password(user_id):
    """Generate and email a replacement password without exposing it to SPA."""
    guard = _require_admin()
    if guard:
        return guard
    if current_user.id == user_id:
        return _err("conflict", "Use Account settings to change your own password", 409)
    target = ub.session.query(ub.User).filter(ub.User.id == user_id).first()
    if target is None:
        return _err("not_found", "User not found", 404)
    if target.name == "Guest" or target.role & constants.ROLE_ANONYMOUS:
        return _err("invalid_target", "The Guest password cannot be reset", 409)
    try:
        target_email = valid_email(target.email or "")
    except Exception:
        target_email = ""
    if not target_email:
        return _err("email_required", "This user needs a valid email address", 409)
    if not config.get_mail_server_configured():
        return _err("mail_not_configured", "Configure mail before resetting passwords", 409)
    result, _ = reset_password(user_id)
    if result != 1:
        return _err("reset_failed", "The password was not changed; the reset email could not be queued", 500)
    return jsonify({"ok": True, "message": "A password reset email has been queued."}), 202


def _ui_config_payload():
    locales = [{"id": str(loc), "name": loc.display_name} for loc in get_available_locale()]
    languages = [{"id": "all", "name": "Show All"}]
    try:
        languages += [{"id": l.lang_code, "name": l.name}
                      for l in calibre_db.speaking_language()]
    except Exception:
        pass
    return {
        "config_calibre_web_title": config.config_calibre_web_title,
        "config_books_per_page": config.config_books_per_page,
        "config_random_books": config.config_random_books,
        "config_authors_max": config.config_authors_max,
        "config_theme": config.config_theme,
        "config_default_language": config.config_default_language,
        "config_default_locale": config.config_default_locale,
        "config_server_announcement": config.config_server_announcement or "",
        "locales": locales,
        "languages": languages,
    }


def _mail_payload():
    """Mail settings for the SPA form. The password is WRITE-ONLY — never
    returned; only whether one is set."""
    return {
        "mail_server": config.mail_server or "",
        "mail_port": config.mail_port,
        "mail_use_ssl": config.mail_use_ssl,
        "mail_login": config.mail_login or "",
        "mail_from": config.mail_from or "",
        "mail_size_mb": int((config.mail_size or 0) / 1024 / 1024),
        "mail_server_type": config.mail_server_type,
        "has_password": bool(getattr(config, "mail_password_e", None)),
    }


@api_v1.route("/admin/mailsettings")
@login_required_if_no_ano
def admin_get_mail():
    """Read SMTP settings (admin only). SECURITY: never returns the password."""
    guard = _require_admin()
    if guard:
        return guard
    return jsonify(_mail_payload())


@api_v1.route("/admin/mailsettings", methods=["POST"])
@login_required_if_no_ano
def admin_update_mail():
    """Update SMTP settings (admin only). The password is write-only — set only
    when a non-empty value is supplied. SECURITY-REVIEW: writes a secret; run
    /security-review before merging this branch (CLAUDE.md hard-rule 3c)."""
    guard = _require_admin()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    for key in ("mail_server", "mail_from", "mail_login"):
        if key in data:
            setattr(config, key, str(data[key] or "").strip())
    for key in ("mail_port", "mail_use_ssl", "mail_server_type"):
        if key in data:
            try:
                setattr(config, key, int(data[key]))
            except (TypeError, ValueError):
                return _err("invalid_request", "%s must be a number" % key, 400)
    if "mail_size_mb" in data:
        try:
            config.mail_size = int(data["mail_size_mb"]) * 1024 * 1024
        except (TypeError, ValueError):
            return _err("invalid_request", "mail_size_mb must be a number", 400)
    # Write-only password: only overwrite when the admin actually typed a new one.
    if data.get("mail_password"):
        config.mail_password_e = str(data["mail_password"])
    try:
        config.save()
    except Exception as ex:
        return _err("db_error", "Could not save mail settings: %s" % ex, 500)
    return jsonify(_mail_payload())


@api_v1.route("/admin/config")
@login_required_if_no_ano
def admin_get_config():
    """Read the SPA-editable UI configuration (admin only)."""
    guard = _require_admin()
    if guard:
        return guard
    return jsonify(_ui_config_payload())


@api_v1.route("/admin/config", methods=["POST"])
@login_required_if_no_ano
def admin_update_config():
    """Update the SPA-editable UI configuration. Only the whitelisted display
    fields are writable here; deep/security config stays on the legacy pages."""
    guard = _require_admin()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    for key in _UI_CONFIG_INT:
        if key in data:
            try:
                setattr(config, key, int(data[key]))
            except (TypeError, ValueError):
                return _err("invalid_request", "%s must be a number" % key, 400)
    for key in _UI_CONFIG_STR:
        if key in data:
            setattr(config, key, str(data[key] or ""))
    try:
        config.save()
    except Exception as ex:
        return _err("db_error", "Could not save configuration: %s" % ex, 500)
    return jsonify(_ui_config_payload())


@api_v1.route("/admin/users", methods=["POST"])
@login_required_if_no_ano
def admin_create_user():
    """Create a household/library user. Mirrors cps/admin.py _handle_new_user:
    same validators, same config-derived defaults (role, locale, language,
    content restrictions, dark theme), so a user created here is indistinguishable
    from one created via the legacy admin page."""
    guard = _require_admin()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    password = data.get("password") or ""
    if not name or not password:
        return _err("invalid_request", "Username and password are required", 400)

    new_user = ub.User()
    try:
        new_user.name = check_username(name)                       # raises if taken/invalid
        new_user.password = generate_password_hash(valid_password(password))  # enforces policy
        email = (data.get("email") or "").strip()
        if email:
            new_user.email = check_email(valid_email(email))       # raises if taken/invalid
        if data.get("kindle_mail"):
            new_user.kindle_mail = valid_email(data["kindle_mail"])
    except Exception as ex:  # validators raise generic Exception carrying a message
        return _err("invalid_request", str(ex), 400)

    # Roles: explicit set from the request, else the configured default role.
    roles = data.get("roles")
    if isinstance(roles, dict):
        role = 0
        for key, bit in ROLE_BITS.items():
            if roles.get(key):
                role |= bit
        new_user.role = role
    else:
        new_user.role = config.config_default_role

    new_user.locale = data.get("locale") or config.config_default_locale or "en"
    new_user.default_language = data.get("default_language") or config.config_default_language or "all"
    # Inherit the instance's content-visibility defaults + sidebar, like legacy.
    new_user.allowed_tags = config.config_allowed_tags
    new_user.denied_tags = config.config_denied_tags
    new_user.allowed_column_value = config.config_allowed_column_value
    new_user.denied_column_value = config.config_denied_column_value
    new_user.sidebar_view = config.config_default_show
    new_user.theme = 1  # caliBlur dark, matching _handle_new_user

    try:
        ub.session.add(new_user)
        ub.session.commit()
    except IntegrityError:
        ub.session.rollback()
        return _err("conflict", "An account already exists for this email or name", 409)
    except Exception as ex:
        ub.session.rollback()
        return _err("db_error", "Could not create user: %s" % ex, 500)

    return jsonify(_serialize_user(new_user)), 201


@api_v1.route("/admin/users/<int:user_id>", methods=["POST"])
@login_required_if_no_ano
def admin_update_user(user_id):
    guard = _require_admin()
    if guard:
        return guard
    user = ub.session.query(ub.User).filter(ub.User.id == user_id).first()
    if not user:
        return _err("not_found", "User not found", 404)

    data = request.get_json(silent=True) or {}

    if "roles" in data and isinstance(data["roles"], dict):
        new_role = user.role
        for key, bit in ROLE_BITS.items():
            if key in data["roles"]:
                if data["roles"][key]:
                    new_role |= bit
                else:
                    new_role &= ~bit
        # Lockout guard: never let the last admin lose the admin role.
        losing_admin = (user.role & constants.ROLE_ADMIN) and not (new_role & constants.ROLE_ADMIN)
        if losing_admin and _other_admin_count(user.id) == 0:
            return _err("conflict", "Can't remove admin from the last administrator", 400)
        user.role = new_role

    try:
        if "email" in data:
            new_email = valid_email(data.get("email") or "")
            if new_email and new_email != user.email:
                user.email = check_email(new_email)  # raises if taken
        if "kindle_mail" in data:
            user.kindle_mail = valid_email(data.get("kindle_mail") or "")
        if "locale" in data and data["locale"]:
            user.locale = data["locale"]
        if "default_language" in data and data["default_language"]:
            user.default_language = data["default_language"]
    except Exception as ex:  # validators raise generic Exception with a message
        ub.session.rollback()
        return _err("invalid_request", str(ex), 400)

    try:
        ub.session.commit()
    except Exception as ex:
        ub.session.rollback()
        return _err("db_error", "Could not save user: %s" % ex, 500)

    return jsonify(_serialize_user(user))


@api_v1.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required_if_no_ano
def admin_delete_user(user_id):
    guard = _require_admin()
    if guard:
        return guard
    if user_id == int(current_user.id):
        return _err("conflict", "You can't delete your own account here", 400)
    user = ub.session.query(ub.User).filter(ub.User.id == user_id).first()
    if not user:
        return _err("not_found", "User not found", 404)
    try:
        # _delete_user enforces the last-admin + Guest guards and purges the
        # user's per-book data (read status, bookmarks, annotations + backups).
        _delete_user(user)
    except Exception as ex:
        ub.session.rollback()
        return _err("conflict", str(ex), 400)
    return "", 204
