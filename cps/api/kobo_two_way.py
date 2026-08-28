# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage 0 SPA API for Kobo two-way annotation sync preferences.

This module is deliberately a *preference* surface over a feature that is
still switched off. It reads and writes the user's opt-in
(``user.kobo_two_way_annotation_sync``), the user's scope
(``user.kobo_two_way_annotation_scope``: 'all' or 'selected'), and per-book
opt-out state (``KoboAnnotationBookState.authority_status='disabled'``). It
also READS observed per-book authority/opaque state so the SPA can show what
enabling will actually do.

It must never do more than that:

* it does NOT call ``gates_allow`` or anything in
  ``cps/services/annotation_sync`` / ``cps/readingservices.py``;
* it never names a book in ``checkforchanges`` and never serves an
  annotation GET;
* it never writes ``authority_status`` to anything but 'disabled' (user
  opt-out) or back to 'unseeded' (user re-enables; the book must re-seed and
  re-prove itself before any future sync — re-enabling never restores a
  previous authority);
* both feature gates keep their defaults; nothing here makes a book sync.

Scope semantics (see notes/KOBO-TWO-WAY-ANNOTATION-SYNC-DESIGN.md §9-10):

* 'all' (default) — every book syncs as it becomes ready, unless the user
  opted that book out ('disabled').
* 'selected' — only individually picked books sync. Entering 'selected'
  bulk-marks the user's 'unseeded' rows 'disabled' so nothing is picked
  implicitly: a legacy backfill row must never read as an explicit pick.
  Switching back to 'all' does not resurrect disabled books (an opt-out is
  user data, not ours to undo); each book can be re-enabled individually.
"""
from sqlalchemy.exc import SQLAlchemyError

from flask import jsonify, request

from . import api_v1
from .. import calibre_db, config, db, logger, ub
from ..cw_login import current_user
from ..services.kobo_annotation_stage0 import emergency_override_disables

log = logger.create()

SCOPE_ALL = "all"
SCOPE_SELECTED = "selected"
_SCOPES = frozenset({SCOPE_ALL, SCOPE_SELECTED})

# States the user may leave/enter via the per-book toggle. 'seeding',
# 'authoritative' and 'quarantined' are pipeline evidence, not preferences —
# a toggle must never silently erase them, so those books are shown with
# their real state and no switch (can_toggle=False).
_TOGGLEABLE_STATES = frozenset({"unseeded", "disabled"})


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _require_real_user():
    """Preference endpoints are for a concretely logged-in user — never the
    anonymous-browse guest (mirrors cps/api/account.py)."""
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    return None


def _book_titles(book_ids):
    """{book_id: title} from the calibre library; empty map when unavailable.

    Titles are cosmetic — an unreadable library DB must not take the
    preference surface down with a 500 (same degrade rule as
    cps/api/books.py::_detail_custom_columns).
    """
    if not book_ids:
        return {}
    try:
        rows = (calibre_db.session.query(db.Books.id, db.Books.title)
                .filter(db.Books.id.in_(book_ids))
                .filter(calibre_db.common_filters(allow_show_archived=True)).all())
        return {row.id: row.title for row in rows}
    except (SQLAlchemyError, AttributeError, KeyError, TypeError):
        log.warning("Book titles unavailable for Kobo two-way state list",
                    exc_info=True)
        return {}


def _user_book_states():
    return (ub.session.query(ub.KoboAnnotationBookState)
            .filter(ub.KoboAnnotationBookState.user_id == current_user.id)
            .order_by(ub.KoboAnnotationBookState.book_id)
            .all())


def _serialize_book(row, titles):
    return {
        "book_id": row.book_id,
        # None when the book left the library or the title lookup failed —
        # the SPA renders a neutral fallback, never a raw id alone.
        "title": titles.get(row.book_id),
        "authority_status": row.authority_status,
        "opaque_content_status": row.opaque_content_status,
        "quarantine_reason": row.quarantine_reason,
        "seeded_at": row.seeded_at.isoformat() if row.seeded_at else None,
        "enabled": row.authority_status != "disabled",
        "can_toggle": row.authority_status in _TOGGLEABLE_STATES,
    }


def _serialize_settings():
    rows = _user_book_states()
    titles = _book_titles([row.book_id for row in rows])
    scope = getattr(current_user, "kobo_two_way_annotation_scope", None) or SCOPE_ALL
    return {
        # Read-only context: the instance gate is admin-owned (classic
        # config page), and the env override can only ever force off.
        "instance_enabled": bool(getattr(config, "config_kobo_two_way_annotation_sync", False)),
        "emergency_disabled": emergency_override_disables(),
        "kobo_available": bool(getattr(config, "config_kobo_sync", 0)),
        "enabled": bool(current_user.kobo_two_way_annotation_sync),
        "scope": scope if scope in _SCOPES else SCOPE_ALL,
        "books": [_serialize_book(row, titles) for row in rows],
    }


@api_v1.route("/account/kobo-two-way-annotations")
def get_kobo_two_way_annotations():
    guard = _require_real_user()
    if guard:
        return guard
    return jsonify(_serialize_settings())


@api_v1.route("/account/kobo-two-way-annotations", methods=["POST"])
def update_kobo_two_way_annotations():
    """Write the opt-in and/or scope. Both keys optional; unknown keys ignored."""
    guard = _require_real_user()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}

    if "scope" in data and data["scope"] not in _SCOPES:
        return _err("invalid_request", "Invalid scope option", 400)

    try:
        if "enabled" in data:
            current_user.kobo_two_way_annotation_sync = bool(data["enabled"])
        if "scope" in data:
            new_scope = data["scope"]
            old_scope = getattr(current_user, "kobo_two_way_annotation_scope", None) or SCOPE_ALL
            if new_scope == SCOPE_SELECTED and old_scope != SCOPE_SELECTED:
                # Default-deny: entering 'selected' means "I will pick". Mark
                # every not-yet-proven book opted out so no legacy/backfill
                # row silently reads as an explicit pick. Only 'unseeded'
                # rows move; pipeline states ('seeding', 'authoritative',
                # 'quarantined') are evidence and stay untouched.
                paused = (ub.session.query(ub.KoboAnnotationBookState)
                          .filter(ub.KoboAnnotationBookState.user_id == current_user.id,
                                  ub.KoboAnnotationBookState.authority_status == "unseeded")
                          .all())
                for row in paused:
                    row.authority_status = "disabled"
                if paused:
                    log.info(
                        "[kobo-two-way-stage0] user %s switched to 'selected' scope; "
                        "%d unseeded book(s) marked disabled (opted out until picked)",
                        current_user.id, len(paused),
                    )
            current_user.kobo_two_way_annotation_scope = new_scope
    except Exception:
        # Unlike account.py's validators, nothing in this block raises with a
        # user-facing message — a failure here is ORM/DB internals. Log it and
        # answer a generic 500; a 400 would mislabel a server fault as bad
        # input, and the exception text would leak schema details.
        ub.session.rollback()
        log.exception("Could not save Kobo two-way settings")
        return _err("db_error", "Could not save Kobo two-way settings", 500)

    try:
        ub.session.commit()
    except Exception:
        ub.session.rollback()
        log.exception("Could not save Kobo two-way settings")
        return _err("db_error", "Could not save Kobo two-way settings", 500)

    return jsonify(_serialize_settings())


@api_v1.route("/account/kobo-two-way-annotations/books", methods=["POST"])
def update_kobo_two_way_book():
    """Opt one book out ('disabled') or back in ('unseeded').

    Only the two toggleable states are reachable here. Re-enabling always
    returns a book to 'unseeded' — it must re-seed and re-prove completeness
    before any later stage may sync it; a toggle never restores authority.
    """
    guard = _require_real_user()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}

    book_id = data.get("book_id")
    if not isinstance(book_id, int) or isinstance(book_id, bool) or book_id < 1:
        return _err("invalid_request", "book_id must be a positive integer", 400)
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return _err("invalid_request", "enabled must be a boolean", 400)

    row = (ub.session.query(ub.KoboAnnotationBookState)
           .filter(ub.KoboAnnotationBookState.user_id == current_user.id,
                   ub.KoboAnnotationBookState.book_id == book_id)
           .first())
    if row is None:
        # No pipeline row yet — the book has no observed state to change.
        # Books enter the pipeline via annotation history or a later stage's
        # discovery; the SPA must not invent state for them.
        return _err("not_found", "This book has no Kobo two-way state yet", 404)

    if row.authority_status not in _TOGGLEABLE_STATES:
        return _err(
            "conflict",
            "This book is %s and cannot be toggled; its state is sync evidence, "
            "not a preference" % row.authority_status, 409)

    if enabled:
        row.authority_status = "unseeded"
        # A stale reason from a life before the opt-out must not follow the
        # book back in; re-seeding re-evaluates everything from scratch.
        row.quarantine_reason = None
    else:
        row.authority_status = "disabled"

    try:
        ub.session.commit()
    except Exception:
        ub.session.rollback()
        log.exception("Could not save Kobo two-way book state")
        return _err("db_error", "Could not save book state", 500)

    titles = _book_titles([row.book_id])
    return jsonify({"book": _serialize_book(row, titles)})
