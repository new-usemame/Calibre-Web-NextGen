# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Kobo sync-token lifecycle for the SPA device-pairing surface."""

from flask import jsonify, request, url_for
from werkzeug.urls import urlsplit

from . import api_v1
from .. import config, ub
from ..cw_login import current_user
from ..kobo_auth import (create_or_view_auth_token, find_auth_token,
                         revoke_auth_token)


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _target_user_id(user_id):
    """Apply the classic self-or-admin authorization gate to an API target."""
    if not current_user.is_authenticated or current_user.is_anonymous:
        return None, _err("unauthorized", "You must be signed in", 401)
    target_id = current_user.id if user_id is None else user_id
    if current_user.id != target_id and not current_user.role_admin():
        return None, _err("forbidden", "You may only manage your own Kobo sync URL", 403)
    if ub.session.query(ub.User).filter(ub.User.id == target_id).first() is None:
        return None, _err("not_found", "User not found", 404)
    return target_id, None


def _is_localhost():
    # Let Werkzeug split the authority. Hand-removing a trailing ``:port``
    # mistakes the colons in a bare IPv6 literal for a port separator.
    host = (urlsplit(request.host_url).hostname or "").lower()
    return (host.startswith("127.") or host == "localhost"
            or host.startswith("::ffff:7f") or host == "::1")


def _payload(user_id, auth_token):
    sync_url = None
    if auth_token is not None:
        # This is the same endpoint rendered by generate_kobo_auth_url.html.
        sync_url = url_for(
            "kobo.TopLevelEndpoint",
            auth_token=auth_token.auth_token,
            _external=True,
        )
    return {
        "user_id": user_id,
        "configured": auth_token is not None,
        "sync_url": sync_url,
        # The bundled KOReader plugin appends /kosync itself. Supplying the
        # tokenized Kobo endpoint there would produce a broken nested URL.
        "server_url": request.url_root.rstrip("/"),
        "is_localhost": _is_localhost(),
    }


def _require_kobo_enabled():
    if not getattr(config, "config_kobo_sync", False):
        return _err("kobo_sync_disabled", "Kobo sync is not enabled", 409)
    return None


def _json_payload(user_id, auth_token):
    response = jsonify(_payload(user_id, auth_token))
    # A Kobo token is a credential even though it is embedded in a URL. Never
    # let a browser or shared reverse proxy retain this response.
    response.headers["Cache-Control"] = "private, no-store"
    return response


@api_v1.route("/account/kobo-sync-token", methods=["GET"])
@api_v1.route("/admin/users/<int:user_id>/kobo-sync-token", methods=["GET"])
def get_kobo_sync_token(user_id=None):
    target_id, error = _target_user_id(user_id)
    if error:
        return error
    return _json_payload(target_id, find_auth_token(target_id))


@api_v1.route("/account/kobo-sync-token", methods=["POST"])
@api_v1.route("/admin/users/<int:user_id>/kobo-sync-token", methods=["POST"])
def create_kobo_sync_token(user_id=None):
    target_id, error = _target_user_id(user_id)
    if error:
        return error
    disabled = _require_kobo_enabled()
    if disabled:
        return disabled
    auth_token, created, committed = create_or_view_auth_token(target_id)
    if not committed:
        return _err("db_error", "Could not create the Kobo sync URL", 500)
    return _json_payload(target_id, auth_token), 201 if created else 200


@api_v1.route("/account/kobo-sync-token", methods=["DELETE"])
@api_v1.route("/admin/users/<int:user_id>/kobo-sync-token", methods=["DELETE"])
def delete_kobo_sync_token(user_id=None):
    target_id, error = _target_user_id(user_id)
    if error:
        return error
    revoke_auth_token(target_id)
    if not ub.session_commit():
        return _err("db_error", "Could not delete the Kobo sync URL", 500)
    return "", 204
