# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Admin settings API for folder-derived ingest labels (#1495)."""

from flask import jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from . import api_v1
from .. import calibre_db, db, logger
from ..cw_login import current_user
from ..services.ingest_folder_labels import (
    NESTED_SETTING,
    TARGET_DISABLED,
    TARGET_SETTING,
    IngestFolderLabelError,
    eligible_custom_column_options,
    validate_target,
)
from ..usermanagement import login_required_if_no_ano

log = logger.create()


def _json(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Authorization"
    return response


def _error(code, message, status):
    return _json({"error": {"code": code, "message": message}}, status)


def _require_admin():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _error("unauthorized", "You must be signed in", 401)
    if not current_user.role_admin():
        return _error("forbidden", "Admin access required", 403)
    return None


def _custom_columns():
    rows = calibre_db.session.query(db.CustomColumns).order_by(
        db.CustomColumns.label.asc(), db.CustomColumns.id.asc(),
    ).all()
    return eligible_custom_column_options(rows)


def _settings_db():
    from ..cwa_db_loader import load_cwa_db
    return load_cwa_db().CWA_DB()


def _close_settings_db(cwa_db):
    try:
        cwa_db.con.close()
    except Exception:
        pass


def _payload(settings, columns):
    target = settings.get(TARGET_SETTING, TARGET_DISABLED)
    try:
        validate_target(target, columns)
        configuration_error = None
    except IngestFolderLabelError as exc:
        configuration_error = str(exc)
    return {
        "target": target,
        "nested": bool(settings.get(NESTED_SETTING, False)),
        "custom_columns": columns,
        "configuration_error": configuration_error,
    }


@api_v1.route("/admin/ingest-folder-label-settings", methods=["GET"])
@login_required_if_no_ano
def ingest_folder_label_settings():
    guard = _require_admin()
    if guard is not None:
        return guard
    try:
        columns = _custom_columns()
        cwa_db = _settings_db()
        try:
            settings = cwa_db.get_cwa_settings()
        finally:
            _close_settings_db(cwa_db)
    except Exception:
        log.exception("Could not load ingest-folder label settings")
        return _error("settings_unavailable", "Could not load ingest-folder settings", 500)
    return _json(_payload(settings, columns))


@api_v1.route("/admin/ingest-folder-label-settings", methods=["PUT"])
@login_required_if_no_ano
def update_ingest_folder_label_settings():
    guard = _require_admin()
    if guard is not None:
        return guard
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error("invalid_request", "Settings body must be a JSON object", 400)
    if set(body) - {"target", "nested"}:
        return _error("invalid_request", "Only target and nested are supported", 400)
    if "nested" in body and type(body["nested"]) is not bool:
        return _error("invalid_request", "nested must be a boolean", 400)

    try:
        columns = _custom_columns()
        cwa_db = _settings_db()
        try:
            # Serialize read/validate/write so concurrent partial saves cannot
            # restore a stale value for the omitted half of the pair.
            cwa_db.con.execute("BEGIN IMMEDIATE")
            settings = cwa_db.get_cwa_settings()
            target = body.get("target", settings.get(TARGET_SETTING, TARGET_DISABLED))
            nested = body.get("nested", bool(settings.get(NESTED_SETTING, False)))
            target = validate_target(target, columns)
            # One parameterized row update keeps the paired values in one
            # cwa.db transaction while omitted request keys keep their values.
            cwa_db.cur.execute(
                f"UPDATE cwa_settings SET {TARGET_SETTING}=?, {NESTED_SETTING}=?",
                (target, int(nested)),
            )
            cwa_db.con.commit()
            saved = cwa_db.get_cwa_settings()
        finally:
            _close_settings_db(cwa_db)
    except IngestFolderLabelError as exc:
        return _error("invalid_folder_label_target", str(exc), 400)
    except (SQLAlchemyError, OSError, RuntimeError):
        log.exception("Could not save ingest-folder label settings")
        return _error("settings_save_failed", "Could not save ingest-folder settings", 500)
    except Exception:
        log.exception("Could not save ingest-folder label settings")
        return _error("settings_save_failed", "Could not save ingest-folder settings", 500)
    return _json(_payload(saved, columns))
