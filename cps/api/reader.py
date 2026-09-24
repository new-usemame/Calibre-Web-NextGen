# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader progress (bookmark) endpoints for /api/v1.

Reads/writes the SAME ub.Bookmark row the legacy reader uses
(/ajax/bookmark/<id>/<format>), with the SAME lowercase format key — so reading
progress is shared between the legacy reader and the SPA reader: open a book in
one, resume in the other. The bookmark_key is the epub.js CFI string.
"""
from datetime import datetime, timezone
from pathlib import Path

from flask import g, jsonify, request
from sqlalchemy import and_
from sqlalchemy.orm.attributes import flag_modified

from . import api_v1
from .. import calibre_db, config, logger, ub
from ..cw_login import current_user
from ..services import reading_position, reading_sources, storyteller_source
from ..services.browser_source import BROWSER_ALIAS
from ..usermanagement import login_required_if_no_ano
from ..reader_settings import merged_reader_settings, resolved_reader_settings

log = logger.create()


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _require_real_user():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    return None


def _can_browse_global():
    """Mirror the book-detail role gate without weakening content filters."""
    try:
        return bool(current_user.role_browse_global())
    except (AttributeError, RuntimeError):
        return False


def _bookmark_filter(book_id, fmt):
    return and_(
        ub.Bookmark.user_id == int(current_user.id),
        ub.Bookmark.book_id == book_id,
        ub.Bookmark.format == fmt,
    )


@api_v1.route("/books/<int:book_id>/bookmark")
@login_required_if_no_ano
def get_bookmark(book_id):
    """Return the saved reading position (epub.js CFI) for this user/book/format."""
    guard = _require_real_user()
    if guard:
        return guard
    fmt = (request.args.get("format") or "epub").lower()
    return jsonify(reading_position.read_resume_position(
        ub.session.get_bind(), int(current_user.id), book_id, fmt,
    ))


@api_v1.route("/books/<int:book_id>/bookmark", methods=["POST"])
@login_required_if_no_ano
def save_bookmark(book_id):
    """Persist (or, with an empty bookmark, clear) the reading position. Mirrors
    the legacy set_bookmark write so the two readers share one row."""
    guard = _require_real_user()
    if guard:
        return guard
    try:
        from ..services.device_registry import (
            WEBREADER_INSTALLATION_ID_HEADER,
            ensure_webreader_device_best_effort,
        )
        g.annotation_origin_device_id = ensure_webreader_device_best_effort(
            user_id=current_user.id,
            installation_id=request.headers.get(WEBREADER_INSTALLATION_ID_HEADER),
        )
    except Exception:
        log.warning("Best-effort web-reader device observation failed", exc_info=True)
        g.annotation_origin_device_id = None
    data = request.get_json(silent=True) or {}
    fmt = (data.get("format") or "epub").lower()
    bookmark_key = data.get("bookmark") or ""

    # Replace-on-write: one bookmark per (user, book, format), like the legacy route.
    ub.session.query(ub.Bookmark).filter(_bookmark_filter(book_id, fmt)).delete()
    if bookmark_key:
        row = ub.session.merge(ub.Bookmark(
            user_id=current_user.id,
            book_id=book_id,
            format=fmt,
            bookmark_key=bookmark_key,
        ))
        # #1318: settle the required write before the optional one, so a bookmark
        # failure is not reported in the vocabulary of a progress-sharing failure
        # (and so the savepoint below cannot roll the bookmark back with it).
        if not ub.session_flush():
            return "", 500

        # #324: share the portable half of the position (the percentage) with the
        # user's other devices. Mirrors the legacy route so both readers behave
        # the same. Only on a save — an empty bookmark is a clear.
        percentage = reading_position.coerce_percentage(data.get("percentage"))
        if percentage is not None:
            try:
                reading_position.record_web_reader_progress(
                    current_user,
                    book_id,
                    percentage,
                    origin_device_id=g.annotation_origin_device_id,
                    cfi=bookmark_key,
                    # A named-source preview is a read-only inspection. When
                    # the reader chooses to continue there, create/update only
                    # the Browser source rather than rewriting device carriers.
                    share_with_devices=data.get("share_with_devices", True) is not False,
                )
            except Exception as e:
                # Position sharing must never cost the user their bookmark.
                log.warning("Could not share web reader progress for book %s: %s", book_id, e)

        # Stamp after sharing: our own mirror must never supersede this CFI.
        row.updated_at = datetime.now(timezone.utc)

    # The SPA debounces one of these every 800ms; answering 204 on a rolled-back
    # write drops the position silently and tells the client not to retry.
    if not ub.session_commit("Bookmark for user {} in book {} via api".format(current_user.id, book_id)):
        return "", 500
    return "", 204


def _book_epub_path(book):
    """Return the visible book's contained EPUB path, or ``None``."""
    data_rows = getattr(book, "data", None) or ()
    if not any(str(data.format).lower() == "epub" for data in data_rows):
        return None
    root = Path(config.get_book_path()).resolve()
    for data in data_rows:
        if str(data.format).lower() != "epub":
            continue
        path = (root / book.path / (data.name + ".epub")).resolve()
        if path.is_relative_to(root) and path.is_file():
            return path
    return None


@api_v1.route("/books/<int:book_id>/reading-sources")
@login_required_if_no_ano
def get_reading_sources(book_id):
    """Return selectable, attributed positions for one visible EPUB.

    Device rows are last reports. The resolved Kobo bookmark is intentionally a
    separate source because its storage table cannot prove which device caused
    the winning value. External connectors are read-only and best effort.
    """
    guard = _require_real_user()
    if guard:
        return guard
    # Match the authorized reader/book-detail surface: hidden and archived are
    # listing states, while a curator may deep-link into the global catalogue.
    # A book on a public shelf opens in the reader without membership, so its
    # reader gets its own places here too; every row below is that user's own.
    # common_filters() still enforces language/content/role restrictions.
    book = calibre_db.get_filtered_book(
        book_id,
        allow_show_archived=True,
        allow_show_hidden=True,
        allow_show_global=_can_browse_global(),
        allow_public_shelf_books=True,
    )
    if book is None:
        return _err("not_found", "Book not found", 404)

    user_id = int(current_user.id)
    # A browser alias was folded into the account's one Browser source, which
    # already holds its latest position; listing it would offer a stale second
    # browser as a place to open.
    devices = (ub.session.query(ub.Device)
               .filter(ub.Device.user_id == user_id,
                       ub.Device.created_by != BROWSER_ALIAS)
               .order_by(ub.Device.active.desc(), ub.Device.id)
               .all())
    positions = (ub.session.query(ub.DeviceReadingPosition)
                 .filter(
                     ub.DeviceReadingPosition.device_id.in_([row.id for row in devices]),
                     ub.DeviceReadingPosition.book_id == book_id,
                 ).all()) if devices else []
    epub_path = _book_epub_path(book)
    sources = reading_sources.device_source_rows(
        devices, positions, book_id=book_id, epub_path=epub_path,
    )

    integration = {"configured": False, "reachable": None}
    client = storyteller_source.configured_client(user_id)
    if client is not None:
        integration["configured"] = True
        if epub_path is not None:
            try:
                source = storyteller_source.read_source(
                    client,
                    title=book.title,
                    authors=[author.name for author in getattr(book, "authors", ())],
                    epub_path=epub_path,
                )
                integration["reachable"] = True
                if source is not None:
                    sources.append(source)
            except Exception:
                integration["reachable"] = False
                log.warning(
                    "Could not read configured Storyteller position for user %s book %s",
                    user_id, book_id, exc_info=True,
                )

    state = (ub.session.query(ub.KoboReadingState)
             .filter_by(user_id=user_id, book_id=book_id).first())
    resolved = reading_sources.resolved_source_row(
        state.current_bookmark if state else None, book_id=book_id,
    )
    if resolved is not None:
        sources.append(resolved)
    return jsonify({
        "book_id": book_id,
        "sources": sources,
        "integrations": {"storyteller": integration},
    })


@api_v1.route("/reader/settings")
@login_required_if_no_ano
def get_reader_settings():
    """Return the complete per-user appearance contract shared by both readers."""
    guard = _require_real_user()
    if guard:
        return guard
    current = (getattr(current_user, "view_settings", None) or {}).get("reader", {})
    return jsonify({"reader": resolved_reader_settings(current)})


@api_v1.route("/reader/settings", methods=["POST"])
@login_required_if_no_ano
def save_reader_settings():
    """Merge a partial reader appearance update into User.view_settings."""
    guard = _require_real_user()
    if guard:
        return guard
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _err("invalid_settings", "Reader settings must be an object", 400)
    view_settings = dict(getattr(current_user, "view_settings", None) or {})
    merged = merged_reader_settings(view_settings.get("reader", {}), payload)
    view_settings["reader"] = merged
    current_user.view_settings = view_settings
    flag_modified(current_user, "view_settings")
    try:
        ub.session.commit()
    except Exception:
        ub.session.rollback()
        return _err("save_failed", "Could not save reader settings", 500)
    return jsonify({"reader": resolved_reader_settings(merged)})
