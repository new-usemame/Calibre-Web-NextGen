# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Admin API for the value map and its library clean-up (#2438, Discussion #2481).

GET    /api/v1/admin/value-maps/<field>          rules, library values, live preview, history
PUT    /api/v1/admin/value-maps/<field>          replace the rules (also the import)
POST   /api/v1/admin/value-maps/<field>/preview  store the live preview as a change set
GET    /api/v1/admin/change-sets/<id>            one change set with its rows
POST   /api/v1/admin/change-sets/<id>/apply      apply it, per-book results
POST   /api/v1/admin/change-sets/<id>/undo       reverse an applied set
"""
from datetime import datetime, timezone

from flask import jsonify, request

from . import api_v1
from .. import calibre_db, db, helper, ub
from ..cw_login import current_user
from ..cwa_db_loader import load_cwa_db
from ..services import change_sets as cs
from ..services.fields import FIELD_SPECS
from ..services import value_map as vm
from ..services.calibre_db_lock import metadata_db_write_lock
from ..usermanagement import login_required_if_no_ano

HISTORY_LIMIT = 10


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _guard(field=None):
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_admin():
        return _err("forbidden", "Admin access required", 403)
    if field is not None and field not in FIELD_SPECS:
        return _err("not_found", "Unknown field", 404)
    return None


def _files_rewritten():
    """Whether queued changes reach the book files (#1374): enforcement on."""
    try:
        return bool(load_cwa_db().CWA_DB().get_cwa_settings().get("auto_metadata_enforcement", False))
    except Exception:
        return False


def _rules(field):
    rows = (ub.session.query(ub.MetadataValueRule)
            .filter(ub.MetadataValueRule.field == field)
            .order_by(ub.MetadataValueRule.incoming_key).all())
    return [{"incoming": r.incoming, "targets": list(r.targets or [])} for r in rows]


def _live_preview(field, value_map):
    out = []
    for change in vm.plan(calibre_db.session, value_map, field):
        book = calibre_db.session.get(db.Books, change["book_id"])
        out.append({"book_id": change["book_id"], "field": field, "title": book.title,
                    "authors": [a.name for a in book.authors],
                    "before": change["old"], "after": change["new"]})
    return out


def _history(field):
    sets = (ub.session.query(ub.MetadataChangeSet)
            .filter(ub.MetadataChangeSet.source == "cleanup",
                    ub.MetadataChangeSet.status != "proposed",
                    ub.MetadataChangeSet.id.in_(
                        ub.session.query(ub.MetadataChange.set_id)
                        .filter(ub.MetadataChange.field == field)))
            .order_by(ub.MetadataChangeSet.id.desc()).limit(HISTORY_LIMIT).all())
    out = []
    for change_set in sets:
        summary = cs.summary(ub.session, calibre_db.session, change_set)
        summary.pop("changes")
        out.append(summary)
    return out


def _state(field):
    value_map = vm.load_map(ub.session, field)
    values = vm.library_values(calibre_db.session, field)
    for item in values:
        item["covered"] = value_map.covers(item["name"])
    return {
        "field": field,
        "multi": FIELD_SPECS[field].multi,
        "rules": _rules(field),
        "values": values,
        "changes": _live_preview(field, value_map),
        "history": _history(field),
        "files_rewritten": _files_rewritten(),
    }


@api_v1.route("/admin/value-maps/<field>", methods=["GET"])
@login_required_if_no_ano
def value_map_state(field):
    guard = _guard(field)
    if guard is not None:
        return guard
    return jsonify(_state(field))


@api_v1.route("/admin/value-maps/<field>", methods=["PUT"])
@login_required_if_no_ano
def value_map_replace(field):
    guard = _guard(field)
    if guard is not None:
        return guard
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _err("invalid_request", "Expected a JSON object", 400)
    # An export is {"rules": [...]}; the homelab tag_map.json shape is {"map": {...}}.
    raw = payload.get("rules", payload.get("map"))
    if isinstance(raw, dict):
        raw = {k: v for k, v in raw.items() if not str(k).startswith("_")}
    rules, errors = vm.clean_rules(field, raw)
    if errors:
        return jsonify({"error": {"code": "invalid_rules", "message": "Some rules are not valid",
                                  "details": errors}}), 400
    now = datetime.now(timezone.utc)
    try:
        (ub.session.query(ub.MetadataValueRule)
         .filter(ub.MetadataValueRule.field == field).delete())
        for rule in rules:
            ub.session.add(ub.MetadataValueRule(
                field=field, incoming=rule["incoming"], incoming_key=rule["incoming_key"],
                targets=rule["targets"], updated_by=current_user.id, updated_at=now))
        ub.session.commit()
    except Exception:
        ub.session.rollback()
        raise
    return jsonify(_state(field))


@api_v1.route("/admin/value-maps/<field>/preview", methods=["POST"])
@login_required_if_no_ano
def value_map_preview(field):
    guard = _guard(field)
    if guard is not None:
        return guard
    value_map = vm.load_map(ub.session, field)
    changes = vm.plan(calibre_db.session, value_map, field)
    if not changes:
        return _err("nothing_to_change", "No book would change with the saved rules", 409)
    change_set = cs.create(ub.session, "cleanup", current_user.id, changes)
    return jsonify(cs.summary(ub.session, calibre_db.session, change_set))


def _load_set(set_id):
    change_set = ub.session.get(ub.MetadataChangeSet, set_id)
    if change_set is None or change_set.source != "cleanup":
        return None
    return change_set


@api_v1.route("/admin/change-sets/<int:set_id>", methods=["GET"])
@login_required_if_no_ano
def change_set_detail(set_id):
    guard = _guard()
    if guard is not None:
        return guard
    change_set = _load_set(set_id)
    if change_set is None:
        return _err("not_found", "Change set not found", 404)
    return jsonify(cs.summary(ub.session, calibre_db.session, change_set))


def _run(set_id, action):
    guard = _guard()
    if guard is not None:
        return guard
    change_set = _load_set(set_id)
    if change_set is None:
        return _err("not_found", "Change set not found", 404)
    payload = request.get_json(silent=True) or {}
    exclude = payload.get("exclude_book_ids", [])
    if not isinstance(exclude, list) or not all(isinstance(i, int) for i in exclude):
        return _err("invalid_request", "exclude_book_ids must be a list of book ids", 400)
    hooks = dict(mark_modified=lambda book: helper.mark_book_modified(book, set_dirty=True),
                 queue_embed=helper.log_metadata_change)
    try:
        with metadata_db_write_lock():
            if action == "apply":
                result = cs.apply(ub.session, calibre_db.session, set_id, exclude=exclude, **hooks)
            else:
                result = cs.undo(ub.session, calibre_db.session, set_id, **hooks)
    except ValueError as error:
        return _err("conflict", str(error), 409)
    first = ub.session.query(ub.MetadataChange).filter(ub.MetadataChange.set_id == set_id).first()
    field = first.field if first is not None else "tags"
    return jsonify({
        **result,
        "files_rewritten": _files_rewritten(),
        "set": cs.summary(ub.session, calibre_db.session, change_set),
        "state": _state(field),
    })


@api_v1.route("/admin/change-sets/<int:set_id>/apply", methods=["POST"])
@login_required_if_no_ano
def change_set_apply(set_id):
    return _run(set_id, "apply")


@api_v1.route("/admin/change-sets/<int:set_id>/undo", methods=["POST"])
@login_required_if_no_ano
def change_set_undo(set_id):
    return _run(set_id, "undo")
