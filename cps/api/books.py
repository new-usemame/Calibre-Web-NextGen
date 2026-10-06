# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Catalog endpoints for /api/v1."""
import csv
import io
import json
import math
import tempfile
from datetime import datetime, timezone

from flask import Response, current_app, jsonify, request
from flask_babel import get_locale
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import String, and_, bindparam, cast, false, func, literal, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.functions import coalesce

from . import api_v1
from .serializers import serialize_book_list_item, serialize_book_detail, serialize_custom_column_value
from .. import (
    calibre_db, config, constants, db, ub, isoLanguages, logger, user_library, tag_hierarchy,
)
from ..annotations import count_user_annotations
from ..cw_login import current_user
from ..services import user_cover, discover_source
from ..shelf import sort_shelves_for_user
from ..helper import edit_book_read_status, canonical_read_status, \
    book_ids_with_read_status, read_statuses_for_books, set_explicit_book_read_status, \
    get_convert_options, get_kosync_progress_display, hot_books_page
from ..sort_orders import BOOK_SORT_ORDERS, book_sort_order, viewer_id
from ..sort_orders import RECENT_SORT
from ..custom_column_sort import (resolve_magic_shelf_sort, custom_sort_options,
                                  load_configured_columns)
from ..usermanagement import login_required_if_no_ano

log = logger.create()

MAX_SELECT_ALL_BOOKS = 100_000


def _selection_response(ids, total):
    if total > MAX_SELECT_ALL_BOOKS:
        return jsonify({"error": {
            "code": "selection_too_large",
            "message": "Select all is limited to 100,000 books. Narrow the current view and try again.",
            "max_items": MAX_SELECT_ALL_BOOKS,
        }}), 413
    return jsonify({"ids": ids, "total": total})

MAX_BOOK_EXPORT_ROWS = 100_000
BOOK_EXPORT_BATCH_SIZE = 250
CLASSIC_ADV_EXPORT_SNAPSHOT_MAX_AGE = 24 * 60 * 60
CLASSIC_ADV_EXPORT_SNAPSHOT_MAX_BYTES = 32 * 1024
CLASSIC_ADV_EXPORT_TOKEN_MAX_CHARS = 48 * 1024
CLASSIC_ADV_EXPORT_TOKEN_SALT = "classic-advanced-book-export"
BOOK_EXPORT_COLUMNS = (
    "Title", "Authors", "Series", "Tags", "Rating", "Read", "Formats", "Date added",
)


class BookExportRequestError(Exception):
    """A safe client-facing validation or access failure for book export."""

    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

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

# The sort options, shared with the classic UI's get_sort_function — which
# additionally writes per-user view state and so cannot be called from a
# read-only API endpoint. Only the ORDER BY is common, and it lives in one place
# so a sort cannot be correct in one UI and wrong in the other (fork #1331).
SORT_MAP = BOOK_SORT_ORDERS
# Download-count ordering runs against app.db and is intentionally unavailable
# to the metadata.db-backed generic list/filter queries below.
_COMPATIBLE_BOOK_SORTS = frozenset((set(SORT_MAP) - {"hotasc", "hotdesc"}) | {RECENT_SORT})


def _sort_context(requested_sort):
    """Return validated metadata-db ordering and UI custom-sort options.

    A list request is also valid while no Calibre library session exists (for
    example, a fresh install). Do not turn that recoverable state into a 500.
    """
    columns = load_configured_columns(config)
    resolved = resolve_magic_shelf_sort(requested_sort, config, columns)
    effective = requested_sort if requested_sort in _COMPATIBLE_BOOK_SORTS else resolved.key
    return {
        "sort": effective,
        "order": list(resolved.order_by) if resolved.join else _requested_order(effective),
        "join": resolved.join,
        "sort_persistable": resolved.persistable,
        "custom_sort_options": custom_sort_options(config, columns),
    }


def _with_sort(payload, context):
    # Definitions belong to the page, not every book. Keep them alongside the
    # sort metadata so every /books collection variant has the same contract.
    definitions, _values = _list_custom_column_data([])
    payload.update(sort=context["sort"], sort_persistable=context["sort_persistable"],
                   custom_sort_options=context["custom_sort_options"],
                   custom_column_definitions=definitions)
    return payload


def _requested_order(sort_param):
    """Resolve the ORDER BY for this request, including the per-user ones.

    ``recent`` needs the viewer, and an anonymous browse has no reading history
    to sort by, so it degrades to the default there rather than 400ing on a
    sort the SPA legitimately offers to signed-in users.
    """
    return book_sort_order(sort_param, user_id=_real_user_id())


def _real_user_id():
    """Return a concrete user id, tolerating stripped-decorator unit contexts.

    The anonymous-browse guest is signed in as a real row, so this is not the
    same question as ``is_authenticated`` — see ``sort_orders.viewer_id``, which
    is where that rule lives now that the per-user sorts need it too.
    """
    return viewer_id(current_user)


def _can_browse_global():
    """Role gate that stays safe in stripped-decorator unit contexts."""
    if _real_user_id() is None:
        return False
    try:
        return bool(current_user.role_browse_global())
    except (AttributeError, RuntimeError):
        return False


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


def _row_read_status(e):
    """Return the configured read carrier from a list-query row."""
    if config.config_read_column:
        # generate_linked_query aliases the custom column as ``value``.
        return getattr(e, "value", None)
    return getattr(e, "read_status", None)


def _list_custom_column_data(entries):
    """Return selected custom-field definitions and values for one list page.

    Custom-sort configuration is the display allowlist: it contains only live,
    scalar int/float/datetime fields. Query each selected column once for the
    page rather than touching a relationship on every book (the latter becomes
    an N+1 query on a large grid).
    """
    try:
        columns = load_configured_columns(config) or []
        book_ids = [int(getattr(entry, "Books", entry).id) for entry in entries]
        values = {book_id: {} for book_id in book_ids}
        for column in columns:
            model = db.cc_classes.get(column.id)
            if model is None or not book_ids:
                continue
            rows = (calibre_db.session.query(model)
                    .filter(model.book.in_(book_ids)).all())
            for row in rows:
                value = serialize_custom_column_value(getattr(row, "value", None), column.datatype)
                values.setdefault(int(row.book), {})[str(column.id)] = [{
                    "value": value,
                    "extra": getattr(row, "extra", None),
                }]
        definitions = [{
            "id": column.id,
            "name": column.name,
            "datatype": column.datatype,
        } for column in columns]
        return definitions, values
    except (SQLAlchemyError, AttributeError, KeyError, TypeError):
        log.warning("Custom-column list data unavailable", exc_info=True)
        return [], {}


def _row_to_item(e, in_progress_ids, hidden_ids=None, cover_override=None,
                 read_status_by_id=None, custom_columns=None):
    """Unwrap a SQLAlchemy Row (Books, is_archived, read_status) or plain Books object."""
    book = getattr(e, "Books", e)
    read_status = _row_read_status(e)
    read_status_name = (read_status_by_id or {}).get(int(book.id))
    if read_status_name is None:
        if config.config_read_column:
            read_status_name = "finished" if read_status else (
                "in_progress" if book.id in (in_progress_ids or set()) else "unread")
        else:
            read_status_name = canonical_read_status(read_status)
    if config.config_read_column:
        # Custom read column: generate_linked_query selects read_column.value as the
        # third column (Row attr "value"), NOT ub.ReadBook.read_status — a truthy
        # value means the book is read. Without this the badge is always false when
        # an admin links read status to a Calibre column (fork #579).
        read = read_status_name == "finished"
    else:
        read = read_status_name == "finished"
    archived = bool(getattr(e, "is_archived", False))
    return serialize_book_list_item(
        book,
        read=read,
        in_progress=read_status_name == "in_progress",
        read_status=read_status_name,
        archived=archived,
        hidden=book.id in (hidden_ids or set()),
        cover_override=cover_override,
        custom_columns=custom_columns,
    )


def _visible_shelves_by_book(book_ids):
    """Shelves each listed book sits on, for the card's shelf chips (#1254).

    One query per list page. Visibility is the same rule as the per-book
    ``/books/<id>/shelves`` membership endpoint: the viewer's own shelves plus
    every public shelf -- never another user's private shelf, whose name would
    otherwise leak through any book that happens to be on it. The anonymous
    guest sees public shelves only. Chips follow the viewer's own shelf order,
    so they read the same way as the sidebar.
    """
    if not book_ids:
        return {}
    uid = _real_user_id()
    visibility = ub.Shelf.is_public == 1
    if uid is not None:
        visibility = or_(ub.Shelf.user_id == uid, ub.Shelf.is_public == 1)
    try:
        rows = (ub.session.query(ub.BookShelf.book_id, ub.Shelf)
                .join(ub.Shelf, ub.Shelf.id == ub.BookShelf.shelf)
                .filter(ub.BookShelf.book_id.in_(book_ids))
                .filter(visibility)
                .all())
    except SQLAlchemyError:
        # Tags are supplementary: an unreadable app DB costs the tags, not the
        # page. ub.session is process-wide, so a failed query must not leave it
        # needing a rollback that every later request would then trip over.
        log.warning("Shelf membership unavailable for book list", exc_info=True)
        ub.session.rollback()
        return {}
    except AttributeError:
        # The reconnect window, where the app-DB session is absent.
        log.warning("Shelf membership unavailable for book list", exc_info=True)
        return {}
    if not rows:
        return {}
    shelves = list({shelf.id: shelf for _book_id, shelf in rows}.values())
    sort_shelves_for_user(shelves, current_user)
    rank = {shelf.id: index for index, shelf in enumerate(shelves)}
    by_book = {}
    for book_id, shelf in rows:
        by_book.setdefault(int(book_id), []).append(shelf)
    return {
        book_id: [{"id": shelf.id, "name": shelf.name}
                  for shelf in sorted(on, key=lambda s: rank[s.id])]
        for book_id, on in by_book.items()
    }


def _rows_to_items(entries, hidden_ids=None, custom_values=None):
    """Serialize one list page after resolving its in-progress ids in bulk."""
    entries = list(entries)
    if custom_values is None:
        _definitions, custom_values = _list_custom_column_data(entries)
    statuses = [
        (getattr(entry, "Books", entry).id, _row_read_status(entry))
        for entry in entries
    ]
    status_by_id = read_statuses_for_books(
        statuses, config.config_read_column, current_user)
    in_progress_ids = {book_id for book_id, status in status_by_id.items()
                       if status == "in_progress"}
    books = [getattr(entry, "Books", entry) for entry in entries]
    overrides = user_cover.overrides_for_user(
        _real_user_id(), [book.id for book in books])
    shelves_by_book = _visible_shelves_by_book([int(book.id) for book in books])
    user_id = _real_user_id()
    page_ids = [int(book.id) for book in books]
    favorite_ids = set()
    if user_id is not None and page_ids:
        try:
            favorite_ids = {int(row[0]) for row in (
                ub.session.query(ub.FavoriteBook.book_id)
                .filter(ub.FavoriteBook.user_id == user_id,
                        ub.FavoriteBook.book_id.in_(page_ids)).all()
            )}
        except SQLAlchemyError:
            # Favorite badges are optional metadata, like shelf tags. A busy or
            # unavailable app DB must not turn an otherwise readable catalog
            # page into a 500; mutations still surface their own write errors.
            favorite_ids = None
            log.warning("Could not load favorite badges for catalog page")
    items = []
    for entry, book in zip(entries, books):
        item = _row_to_item(
            entry, in_progress_ids, hidden_ids,
            cover_override=overrides.get(int(book.id)),
            read_status_by_id=status_by_id,
            custom_columns=(custom_values or {}).get(int(book.id)),
        )
        item["shelves"] = shelves_by_book.get(int(book.id), [])
        item["favorited"] = None if favorite_ids is None else int(book.id) in favorite_ids
        items.append(item)
    return items


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
    """Return a per-user read-status filter for catalog views.

    When an admin links read status to a Calibre column (config_read_column),
    filter on that column's value (mirrors web.py's books_list); otherwise use the
    built-in per-user ub.ReadBook table. The join for the custom column is provided
    by generate_linked_query inside fill_indexpage, so the value is queryable here.
    """
    # Personal pauses are independent of the optional shared Boolean column.
    # Even a removed/misconfigured column must not broaden an exact pause view.
    if filter_val in ("did_not_finish", "on_hold"):
        status = (ub.ReadBook.STATUS_DID_NOT_FINISH if filter_val == "did_not_finish"
                  else ub.ReadBook.STATUS_ON_HOLD)
        ids = book_ids_with_read_status(_real_user_id(), status)
        return db.Books.id.in_(ids)
    if config.config_read_column:
        try:
            read_col = db.cc_classes[config.config_read_column].value
        except (KeyError, AttributeError):
            log.error("Custom Column No.%s does not exist in calibre database",
                      config.config_read_column)
            return True
        paused_ids = book_ids_with_read_status(
            _real_user_id(), ub.ReadBook.STATUS_DID_NOT_FINISH,
            ub.ReadBook.STATUS_ON_HOLD)
        if filter_val == "read":
            return and_(coalesce(read_col, False) == True,  # noqa: E712
                        ~db.Books.id.in_(paused_ids))
        if filter_val == "unread":
            return and_(coalesce(read_col, False) != True,  # noqa: E712
                        ~db.Books.id.in_(paused_ids))
        if filter_val == "in_progress":
            ids = book_ids_with_read_status(
                _real_user_id(), ub.ReadBook.STATUS_IN_PROGRESS)
            return and_(db.Books.id.in_(ids), ~coalesce(read_col, False))
        return True
    if filter_val == "read":
        return and_(
            ub.ReadBook.user_id == int(current_user.id),
            ub.ReadBook.read_status == ub.ReadBook.STATUS_FINISHED,
        )
    if filter_val == "unread":
        paused_ids = book_ids_with_read_status(
            _real_user_id(), ub.ReadBook.STATUS_DID_NOT_FINISH,
            ub.ReadBook.STATUS_ON_HOLD)
        return and_(coalesce(ub.ReadBook.read_status, 0) != ub.ReadBook.STATUS_FINISHED,
                    ~db.Books.id.in_(paused_ids))
    if filter_val == "in_progress":
        return ub.ReadBook.read_status == ub.ReadBook.STATUS_IN_PROGRESS
    return True


def _export_ids_filter(values):
    """Build an ID predicate using one SQLite JSON bind, even for large lists."""
    values = sorted({int(value) for value in values if value is not None})
    if not values:
        return false()
    if not db._sqlite_json_available(ub.session, calibre_db.session):
        # Keep the bounded legacy case usable on old SQLite builds. For large
        # lists, failing explicitly is safer than emitting SQL that exceeds
        # SQLite's connection variable limit.
        if len(values) <= 900:
            return db.Books.id.in_(values)
        raise BookExportRequestError(
            "large_filter_unsupported",
            "This SQLite build cannot safely export a large saved list",
            503,
        )
    value_table = func.json_each(bindparam("export_book_ids", json.dumps(values), unique=True)).table_valued(
        "value"
    ).alias("export_book_ids")
    return db.Books.id.in_(select(value_table.c.value))


def _catalog_visibility(show_hidden=False, *, allow_archived=False, viewing_tag_id=None):
    """Return the same common visibility predicate used by catalog listings."""
    if not show_hidden or current_user.is_anonymous:
        return calibre_db.common_filters(
            allow_show_archived=allow_archived, viewing_tag_id=viewing_tag_id
        )
    hidden_ids = _hidden_book_ids()
    archived_ids = _archived_book_ids()
    return calibre_db.common_filters(
        allow_show_archived=True,
        allow_show_hidden=True,
        viewing_tag_id=viewing_tag_id,
        extra_filter=or_(~_export_ids_filter(archived_ids), _export_ids_filter(hidden_ids)),
    )


def _tag_group_filter(path):
    if not isinstance(path, str) or not path or len(path) > 4096:
        raise BookExportRequestError("invalid_request", "Tag group must be bounded text", 400)
    if not current_user.check_visibility(constants.SIDEBAR_CATEGORY):
        raise BookExportRequestError("not_found", "Tag group not found", 404)
    try:
        return tag_hierarchy.read_tree(calibre_db).book_filter(path)
    except LookupError:
        raise BookExportRequestError("not_found", "Tag group not found", 404) from None
    except (SQLAlchemyError, ValueError, TypeError):
        raise BookExportRequestError(
            "service_unavailable", "Tag hierarchy temporarily unavailable. Please try again.", 503
        ) from None


def _catalog_book_query(*, search=None, author_id=None, series_id=None, tag_id=None,
                        publisher_id=None, language_code=None, rating_id=None,
                        book_format=None, filter_val=None, show_hidden=False,
                        book_ids=None, classic_tag_view=False, tag_path=None):
    """Build one unpaged query for catalog search results and file export.

    This is shared by ``/books`` and ``/books/export`` so combined search,
    entity and read criteria cannot drift between the visible list and its
    downloaded copy. Discovery views with randomized/truncated semantics are
    handled explicitly rather than broadening to the full library.
    """
    if filter_val == "hot":
        raise BookExportRequestError(
            "unsupported_source", "The Hot list cannot be exported right now", 400
        )

    if tag_path is not None:
        # A synthetic group never inherits Classic's exact-tag allowlist
        # exception or the library's hidden-book recovery mode.
        classic_tag_view = False
        show_hidden = False

    series_join = (db.books_series_link, db.Books.id == db.books_series_link.c.book, db.Series)
    if search:
        # Keep the search predicate itself in CalibreDB so API, classic, and
        # export search semantics share the one implementation.
        query = calibre_db.search_query(
            search, config, *series_join, allow_show_hidden=show_hidden,
            eager_data=False,
            viewing_tag_id=tag_id if classic_tag_view else None,
        )
    else:
        query = calibre_db.generate_linked_query(config.config_read_column, db.Books)
        query = query.outerjoin(db.books_series_link, db.Books.id == db.books_series_link.c.book)
        query = query.outerjoin(db.Series)
        query = query.filter(_catalog_visibility(
            show_hidden, allow_archived=(filter_val == "archived" and tag_path is None),
            viewing_tag_id=tag_id if classic_tag_view else None,
        ))

    if filter_val == "discover":
        if book_ids is None:
            raise BookExportRequestError(
                "unsupported_source", "Discover export requires the visible sample IDs", 400
            )
        query = query.filter(_export_ids_filter(book_ids))
        query = query.filter(_build_read_filter("unread"))
    elif filter_val == "archived":
        archived = (ub.session.query(ub.ArchivedBook.book_id)
                    .filter(ub.ArchivedBook.user_id == int(current_user.id),
                            ub.ArchivedBook.is_archived.is_(True)).all())
        query = query.filter(_export_ids_filter([row[0] for row in archived]))
    elif filter_val == "favorites":
        favorites = (ub.session.query(ub.FavoriteBook.book_id)
                     .filter(ub.FavoriteBook.user_id == int(current_user.id)).all())
        query = query.filter(_export_ids_filter([row[0] for row in favorites]))
    elif filter_val == "rated":
        query = query.filter(db.Books.ratings.any(db.Ratings.rating > 9))
    elif filter_val not in (None, "", "all", "read", "unread", "in_progress",
                            "did_not_finish", "on_hold", "discover"):
        raise BookExportRequestError("invalid_filter", "Unsupported book-list filter", 400)

    entity_filter = _build_entity_filter(
        author_id, series_id, tag_id, publisher_id, language_code,
        rating=rating_id, book_format=book_format,
    )
    if entity_filter is not True:
        query = query.filter(entity_filter)
    if tag_path is not None:
        query = query.filter(_tag_group_filter(tag_path))
    if filter_val in ("read", "unread", "in_progress", "did_not_finish", "on_hold"):
        query = query.filter(_build_read_filter(filter_val))
    eager_options = [
        selectinload(db.Books.authors),
        selectinload(db.Books.series),
        selectinload(db.Books.tags),
        selectinload(db.Books.ratings),
    ]
    eager_options.append(selectinload(db.Books.data))
    return query.options(*eager_options).distinct()


def _strict_int(value, label, *, optional=False):
    if optional and value in (None, ""):
        return None
    if isinstance(value, bool):
        raise BookExportRequestError("invalid_request", f"{label} must be an integer", 400)
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and len(value.strip()) <= 19 and value.strip().isascii() \
            and value.strip().isdecimal():
        parsed = int(value.strip())
    else:
        raise BookExportRequestError("invalid_request", f"{label} must be an integer", 400) from None
    if parsed < 0:
        raise BookExportRequestError("invalid_request", f"{label} must be non-negative", 400)
    if parsed > (2**63 - 1):
        raise BookExportRequestError("invalid_request", f"{label} is too large", 400)
    return parsed


def _export_params_object(value, allowed_keys, label):
    if not isinstance(value, dict):
        raise BookExportRequestError("invalid_request", f"{label} parameters must be an object", 400)
    unknown = set(value) - set(allowed_keys)
    if unknown:
        raise BookExportRequestError(
            "invalid_request", f"Unsupported {label} parameter: {sorted(unknown)[0]}", 400
        )
    return value


def _classic_adv_export_serializer():
    return URLSafeTimedSerializer(
        current_app.secret_key, salt=CLASSIC_ADV_EXPORT_TOKEN_SALT
    )


def create_classic_advanced_export_snapshot(term):
    """Sign an immutable Classic advanced-search term for its rendered page."""
    user_id = _real_user_id()
    if user_id is None or not isinstance(term, dict):
        return None
    try:
        serialized = json.dumps(term, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError):
        return None
    if len(serialized.encode("utf-8")) > CLASSIC_ADV_EXPORT_SNAPSHOT_MAX_BYTES:
        return None
    return _classic_adv_export_serializer().dumps({
        "version": 1,
        "user_id": int(user_id),
        "term": term,
    })


def _classic_advanced_export_query(params):
    params = _export_params_object(params, {"snapshot", "sort"}, "Classic advanced search")
    token = params.get("snapshot")
    if not isinstance(token, str) or len(token) > CLASSIC_ADV_EXPORT_TOKEN_MAX_CHARS:
        raise BookExportRequestError(
            "invalid_snapshot", "This advanced-search result is no longer available. Run the search again.", 400
        )
    try:
        payload = _classic_adv_export_serializer().loads(
            token, max_age=CLASSIC_ADV_EXPORT_SNAPSHOT_MAX_AGE
        )
    except SignatureExpired:
        raise BookExportRequestError(
            "expired_snapshot", "This advanced-search result expired. Run the search again.", 409
        ) from None
    except BadSignature:
        raise BookExportRequestError(
            "invalid_snapshot", "This advanced-search result is invalid. Run the search again.", 400
        ) from None
    if (not isinstance(payload, dict) or payload.get("version") != 1
            or isinstance(payload.get("user_id"), bool)
            or payload.get("user_id") != _real_user_id()
            or not isinstance(payload.get("term"), dict)):
        raise BookExportRequestError(
            "invalid_snapshot", "This advanced-search result is unavailable to this account.", 403
        )
    term = payload["term"]
    try:
        term_size = len(json.dumps(term, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, OverflowError):
        term_size = CLASSIC_ADV_EXPORT_SNAPSHOT_MAX_BYTES + 1
    if term_size > CLASSIC_ADV_EXPORT_SNAPSHOT_MAX_BYTES:
        raise BookExportRequestError("invalid_snapshot", "The saved search is too large", 400)
    sort_context = _export_sort(params.get("sort"))
    from ..search import build_adv_search_query
    query, _criteria = build_adv_search_query(term)
    return _join_sort(query, sort_context).options(
        selectinload(db.Books.authors), selectinload(db.Books.series),
        selectinload(db.Books.tags), selectinload(db.Books.ratings),
        selectinload(db.Books.data),
    ).distinct().order_by(*sort_context["order"])


def _export_sort(value, *, default="new"):
    """Resolve export ordering strictly; never silently replace a chosen sort."""
    value = default if value is None else value
    if not isinstance(value, str) or len(value) > 32:
        raise BookExportRequestError("invalid_request", "Sort must be a short text value", 400)
    if value in _COMPATIBLE_BOOK_SORTS:
        return {"sort": value, "order": _requested_order(value), "join": ()}
    resolved = resolve_magic_shelf_sort(value, config, load_configured_columns(config))
    if not resolved.persistable:
        raise BookExportRequestError(
            "sort_unavailable", "Custom sort metadata is unavailable. Retry the export.", 503
        )
    if resolved.key != value or not resolved.join:
        raise BookExportRequestError("invalid_request", "Unsupported sort order", 400)
    return {"sort": value, "order": list(resolved.order_by), "join": resolved.join}


def _join_sort(query, context):
    """Apply the validated direct join before any count, selection or paging."""
    return query.outerjoin(*context["join"]) if context["join"] else query


def _safe_csv_cell(value):
    if value is None:
        return ""
    text = str(value)
    probe = text.lstrip(" \t\r\n\v\f\x00\u00a0\u200b\ufeff")
    if probe.startswith(("=", "+", "-", "@")):
        # Quoting CSV fields does not prevent spreadsheet formula evaluation.
        return "'" + text
    return text


def _one_line(value):
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def _book_export_values(entry):
    book = getattr(entry, "Books", entry)
    authors = [author.name.replace("|", ",") for author in (book.authors or []) if author]
    series = [item for item in (book.series or []) if item]
    series_text = "; ".join(
        f"{item.name} ({book.series_index})" for item in series
    )
    tags = ", ".join(tag.name for tag in (book.tags or []) if tag)
    ratings = [rating.rating for rating in (book.ratings or []) if rating and rating.rating is not None]
    rating_text = "" if not ratings else f"{ratings[0] / 2:g}"
    if config.config_read_column:
        read_value = bool(getattr(entry, "value", None))
    else:
        read_value = getattr(entry, "read_status", None) == ub.ReadBook.STATUS_FINISHED
    formats = ", ".join(sorted({str(data.format).lower() for data in (book.data or []) if data.format}))
    timestamp = getattr(book, "timestamp", None)
    return [
        book.title or "",
        ", ".join(authors),
        series_text,
        tags,
        rating_text,
        "Yes" if read_value else "No",
        formats,
        timestamp.isoformat() if hasattr(timestamp, "isoformat") else timestamp or "",
    ]


def _count_export_rows(query):
    # LIMIT keeps the preflight bounded while still making the 100,000-row
    # refusal exact. Clear ORDER BY so SQLite doesn't sort before counting.
    return (query.with_entities(db.Books.id).order_by(None).distinct()
            .limit(MAX_BOOK_EXPORT_ROWS + 1).count())


def _write_export(spool, query, format_name):
    """Prepare an export before sending headers and return the number of rows written."""
    written = 0

    def write_record(value):
        nonlocal written
        if written >= MAX_BOOK_EXPORT_ROWS:
            raise BookExportRequestError(
                "result_changed",
                "The book list grew beyond the export limit while it was being prepared. Retry the download.",
                409,
            )
        spool.write(value.encode("utf-8"))
        written += 1

    if format_name == "txt":
        batch = []
        for entry in query.yield_per(BOOK_EXPORT_BATCH_SIZE):
            batch.append(entry)
            if len(batch) >= BOOK_EXPORT_BATCH_SIZE:
                calibre_db.order_authors(batch, list_return=True, combined=True)
                for row in batch:
                    values = _book_export_values(row)
                    write_record(_one_line(values[0] + " — " + values[1]) + "\n")
                batch.clear()
        if batch:
            calibre_db.order_authors(batch, list_return=True, combined=True)
            for row in batch:
                values = _book_export_values(row)
                write_record(_one_line(values[0] + " — " + values[1]) + "\n")
        return written

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(BOOK_EXPORT_COLUMNS)
    spool.write(buffer.getvalue().encode("utf-8"))
    buffer.seek(0)
    buffer.truncate(0)

    batch = []
    for entry in query.yield_per(BOOK_EXPORT_BATCH_SIZE):
        batch.append(entry)
        if len(batch) >= BOOK_EXPORT_BATCH_SIZE:
            calibre_db.order_authors(batch, list_return=True, combined=True)
            for row in batch:
                writer.writerow([_safe_csv_cell(value) for value in _book_export_values(row)])
                write_record(buffer.getvalue())
                buffer.seek(0)
                buffer.truncate(0)
            batch.clear()
    if batch:
        calibre_db.order_authors(batch, list_return=True, combined=True)
        for row in batch:
            writer.writerow([_safe_csv_cell(value) for value in _book_export_values(row)])
            write_record(buffer.getvalue())
            buffer.seek(0)
            buffer.truncate(0)
    return written


def _query_order(sort_key):
    return _requested_order(sort_key)


def _catalog_export_query(params, *, classic_tag_view=False):
    params = _export_params_object(params, {
        "search", "filter", "book_ids", "author", "series", "tag", "publisher",
        "language", "rating", "format", "sort", "show_hidden", "tag_path",
    }, "Catalog")
    if "tag_path" in params and (
        not isinstance(params["tag_path"], str) or not params["tag_path"]
        or len(params["tag_path"]) > 4096
    ):
        raise BookExportRequestError("invalid_request", "Tag group must be bounded text", 400)
    search = params.get("search")
    if search is not None and not isinstance(search, str):
        raise BookExportRequestError("invalid_request", "Search must be text", 400)
    search = (search or "").strip()
    if len(search) > 512:
        raise BookExportRequestError("invalid_request", "Search is too long", 400)
    filter_val = params.get("filter")
    if filter_val is not None and not isinstance(filter_val, str):
        raise BookExportRequestError("invalid_request", "Filter must be text", 400)
    if filter_val not in (None, "", "all", "read", "unread", "in_progress",
                          "did_not_finish", "on_hold", "favorites", "rated", "archived", "discover"):
        raise BookExportRequestError("invalid_filter", "Unsupported book-list filter", 400)
    for key, limit in (("language", 128), ("format", 32)):
        value = params.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise BookExportRequestError("invalid_request", f"{key.title()} must be short text", 400)
    show_hidden = params.get("show_hidden", False)
    if not isinstance(show_hidden, (bool, int, str)) or (
        isinstance(show_hidden, str) and show_hidden.lower() not in ("0", "1", "true", "false", "yes", "no", "on", "off")
    ):
        raise BookExportRequestError("invalid_request", "show_hidden must be a boolean", 400)
    ids = params.get("book_ids")
    sort_context = _export_sort(params.get("sort"))
    if ids is not None:
        if not isinstance(ids, list) or len(ids) > MAX_BOOK_EXPORT_ROWS:
            raise BookExportRequestError("invalid_request", "Book IDs must be a bounded list", 400)
        ids = [_strict_int(value, "Book ID") for value in ids]
        if len(set(ids)) != len(ids):
            raise BookExportRequestError("invalid_request", "Book IDs must be unique", 400)
    elif filter_val == "discover":
        raise BookExportRequestError(
            "unsupported_source", "Discover export requires the visible sample IDs", 400
        )
    if ids is not None and filter_val != "discover":
        raise BookExportRequestError(
            "invalid_request", "Book IDs are supported only for the Discover sample", 400
        )

    read_filter = filter_val if filter_val in (
        "read", "unread", "in_progress", "did_not_finish", "on_hold") else None
    query = _catalog_book_query(
        search=search or None,
        author_id=_strict_int(params.get("author"), "Author ID", optional=True),
        series_id=_strict_int(params.get("series"), "Series ID", optional=True),
        tag_id=_strict_int(params.get("tag"), "Tag ID", optional=True),
        publisher_id=_strict_int(params.get("publisher"), "Publisher ID", optional=True),
        language_code=params.get("language"),
        rating_id=_strict_int(params.get("rating"), "Rating ID", optional=True),
        book_format=params.get("format"),
        filter_val=filter_val if filter_val not in (None, "", "all") else read_filter,
        show_hidden=(filter_val != "discover" and show_hidden
                     in (True, 1, "1", "true", "yes", "on")),
        book_ids=ids,
        classic_tag_view=classic_tag_view,
        tag_path=params.get("tag_path"),
    )
    query = _join_sort(query, sort_context)
    if ids is not None:
        # The random sample is explicit in the request, but every member is
        # revalidated against the caller's visible, unread catalog. A stale or
        # forged ID refuses the entire export instead of silently widening it.
        expected = len(ids)
        if _count_export_rows(query) != expected:
            raise BookExportRequestError(
                "invalid_book_ids", "The Discover sample changed; reload it and retry", 409
            )
        if ids:
            # `CASE id WHEN ...` expands two bind parameters per sample ID.
            # Keep the whole sample in a single JSON bind and use delimited
            # lookup positions to preserve the visible Discover order.
            sample_csv = "," + ",".join(str(book_id) for book_id in ids) + ","
            id_token = literal(",") + cast(db.Books.id, String) + literal(",")
            query = query.order_by(func.instr(
                bindparam("discover_sample_order", sample_csv, unique=True), id_token
            ))
    else:
        query = query.order_by(*sort_context["order"])
    return query


def _advanced_export_query(params):
    from ..search import build_adv_search_query
    from .search import _CC_KEY, _CC_RANGE, _CC_SINGLE, _custom_value, _json_to_term

    params = _export_params_object(params, {
        "title", "authors", "publisher", "comments", "publishstart", "publishend",
        "rating_high", "rating_low", "read_status", "include_tag", "exclude_tag",
        "include_serie", "exclude_serie", "include_language", "exclude_language",
        "include_extension", "exclude_extension", "include_shelf", "exclude_shelf",
        "custom", "sort",
    }, "Advanced search")
    for key in ("title", "authors", "publisher", "comments", "publishstart", "publishend",
                "read_status"):
        value = params.get(key)
        if value is not None and not isinstance(value, str):
            raise BookExportRequestError("invalid_request", f"{key} must be text", 400)
        if isinstance(value, str) and len(value) > 2048:
            raise BookExportRequestError("invalid_request", f"{key} is too long", 400)
    for key in ("rating_high", "rating_low"):
        value = params.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise BookExportRequestError("invalid_request", f"{key} must be a rating", 400)
        try:
            rating = int(value)
        except (TypeError, ValueError, OverflowError):
            raise BookExportRequestError("invalid_request", f"{key} must be a rating", 400) from None
        if isinstance(value, float) and not value.is_integer():
            raise BookExportRequestError("invalid_request", f"{key} must be a whole-star rating", 400)
        if not 0 <= rating <= 5:
            raise BookExportRequestError("invalid_request", f"{key} must be between 0 and 5", 400)
    if params.get("read_status", "all") not in (
            "all", "read", "unread", "in_progress", "did_not_finish", "on_hold"):
        raise BookExportRequestError("invalid_request", "Unsupported read-status filter", 400)
    for key in ("publishstart", "publishend"):
        value = params.get(key)
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                raise BookExportRequestError("invalid_request", f"{key} must be an ISO date", 400) from None
    for key in ("include_tag", "exclude_tag", "include_serie", "exclude_serie",
                "include_language", "exclude_language", "include_extension", "exclude_extension",
                "include_shelf", "exclude_shelf"):
        value = params.get(key)
        if value is not None and (
            not isinstance(value, list) or len(value) > 500
            or any(not isinstance(item, (str, int)) or isinstance(item, bool)
                   or len(str(item)) > 256 for item in value)
        ):
            raise BookExportRequestError("invalid_request", f"{key} must be a bounded list", 400)
    custom = params.get("custom")
    if custom is not None and (
        not isinstance(custom, dict) or len(custom) > 100
        or any(not isinstance(key, str) or len(key) > 64
               or not isinstance(value, (str, int, float, bool))
               for key, value in custom.items())
        or any(len(str(value)) > 2048 for value in custom.values())
        or any(isinstance(value, float) and not math.isfinite(value)
               for value in custom.values())
    ):
        raise BookExportRequestError("invalid_request", "custom must be a bounded object", 400)
    columns = calibre_db.get_cc_columns(config, filter_config_custom_read=True)
    custom_types = {int(column.id): column.datatype for column in columns}
    for key, raw in (custom or {}).items():
        match = _CC_KEY.fullmatch(key)
        if match is None:
            raise BookExportRequestError("invalid_request", "Unsupported custom-column filter", 400)
        column_id = int(match.group(1))
        datatype = custom_types.get(column_id)
        suffix = match.group(2) or ""
        if datatype not in _CC_SINGLE and datatype not in _CC_RANGE:
            raise BookExportRequestError("invalid_request", "Unsupported custom-column filter", 400)
        if ((datatype in _CC_RANGE and suffix not in _CC_RANGE[datatype])
                or (datatype in _CC_SINGLE and suffix)):
            raise BookExportRequestError("invalid_request", "Unsupported custom-column filter", 400)
        if raw not in (None, "") and _custom_value(datatype, suffix, raw) is None:
            raise BookExportRequestError("invalid_request", "Invalid custom-column filter value", 400)
    term = _json_to_term(params, columns)
    query, _criteria = build_adv_search_query(term)
    sort_context = _export_sort(params.get("sort"))
    return _join_sort(query, sort_context).options(
        selectinload(db.Books.authors), selectinload(db.Books.series),
        selectinload(db.Books.tags), selectinload(db.Books.ratings),
        selectinload(db.Books.data),
    ).distinct().order_by(*sort_context["order"])


def _manual_shelf_export_query(shelf_id, params):
    from ..shelf import check_shelf_view_permissions

    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.id == shelf_id).first()
    if shelf is None:
        raise BookExportRequestError("not_found", "Shelf not found", 404)
    if not check_shelf_view_permissions(shelf):
        raise BookExportRequestError("forbidden", "You are not allowed to view this shelf", 403)

    query = calibre_db.generate_linked_query(config.config_read_column, db.Books)
    query = query.join(ub.BookShelf, ub.BookShelf.book_id == db.Books.id)
    query = query.outerjoin(db.books_series_link, db.Books.id == db.books_series_link.c.book)
    query = query.outerjoin(db.Series).filter(ub.BookShelf.shelf == shelf_id)
    query = query.filter(calibre_db.common_filters(allow_public_shelf_books=bool(shelf.is_public)))
    params = _export_params_object(params, {"sort"}, "Shelf")
    sort_key = params.get("sort", "stored")
    if not isinstance(sort_key, str) or len(sort_key) > 32:
        raise BookExportRequestError("invalid_request", "Sort must be a short text value", 400)
    if sort_key not in ("stored", "recent", "hotasc", "hotdesc", *SORT_MAP):
        raise BookExportRequestError("invalid_request", "Unsupported shelf sort order", 400)
    if sort_key == "stored" or sort_key in ("hotasc", "hotdesc"):
        order = (ub.BookShelf.order.asc(), db.Books.id.asc())
    else:
        order = tuple(_query_order(sort_key))
    return query.options(
        selectinload(db.Books.authors), selectinload(db.Books.series),
        selectinload(db.Books.tags), selectinload(db.Books.ratings),
        selectinload(db.Books.data),
    ).distinct().order_by(*order)


def _smart_shelf_export_query(shelf_id, params, *, classic_owner_rules=False):
    from .. import magic_shelf
    from ..custom_column_sort import load_configured_columns, resolve_magic_shelf_sort

    shelf = ub.session.query(ub.MagicShelf).filter(ub.MagicShelf.id == shelf_id).first()
    if shelf is None:
        raise BookExportRequestError("not_found", "Smart shelf not found", 404)
    uid = _real_user_id()
    if shelf.user_id != uid and not shelf.is_public:
        raise BookExportRequestError("forbidden", "You are not allowed to view this shelf", 403)
    rule_user_id = shelf.user_id if classic_owner_rules else uid
    try:
        query_filter = magic_shelf.build_query_from_rules(shelf.rules, user_id=rule_user_id)
    except Exception:
        log.warning("Could not build smart-shelf export query for %s", shelf_id, exc_info=True)
        query_filter = None
    query = calibre_db.generate_linked_query(config.config_read_column, db.Books)
    if query_filter is None:
        query = query.filter(false())
    else:
        query = query.filter(query_filter)
        bypass_language = (
            magic_shelf.rules_reference_read_status(shelf.rules)
            if classic_owner_rules else False
        )
        query = query.filter(calibre_db.common_filters(return_all_languages=bypass_language))

    params = _export_params_object(params, {"sort"}, "Smart shelf")
    sort_key = params.get("sort", "new")
    if not isinstance(sort_key, str) or len(sort_key) > 40:
        raise BookExportRequestError("invalid_request", "Sort must be a short text value", 400)
    columns = load_configured_columns(config)
    resolved = resolve_magic_shelf_sort(sort_key, config, columns)
    query = query.outerjoin(db.books_series_link, db.Books.id == db.books_series_link.c.book)
    query = query.outerjoin(db.Series)
    if resolved.join:
        query = query.outerjoin(*resolved.join)
    return query.options(
        selectinload(db.Books.authors), selectinload(db.Books.series),
        selectinload(db.Books.tags), selectinload(db.Books.ratings),
        selectinload(db.Books.data),
    ).distinct().order_by(*resolved.order_by)


def _global_export_query(params):
    if (not current_user.is_authenticated or current_user.is_anonymous
            or not current_user.role_browse_global()):
        raise BookExportRequestError("forbidden", "Global library access required", 403)
    params = _export_params_object(params, {"search", "filter", "sort"}, "Global library")
    filter_name = params.get("filter", "all")
    if filter_name not in ("all", "not_in_my_library"):
        raise BookExportRequestError("invalid_filter", "Unsupported global-library filter", 400)
    term = params.get("search", "")
    if not isinstance(term, str) or len(term) > 512:
        raise BookExportRequestError("invalid_request", "Search must be at most 512 characters", 400)
    filters = []
    if filter_name == "not_in_my_library":
        filters.append(user_library.global_missing_filter(current_user, cdb=calibre_db))
    if term.strip():
        like = "%" + term.strip() + "%"
        filters.append(or_(
            func.lower(db.Books.title).ilike(func.lower(like)),
            db.Books.authors.any(func.lower(db.Authors.name).ilike(func.lower(like))),
            db.Books.series.any(func.lower(db.Series.name).ilike(func.lower(like))),
        ))
    query = calibre_db.generate_linked_query(config.config_read_column, db.Books)
    query = query.outerjoin(db.books_series_link, db.Books.id == db.books_series_link.c.book)
    query = query.outerjoin(db.Series).filter(and_(*filters) if filters else True)
    query = query.filter(calibre_db.common_filters(allow_show_global=True))
    sort_context = _export_sort(params.get("sort"))
    return _join_sort(query, sort_context).options(
        selectinload(db.Books.authors), selectinload(db.Books.series),
        selectinload(db.Books.tags), selectinload(db.Books.ratings),
        selectinload(db.Books.data),
    ).distinct().order_by(*sort_context["order"])


def _book_export_query(payload):
    if not isinstance(payload, dict):
        raise BookExportRequestError("invalid_request", "Export request must be an object", 400)
    if set(payload) - {"format", "source", "id", "params"}:
        raise BookExportRequestError("invalid_request", "Unsupported export request field", 400)
    format_name = payload.get("format", "csv")
    if format_name not in ("csv", "txt"):
        raise BookExportRequestError("invalid_format", "Format must be csv or txt", 400)
    source = payload.get("source")
    params = payload.get("params", {})
    if not isinstance(params, dict):
        raise BookExportRequestError("invalid_request", "Export parameters must be an object", 400)
    shelf_id = _strict_int(payload.get("id"), "Shelf ID", optional=True)
    if source == "catalog":
        query = _catalog_export_query(params)
    elif source == "classic_catalog":
        if _strict_int(params.get("tag"), "Tag ID", optional=True) is None:
            raise BookExportRequestError(
                "unsupported_source", "This Classic list is not an exportable tag view", 400
            )
        query = _catalog_export_query(params, classic_tag_view=True)
    elif source == "advanced":
        query = _advanced_export_query(params)
    elif source == "classic_advanced":
        query = _classic_advanced_export_query(params)
    elif source == "shelf":
        if shelf_id is None:
            raise BookExportRequestError("invalid_request", "Shelf ID is required", 400)
        query = _manual_shelf_export_query(shelf_id, params)
    elif source == "smart_shelf":
        if shelf_id is None:
            raise BookExportRequestError("invalid_request", "Smart shelf ID is required", 400)
        query = _smart_shelf_export_query(shelf_id, params)
    elif source == "classic_smart_shelf":
        if shelf_id is None:
            raise BookExportRequestError("invalid_request", "Smart shelf ID is required", 400)
        query = _smart_shelf_export_query(shelf_id, params, classic_owner_rules=True)
    elif source == "global":
        query = _global_export_query(params)
    else:
        raise BookExportRequestError("unsupported_source", "This book list cannot be exported", 400)
    return format_name, query


@api_v1.route("/books/export", methods=["POST"])
@login_required_if_no_ano
def export_book_list():
    """Stream the current visible result set as CSV or one-book-per-line TXT."""
    if _real_user_id() is None:
        return jsonify({"error": {"code": "authentication_required",
                                  "message": "Sign in to export a book list"}}), 401
    user_library.mark_response_user_specific()
    payload = request.get_json(silent=True)
    try:
        format_name, query = _book_export_query(payload)
        count = _count_export_rows(query)
        if count > MAX_BOOK_EXPORT_ROWS:
            return jsonify({"error": {
                "code": "export_too_large",
                "message": f"This result has more than {MAX_BOOK_EXPORT_ROWS:,} books. Add a filter and retry.",
                "limit": MAX_BOOK_EXPORT_ROWS,
            }}), 413
        spool = tempfile.SpooledTemporaryFile(max_size=1_048_576, mode="w+b")
        try:
            written = _write_export(spool, query, format_name)
        except Exception:
            spool.close()
            raise
        if written != count:
            spool.close()
            return jsonify({"error": {
                "code": "result_changed",
                "message": "The book list changed while it was being exported. Retry the download.",
            }}), 409
        spool.seek(0)
        response = Response(
            iter(lambda: spool.read(64 * 1024), b""),
            mimetype="text/csv" if format_name == "csv" else "text/plain",
            headers={
                "Content-Disposition": f'attachment; filename="calibre-web-books.{format_name}"',
                "Cache-Control": "private, no-store",
                "X-Export-Count": str(count),
            },
        )
        response.vary.add("Cookie")
        response.vary.add("Authorization")
        response.call_on_close(spool.close)
        return response
    except BookExportRequestError as exc:
        return jsonify({"error": {"code": exc.code, "message": exc.message}}), exc.status


@api_v1.route("/books")
@login_required_if_no_ano
def list_books():
    select_all = request.args.get("select_all", "").strip().lower() in (
        "1", "true", "yes", "on"
    )
    page = 1 if select_all else request.args.get("page", 1, type=int)
    per_page = (MAX_SELECT_ALL_BOOKS + 1 if select_all else request.args.get(
        "per_page", config.config_books_per_page, type=int
    ))
    sort = request.args.get("sort", "new")
    sort_context = _sort_context(sort)
    order = sort_context["order"]
    custom_join = sort_context["join"]
    search = request.args.get("search")
    show_hidden = (request.args.get("show_hidden", "").strip().lower()
                   in ("1", "true", "yes", "on"))
    # Anonymous browse has no per-user hidden state. Treat a forged query as the
    # ordinary list and keep the response/action surface coherent with the 401
    # mutation guard.
    show_hidden = bool(show_hidden and _real_user_id() is not None)
    hidden_ids = _hidden_book_ids() if show_hidden else set()
    to_items = lambda entries: _rows_to_items(entries, hidden_ids)

    author_id = request.args.get("author", type=int)
    series_id = request.args.get("series", type=int)
    tag_id = request.args.get("tag", type=int)
    tag_path = request.args.get("tag_path")
    publisher_id = request.args.get("publisher", type=int)
    language_code = request.args.get("language")
    rating_id = request.args.get("rating", type=int)
    book_format = request.args.get("format")
    filter_val = request.args.get("filter")
    has_entity_filter = any(value not in (None, "") for value in (
        author_id, series_id, tag_id, publisher_id, language_code, rating_id, book_format,
    ))

    if search or tag_path is not None:
        offset = (page - 1) * per_page
        try:
            query = _catalog_book_query(
                search=search,
                author_id=author_id,
                series_id=series_id,
                tag_id=tag_id,
                tag_path=tag_path,
                publisher_id=publisher_id,
                language_code=language_code,
                rating_id=rating_id,
                book_format=book_format,
                filter_val=(filter_val if tag_path is not None or filter_val in
                            ("read", "unread", "in_progress", "did_not_finish", "on_hold",
                             "favorites", "rated", "archived") else None),
                show_hidden=show_hidden if tag_path is None else False,
            )
        except BookExportRequestError as exc:
            return jsonify({"error": {"code": exc.code, "message": exc.message}}), exc.status
        query = _join_sort(query, sort_context)
        total = query.with_entities(db.Books.id).order_by(None).distinct().count()
        if select_all:
            ids = [row[0] for row in query.with_entities(db.Books.id)
                   .order_by(*order).limit(per_page).all()]
            return _selection_response(ids, total)
        entries = query.order_by(*order).offset(offset).limit(per_page).all()
        entries = calibre_db.order_authors(entries, list_return=True, combined=True)
        return jsonify(_with_sort({
            "items": to_items(entries),
            "page": page,
            "per_page": per_page,
            "total": total,
        }, sort_context))

    series_join = (db.books_series_link, db.Books.id == db.books_series_link.c.book, db.Series)

    # --- discovery views (mirror web.py books_list categories) ---
    if filter_val == "discover":
        # Random unread books (single page, like the legacy Discover view).
        source_filter, _source_available = discover_source.filter_for(current_user)
        sort_context["sort"] = "new"
        if not config.config_read_column:
            disc_filter = coalesce(ub.ReadBook.read_status, 0) != ub.ReadBook.STATUS_FINISHED
        else:
            try:
                disc_filter = coalesce(db.cc_classes[config.config_read_column].value, False) != True  # noqa: E712
            except (KeyError, AttributeError):
                disc_filter = True
        paused_ids = book_ids_with_read_status(
            _real_user_id(), ub.ReadBook.STATUS_DID_NOT_FINISH,
            ub.ReadBook.STATUS_ON_HOLD)
        disc_filter = and_(disc_filter, ~db.Books.id.in_(paused_ids))
        discover_per_page = config.config_books_per_page if select_all else per_page
        entries, _random, _pg = calibre_db.fill_indexpage(
            1, discover_per_page, db.Books, disc_filter, [func.randomblob(2)],
            True, config.config_read_column, ids_only=select_all, extra_filter=source_filter)
        if select_all:
            # Discover is deliberately a random, one-page sample. Its current
            # cards are the complete view; never expand this request to the
            # 100,001-row safety probe used by normal full-result selection.
            return _selection_response(entries, len(entries))
        items = to_items(entries)
        return jsonify(_with_sort({"items": items, "page": 1, "per_page": per_page,
                                  "total": len(items)}, sort_context))

    if filter_val == "hot":
        sort_context["sort"] = "new"
        if select_all:
            entries, total = hot_books_page(calibre_db.common_filters(), BOOK_SORT_ORDERS["hotdesc"],
                                            0, per_page, ids_only=True)
            return _selection_response(entries, total)
        entries, total = hot_books_page(calibre_db.common_filters(), BOOK_SORT_ORDERS["hotdesc"],
                                        per_page * (page - 1), per_page)
        return jsonify(_with_sort({"items": to_items(entries),
                        "page": page, "per_page": per_page, "total": total}, sort_context))

    # --- archived path (two-step: collect ids, then fill_indexpage_with_archived_books) ---
    if filter_val == "archived" and not has_entity_filter:
        archived_books = (ub.session.query(ub.ArchivedBook)
                         .filter(ub.ArchivedBook.user_id == int(current_user.id))
                         .filter(ub.ArchivedBook.is_archived == True)  # noqa: E712
                         .all())
        archived_ids = [ab.book_id for ab in archived_books]
        archived_filter = db.Books.id.in_(archived_ids)
        series_join = (db.books_series_link, db.Books.id == db.books_series_link.c.book, db.Series)
        entries, _random, pagination = calibre_db.fill_indexpage_with_archived_books(
            page, db.Books, per_page, archived_filter, order,
            True, True, config.config_read_column, *series_join, *custom_join, ids_only=select_all,
        )
        if select_all:
            return _selection_response(entries, pagination.total_count)
        return jsonify(_with_sort({
            "items": to_items(entries),
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total_count,
        }, sort_context))

    # --- discovery views (mirror web.py books_list categories) ---
    if filter_val == "favorites" and not has_entity_filter:
        fav_ids = [f.book_id for f in (ub.session.query(ub.FavoriteBook)
                   .filter(ub.FavoriteBook.user_id == int(current_user.id)).all())]
        entries, _random, pagination = calibre_db.fill_indexpage(
            page, per_page, db.Books, db.Books.id.in_(fav_ids), order,
            True, config.config_read_column, *series_join, *custom_join, ids_only=select_all)
        if select_all:
            return _selection_response(entries, pagination.total_count)
        return jsonify(_with_sort({"items": to_items(entries),
                        "page": pagination.page, "per_page": pagination.per_page,
                        "total": pagination.total_count}, sort_context))

    if filter_val == "rated" and not has_entity_filter:
        # Top-rated: Calibre stores rating 0–10 (half-stars); >9 == 5 stars.
        rated_filter = db.Books.ratings.any(db.Ratings.rating > 9)
        entries, _random, pagination = calibre_db.fill_indexpage(
            page, per_page, db.Books, rated_filter, order,
            True, config.config_read_column, *series_join, *custom_join, ids_only=select_all)
        if select_all:
            return _selection_response(entries, pagination.total_count)
        return jsonify(_with_sort({"items": to_items(entries),
                        "page": pagination.page, "per_page": pagination.per_page,
                        "total": pagination.total_count}, sort_context))

    if filter_val in ("archived", "favorites", "rated") and has_entity_filter:
        offset = (page - 1) * per_page
        query = _catalog_book_query(
            author_id=author_id,
            series_id=series_id,
            tag_id=tag_id,
            publisher_id=publisher_id,
            language_code=language_code,
            rating_id=rating_id,
            book_format=book_format,
            filter_val=filter_val,
            show_hidden=show_hidden,
        )
        query = _join_sort(query, sort_context)
        total = query.with_entities(db.Books.id).order_by(None).distinct().count()
        if select_all:
            ids = [row[0] for row in query.with_entities(db.Books.id)
                   .order_by(*order).limit(per_page).all()]
            return _selection_response(ids, total)
        entries = query.order_by(*order).offset(offset).limit(per_page).all()
        entries = calibre_db.order_authors(entries, list_return=True, combined=True)
        return jsonify(_with_sort({"items": to_items(entries), "page": page,
                        "per_page": per_page, "total": total}, sort_context))

    # --- entity + read/unread path ---
    entity_filter = _build_entity_filter(author_id, series_id, tag_id, publisher_id, language_code,
                                         rating=rating_id, book_format=book_format)
    read_filter = (_build_read_filter(filter_val)
                   if filter_val in ("read", "unread", "in_progress",
                                     "did_not_finish", "on_hold") else True)

    if entity_filter is True and read_filter is True:
        db_filter = True
    elif entity_filter is True:
        db_filter = read_filter
    elif read_filter is True:
        db_filter = entity_filter
    else:
        db_filter = and_(entity_filter, read_filter)

    # series_join is needed when order references db.Series.name (authaz/authza)
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
        True, config.config_read_column, *series_join, *custom_join,
        ids_only=select_all,
        **listing_options,
    )
    if select_all:
        return _selection_response(entries, pagination.total_count)
    return jsonify(_with_sort({
        "items": to_items(entries),
        "page": pagination.page,
        "per_page": pagination.per_page,
        "total": pagination.total_count,
    }, sort_context))


@api_v1.route("/library/global")
@login_required_if_no_ano
def list_global_library():
    """List the global archive, including recent books absent from My Library.

    ``filter=not_in_my_library&sort=new`` is the discovery view for curated
    accounts. It never auto-adds a book.
    """
    if (not current_user.is_authenticated or current_user.is_anonymous
            or not current_user.role_browse_global()):
        return jsonify({
            "error": {"code": "forbidden", "message": "Global library access required"}
        }), 403
    page = max(1, request.args.get("page", 1, type=int))
    per_page = max(1, min(200, request.args.get(
        "per_page", config.config_books_per_page, type=int
    )))
    sort_context = _sort_context(request.args.get("sort", "new"))
    order = sort_context["order"]
    custom_join = sort_context["join"]
    term = (request.args.get("search") or "").strip()
    filter_name = request.args.get("filter", "all")
    if filter_name not in ("all", "not_in_my_library"):
        return jsonify({
            "error": {
                "code": "invalid_filter",
                "message": "filter must be 'all' or 'not_in_my_library'",
            }
        }), 400
    filters = []
    if filter_name == "not_in_my_library":
        filters.append(user_library.global_missing_filter(
            current_user, cdb=calibre_db
        ))
    if term:
        like = "%" + term + "%"
        filters.append(or_(
            func.lower(db.Books.title).ilike(func.lower(like)),
            db.Books.authors.any(func.lower(db.Authors.name).ilike(func.lower(like))),
            db.Books.series.any(func.lower(db.Series.name).ilike(func.lower(like))),
        ))
    global_filter = and_(*filters) if filters else True
    series_join = (
        db.books_series_link,
        db.Books.id == db.books_series_link.c.book,
        db.Series,
    )
    entries, _random, pagination = calibre_db.fill_indexpage(
        page, per_page, db.Books, global_filter, order,
        True, config.config_read_column, *series_join, *custom_join,
        allow_show_global=True,
    )
    member_ids = set()
    personal_library_mode = (
        user_library.mode_for_user(current_user)
        == constants.LIBRARY_MODE_PERSONAL
    )
    if personal_library_mode:
        page_ids = [int(getattr(entry, "Books", entry).id) for entry in entries]
        member_ids = {int(row[0]) for row in (
            ub.session.query(ub.UserLibraryBook.book_id)
            .filter(ub.UserLibraryBook.user_id == int(current_user.id),
                    ub.UserLibraryBook.book_id.in_(page_ids)).all()
        )}
    items = _rows_to_items(entries)
    for item in items:
        item["in_my_library"] = (
            not personal_library_mode
            or item["id"] in member_ids
        )
    return jsonify(_with_sort({
        "items": items,
        "page": pagination.page,
        "per_page": pagination.per_page,
        "total": pagination.total_count,
        "library_mode": user_library.mode_for_user(current_user),
        "filter": filter_name,
    }, sort_context))


@api_v1.route("/books/<int:book_id>")
@login_required_if_no_ano
def book_detail(book_id):
    # A global-library card is a valid deep link for a curator even when the
    # book is outside their selection. Bypass only the membership predicate;
    # language/content restrictions still flow through common_filters().
    allow_show_global = _can_browse_global()
    result = calibre_db.get_book_read_archived(
        book_id, config.config_read_column,
        allow_show_archived=True, allow_show_hidden=True,
        allow_show_global=allow_show_global,
        allow_public_shelf_books=True,
    )
    if not result:
        return jsonify({"error": {"code": "not_found", "message": "Book not found"}}), 404

    book, read_status, is_archived = result

    # The detail lookup already applied every content restriction. Only a
    # book on a currently public shelf can extend access beyond membership.
    accessible_via_public_shelf = calibre_db.session.query(db.Books.id).filter(
        db.Books.id == book_id,
        db.public_shelf_book_filter(ub.session, calibre_db.session),
    ).first() is not None

    in_my_library = user_library.contains_book(current_user, book_id)

    # Enrich language objects with display name so serialize_book_detail stays pure
    for lang in getattr(book, "languages", None) or []:
        lang.language_name = isoLanguages.get_language_name(get_locale(), lang.lang_code)

    # Per-user favorite + hidden state (presence-based rows). Anonymous/guest
    # sessions have no real id, so they simply read back as not-favorited/hidden.
    show_personal_state = in_my_library or accessible_via_public_shelf
    favorited = hidden = False
    annotation_count = 0
    kosync_progress = None
    kosync_progress_timestamp = None
    kosync_progress_created_at = None
    if show_personal_state and current_user.is_authenticated and not current_user.is_anonymous:
        uid = int(current_user.id)
        favorited = (ub.session.query(ub.FavoriteBook)
                     .filter(ub.FavoriteBook.user_id == uid, ub.FavoriteBook.book_id == book_id)
                     .first() is not None)
        hidden = (ub.session.query(ub.UserHiddenBook)
                  .filter(ub.UserHiddenBook.user_id == uid, ub.UserHiddenBook.book_id == book_id)
                  .first() is not None)
        annotation_count = count_user_annotations(uid, book_id, session=ub.session)
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
    effective_read_status = "unread"
    if show_personal_state:
        effective_read_status = read_statuses_for_books(
            ((book_id, read_status),), config.config_read_column,
            current_user).get(book_id, "unread")
    read = show_personal_state and effective_read_status == "finished"
    # Sync-driven "currently reading" tri-state (fork #634). The classic detail
    # page renders this marker off read_status_raw == STATUS_IN_PROGRESS; the SPA
    # book page never received the flag, so the badge was missing in the new UI.
    # Derive it from the shared helper so both surfaces stay in agreement.
    in_progress = show_personal_state and effective_read_status == "in_progress"
    body = serialize_book_detail(
        book,
        read=read,
        archived=in_my_library and bool(is_archived),
        favorited=favorited,
        hidden=hidden,
        in_progress=in_progress,
        read_status=effective_read_status,
        annotation_count=annotation_count,
        custom_column_definitions=_detail_custom_columns(),
        original_filename=_original_filename(book_id),
        cover_override=user_cover.override_for_user(_real_user_id(), book_id),
    )
    body["in_my_library"] = in_my_library
    body["accessible_via_public_shelf"] = accessible_via_public_shelf
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


@api_v1.route("/books/<int:book_id>/read-status", methods=["POST"])
@login_required_if_no_ano
def set_book_read_status(book_id):
    """Set an explicit per-user status while preserving legacy boolean writes."""
    data = request.get_json(silent=True)
    status = data.get("status") if isinstance(data, dict) else None
    if status not in ("unread", "finished", "in_progress",
                      "did_not_finish", "on_hold"):
        return jsonify({"error": {"code": "invalid_read_status",
                                   "message": "Invalid reading status"}}), 400
    if _real_user_id() is None:
        return jsonify({"error": {"code": "forbidden",
                                   "message": "A user account is required"}}), 403

    visible = calibre_db.get_book_read_archived(
        book_id, config.config_read_column,
        allow_show_archived=True, allow_show_hidden=True,
        allow_show_global=_can_browse_global(),
        allow_public_shelf_books=True,
    )
    if not visible:
        return jsonify({"error": {"code": "not_found", "message": "Book not found"}}), 404
    if not user_library.contains_book(current_user, book_id):
        return jsonify({"error": {"code": "forbidden",
                                   "message": "Book is not in your library"}}), 403

    message = set_explicit_book_read_status(book_id, status)
    if message:
        return jsonify({"error": {"code": "read_status_failed",
                                   "message": str(message)}}), 500
    return jsonify({"status": status})
