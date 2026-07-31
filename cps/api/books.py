# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Catalog endpoints for /api/v1."""
from datetime import timezone

from flask import jsonify, request
from flask_babel import get_locale
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.functions import coalesce

from . import api_v1
from .serializers import serialize_book_list_item, serialize_book_detail
from .. import calibre_db, config, db, ub, isoLanguages, logger
from ..cw_login import current_user
from ..helper import edit_book_read_status, book_is_in_progress, get_convert_options, \
    get_kosync_progress_display
from ..usermanagement import login_required_if_no_ano

log = logger.create()

# SQLite builds vary in their host-parameter ceiling. Stay below even the
# conservative historical limit when filtering app.db-derived download ids
# against the separate calibre metadata database.
_SQLITE_IN_CHUNK = 900


def _visible_ids_for_chunk(book_ids):
    query = calibre_db.generate_linked_query(config.config_read_column, db.Books)
    return {row[0] for row in query.filter(calibre_db.common_filters())
            .filter(db.Books.id.in_(book_ids))
            .with_entities(db.Books.id).all()}


def _visible_hot_book_ids(book_ids):
    """Return visible ids in hotness order without an unbounded SQL ``IN``."""
    visible = set()
    for start in range(0, len(book_ids), _SQLITE_IN_CHUNK):
        visible.update(_visible_ids_for_chunk(book_ids[start:start + _SQLITE_IN_CHUNK]))
    return [book_id for book_id in book_ids if book_id in visible]


def _detail_custom_columns():
    """Classic-parity display definitions, degrading safely if DB metadata is unavailable.

    ``SQLAlchemyError`` is the case that actually happens in production: the
    calibre metadata DB is reachable but its schema is not (a library that is
    still being written, a wrong/renamed library path, a mid-migration
    ``custom_columns``), and the query raises ``OperationalError``. The
    non-DB errors below cover the reconnect window, where ``calibre_db.session``
    can be absent rather than merely unreadable.

    Custom columns are supplementary to a book's detail payload, so an
    unreadable definition table must not take the whole page down with a 500.
    """
    try:
        return calibre_db.get_cc_columns(config, filter_config_custom_read=True)
    except (SQLAlchemyError, AttributeError, KeyError, TypeError):
        log.warning("Custom-column definitions unavailable for book detail", exc_info=True)
        return []


def _original_filename(book_id):
    """Best-effort app.db lookup. A rolling-upgrade request can arrive before
    the auxiliary table/session is ready; book detail must still render."""
    try:
        row = (ub.session.query(ub.BookOriginalFilename)
               .filter(ub.BookOriginalFilename.book_id == book_id).first())
        return row.filename if row else None
    except Exception:
        return None

# Stateless sort map — mirrors web.py sort options without calling get_sort_function
# (which writes per-user state and must not be called from a read-only API endpoint).
SORT_MAP = {
    "new": [db.Books.timestamp.desc()],
    "old": [db.Books.timestamp],
    "abc": [func.ng_sort_key(db.Books.sort), db.Books.sort, db.Books.id],
    "zyx": [func.ng_sort_key(db.Books.sort).desc(), db.Books.sort.desc(), db.Books.id.desc()],
    "pubnew": [db.Books.pubdate.desc()],
    "pubold": [db.Books.pubdate],
    "modifiednew": [db.Books.last_modified.desc()],
    "modifiedold": [db.Books.last_modified],
    "authaz": [func.ng_sort_key(db.Books.author_sort), db.Books.author_sort,
               func.ng_sort_key(db.Series.name), db.Series.name, db.Books.series_index],
    "authza": [func.ng_sort_key(db.Books.author_sort).desc(), db.Books.author_sort.desc(),
               func.ng_sort_key(db.Series.name).desc(), db.Series.name.desc(), db.Books.series_index.desc()],
    # Series reading order — mirrors web.py get_sort_function's seriesasc/seriesdesc.
    # Every list_books path already joins db.Series (series_join), so ordering by
    # db.Books.series_index needs no extra plumbing. Used by the new-UI series view
    # so a series reads 1, 2, 3… instead of newest-first (fork #573).
    "seriesasc": [db.Books.series_index.asc()],
    "seriesdesc": [db.Books.series_index.desc()],
}


def _real_user_id():
    """Return a concrete user id, tolerating stripped-decorator unit contexts."""
    try:
        if (not current_user.is_authenticated) or current_user.is_anonymous:
            return None
        return int(current_user.id)
    except (AttributeError, RuntimeError):
        return None


def _hidden_book_ids():
    """Current user's hidden ids, once per list request (never for Guest)."""
    user_id = _real_user_id()
    if user_id is None:
        return set()
    rows = (ub.session.query(ub.UserHiddenBook.book_id)
            .filter(ub.UserHiddenBook.user_id == user_id).all())
    return {int(row[0]) for row in rows}


def _archived_book_ids():
    """Current user's truly archived ids for the SPA recovery filter."""
    user_id = _real_user_id()
    if user_id is None:
        return set()
    rows = (ub.session.query(ub.ArchivedBook.book_id)
            .filter(ub.ArchivedBook.user_id == user_id)
            .filter(ub.ArchivedBook.is_archived.is_(True)).all())
    return {int(row[0]) for row in rows}


def _row_to_item(e, hidden_ids=None):
    """Unwrap a SQLAlchemy Row (Books, is_archived, read_status) or plain Books object."""
    book = getattr(e, "Books", e)
    if config.config_read_column:
        # Custom read column: generate_linked_query selects read_column.value as the
        # third column (Row attr "value"), NOT ub.ReadBook.read_status — a truthy
        # value means the book is read. Without this the badge is always false when
        # an admin links read status to a Calibre column (fork #579).
        read = bool(getattr(e, "value", None))
    else:
        read = getattr(e, "read_status", None) == ub.ReadBook.STATUS_FINISHED
    archived = bool(getattr(e, "is_archived", False))
    return serialize_book_list_item(
        book,
        read=read,
        archived=archived,
        hidden=book.id in (hidden_ids or set()),
    )


def _build_entity_filter(author, series, tag, publisher, language, rating=None, book_format=None):
    """Build an entity db_filter from query params; returns True (no-op) if none supplied.

    Only the first supplied entity param is honoured — multiple entity filters
    are AND-ed via a chain of .any() but the API only supports one at a time in
    practice. If multiple are supplied they are AND-ed together.
    """
    parts = []
    if author is not None:
        parts.append(db.Books.authors.any(db.Authors.id == author))
    if series is not None:
        parts.append(db.Books.series.any(db.Series.id == series))
    if tag is not None:
        parts.append(db.Books.tags.any(db.Tags.id == tag))
    if publisher is not None:
        parts.append(db.Books.publishers.any(db.Publishers.id == publisher))
    if rating is not None:
        parts.append(db.Books.ratings.any(db.Ratings.id == rating))
    if book_format:
        parts.append(db.Books.data.any(db.Data.format == book_format.upper()))
    if language is not None:
        # "none" is the synthetic category speaking_language() appends for books
        # with no language link — match books that have no Languages rows, not a
        # (non-existent) lang_code == "none".
        if language == "none":
            parts.append(~db.Books.languages.any())
        else:
            parts.append(db.Books.languages.any(db.Languages.lang_code == language))
    if not parts:
        return True
    if len(parts) == 1:
        return parts[0]
    return and_(*parts)


def _build_read_filter(filter_val):
    """Return a db_filter for ?filter=read|unread.

    When an admin links read status to a Calibre column (config_read_column),
    filter on that column's value (mirrors web.py's books_list); otherwise use the
    built-in per-user ub.ReadBook table. The join for the custom column is provided
    by generate_linked_query inside fill_indexpage, so the value is queryable here.
    """
    if config.config_read_column:
        try:
            read_col = db.cc_classes[config.config_read_column].value
        except (KeyError, AttributeError):
            log.error("Custom Column No.%s does not exist in calibre database",
                      config.config_read_column)
            return True
        if filter_val == "read":
            return coalesce(read_col, False) == True   # noqa: E712
        if filter_val == "unread":
            return coalesce(read_col, False) != True   # noqa: E712
        return True
    if filter_val == "read":
        return and_(
            ub.ReadBook.user_id == int(current_user.id),
            ub.ReadBook.read_status == ub.ReadBook.STATUS_FINISHED,
        )
    if filter_val == "unread":
        return coalesce(ub.ReadBook.read_status, 0) != ub.ReadBook.STATUS_FINISHED
    return True


@api_v1.route("/books")
@login_required_if_no_ano
def list_books():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", config.config_books_per_page, type=int)
    sort = request.args.get("sort", "new")
    order = SORT_MAP.get(sort, SORT_MAP["new"])
    search = request.args.get("search")
    show_hidden = (request.args.get("show_hidden", "").strip().lower()
                   in ("1", "true", "yes", "on"))
    # Anonymous browse has no per-user hidden state. Treat a forged query as the
    # ordinary list and keep the response/action surface coherent with the 401
    # mutation guard.
    show_hidden = bool(show_hidden and _real_user_id() is not None)
    hidden_ids = _hidden_book_ids() if show_hidden else set()
    to_items = lambda entries: [_row_to_item(e, hidden_ids) for e in entries]

    if search:
        offset = (page - 1) * per_page
        join = (
            db.books_series_link,
            db.Books.id == db.books_series_link.c.book,
            db.Series,
        )
        entries, total, _pagination = calibre_db.get_search_results(
            search, config, offset, [order], per_page, *join,
            allow_show_hidden=show_hidden,
        )
        return jsonify({
            "items": to_items(entries),
            "page": page,
            "per_page": per_page,
            "total": total,
        })

    # Entity filters
    author_id = request.args.get("author", type=int)
    series_id = request.args.get("series", type=int)
    tag_id = request.args.get("tag", type=int)
    publisher_id = request.args.get("publisher", type=int)
    language_code = request.args.get("language")
    filter_val = request.args.get("filter")  # read | unread | archived

    # --- archived path (two-step: collect ids, then fill_indexpage_with_archived_books) ---
    if filter_val == "archived":
        archived_books = (ub.session.query(ub.ArchivedBook)
                         .filter(ub.ArchivedBook.user_id == int(current_user.id))
                         .filter(ub.ArchivedBook.is_archived == True)  # noqa: E712
                         .all())
        archived_ids = [ab.book_id for ab in archived_books]
        archived_filter = db.Books.id.in_(archived_ids)
        series_join = (db.books_series_link, db.Books.id == db.books_series_link.c.book, db.Series)
        entries, _random, pagination = calibre_db.fill_indexpage_with_archived_books(
            page, db.Books, per_page, archived_filter, order,
            True, True, config.config_read_column, *series_join,
        )
        return jsonify({
            "items": to_items(entries),
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total_count,
        })

    series_join = (db.books_series_link, db.Books.id == db.books_series_link.c.book, db.Series)

    # --- discovery views (mirror web.py books_list categories) ---
    if filter_val == "favorites":
        fav_ids = [f.book_id for f in (ub.session.query(ub.FavoriteBook)
                   .filter(ub.FavoriteBook.user_id == int(current_user.id)).all())]
        entries, _random, pagination = calibre_db.fill_indexpage(
            page, per_page, db.Books, db.Books.id.in_(fav_ids), order,
            True, config.config_read_column, *series_join)
        return jsonify({"items": to_items(entries),
                        "page": pagination.page, "per_page": pagination.per_page,
                        "total": pagination.total_count})

    if filter_val == "rated":
        # Top-rated: Calibre stores rating 0–10 (half-stars); >9 == 5 stars.
        rated_filter = db.Books.ratings.any(db.Ratings.rating > 9)
        entries, _random, pagination = calibre_db.fill_indexpage(
            page, per_page, db.Books, rated_filter, order,
            True, config.config_read_column, *series_join)
        return jsonify({"items": to_items(entries),
                        "page": pagination.page, "per_page": pagination.per_page,
                        "total": pagination.total_count})

    if filter_val == "discover":
        # Random unread books (single page, like the legacy Discover view).
        if not config.config_read_column:
            disc_filter = coalesce(ub.ReadBook.read_status, 0) != ub.ReadBook.STATUS_FINISHED
        else:
            try:
                disc_filter = coalesce(db.cc_classes[config.config_read_column].value, False) != True  # noqa: E712
            except (KeyError, AttributeError):
                disc_filter = True
        entries, _random, _pg = calibre_db.fill_indexpage(
            1, per_page, db.Books, disc_filter, [func.randomblob(2)],
            True, config.config_read_column)
        items = to_items(entries)
        return jsonify({"items": items, "page": 1, "per_page": per_page, "total": len(items)})

    if filter_val == "hot":
        # Most-downloaded, paginated by the downloads table (mirrors render_hot_books).
        off = per_page * (page - 1)
        all_hot_ids = [row[0] for row in (ub.session.query(ub.Downloads.book_id)
                   .group_by(ub.Downloads.book_id)
                   .order_by(func.count(ub.Downloads.book_id).desc()))]
        # Filter before paginating: otherwise a hidden/restricted book leaves a
        # short page while the header still counts it.
        visible_hot_ids = _visible_hot_book_ids(all_hot_ids)
        total = len(visible_hot_ids)
        hot_ids = visible_hot_ids[off:off + per_page]
        entries = []
        if hot_ids:
            q = calibre_db.generate_linked_query(config.config_read_column, db.Books)
            rows = q.filter(calibre_db.common_filters()).filter(db.Books.id.in_(hot_ids)).all()
            book_map = {r.Books.id: r for r in rows}
            entries = [book_map[i] for i in hot_ids if i in book_map]  # preserve hotness order
        return jsonify({"items": to_items(entries),
                        "page": page, "per_page": per_page, "total": total})

    # --- entity + read/unread path ---
    rating_id = request.args.get("rating", type=int)
    book_format = request.args.get("format")
    entity_filter = _build_entity_filter(author_id, series_id, tag_id, publisher_id, language_code,
                                         rating=rating_id, book_format=book_format)
    read_filter = _build_read_filter(filter_val) if filter_val in ("read", "unread") else True

    if entity_filter is True and read_filter is True:
        db_filter = True
    elif entity_filter is True:
        db_filter = read_filter
    elif read_filter is True:
        db_filter = entity_filter
    else:
        db_filter = and_(entity_filter, read_filter)

    # series_join is needed when order references db.Series.name (authaz/authza)
    series_join = (db.books_series_link, db.Books.id == db.books_series_link.c.book, db.Series)
    listing_options = {}
    if show_hidden:
        # SPA-only recovery rule: include this user's hidden+archived books so
        # they can be unhidden, but do not reveal ordinary archived-only books.
        # common_filters() itself stays strict for classic/OPDS/Kobo/shelves.
        archived_ids = _archived_book_ids()
        listing_options = {
            "allow_show_hidden": True,
            "allow_show_archived": True,
            "extra_filter": or_(
                db.Books.id.notin_(archived_ids),
                db.Books.id.in_(hidden_ids),
            ),
        }
    entries, _random, pagination = calibre_db.fill_indexpage(
        page, per_page, db.Books, db_filter, order,
        True, config.config_read_column, *series_join,
        **listing_options,
    )
    return jsonify({
        "items": to_items(entries),
        "page": pagination.page,
        "per_page": pagination.per_page,
        "total": pagination.total_count,
    })


@api_v1.route("/books/<int:book_id>")
@login_required_if_no_ano
def book_detail(book_id):
    result = calibre_db.get_book_read_archived(
        book_id, config.config_read_column,
        allow_show_archived=True, allow_show_hidden=True,
    )
    if not result:
        return jsonify({"error": {"code": "not_found", "message": "Book not found"}}), 404

    book, read_status, is_archived = result

    # Enrich language objects with display name so serialize_book_detail stays pure
    for lang in getattr(book, "languages", None) or []:
        lang.language_name = isoLanguages.get_language_name(get_locale(), lang.lang_code)

    # Per-user favorite + hidden state (presence-based rows). Anonymous/guest
    # sessions have no real id, so they simply read back as not-favorited/hidden.
    favorited = hidden = False
    kosync_progress = None
    kosync_progress_timestamp = None
    kosync_progress_created_at = None
    if current_user.is_authenticated and not current_user.is_anonymous:
        uid = int(current_user.id)
        favorited = (ub.session.query(ub.FavoriteBook)
                     .filter(ub.FavoriteBook.user_id == uid, ub.FavoriteBook.book_id == book_id)
                     .first() is not None)
        hidden = (ub.session.query(ub.UserHiddenBook)
                  .filter(ub.UserHiddenBook.user_id == uid, ub.UserHiddenBook.book_id == book_id)
                  .first() is not None)
        # KOReader/Kobo synced reading progress (#587) — surfaced on the new-UI
        # book page like the classic detail view. Same source as web.show_book:
        # KoboReadingState.current_bookmark.progress_percent (None when unsynced).
        # #627: the percentage and the two timestamps are resolved together, so
        # a book with no position reports none of them. Previously this view
        # inlined the same query as the classic detail page and the two drifted.
        kosync_progress, last_synced, started_reading = get_kosync_progress_display(
            ub.session, uid, book_id)
        if last_synced:
            kosync_progress_timestamp = last_synced.replace(tzinfo=timezone.utc).isoformat()
        if started_reading:
            kosync_progress_created_at = started_reading.replace(tzinfo=timezone.utc).isoformat()

    # With a custom read column, get_book_read_archived returns the column's value
    # (truthy = read); otherwise the built-in ub.ReadBook.read_status. Match the
    # list badge's logic (fork #579) so both surfaces agree.
    read = bool(read_status) if config.config_read_column \
        else read_status == ub.ReadBook.STATUS_FINISHED
    # Sync-driven "currently reading" tri-state (fork #634). The classic detail
    # page renders this marker off read_status_raw == STATUS_IN_PROGRESS; the SPA
    # book page never received the flag, so the badge was missing in the new UI.
    # Derive it from the shared helper so both surfaces stay in agreement.
    in_progress = book_is_in_progress(
        book_id, read_status, config.config_read_column, current_user)
    body = serialize_book_detail(
        book,
        read=read,
        archived=bool(is_archived),
        favorited=favorited,
        hidden=hidden,
        in_progress=in_progress,
        custom_column_definitions=_detail_custom_columns(),
        original_filename=_original_filename(book_id),
    )
    source_formats, target_formats = get_convert_options(book)
    body["convert_options"] = {
        "sources": source_formats,
        "targets": target_formats,
    }
    body["kosync_progress"] = kosync_progress
    body["kosync_progress_timestamp"] = kosync_progress_timestamp
    body["kosync_progress_created_at"] = kosync_progress_created_at
    return jsonify(body)


@api_v1.route("/books/<int:book_id>/read", methods=["POST"])
@login_required_if_no_ano
def toggle_book_read(book_id):
    data = request.get_json(silent=True) or {}
    read = bool(data.get("read", True))
    edit_book_read_status(book_id, read)
    return jsonify({"read": read})
