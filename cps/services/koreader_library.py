# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The KOReader library: every book in a user's e-reader scope, described as files.

The KOReader plugin keeps one folder per server holding one file per book the
user's e-readers should carry (``cps/services/ereader_scope.py``). A book that
is not downloaded yet is a small placeholder EPUB; opening it downloads the real
file. This module builds the manifest the plugin reconciles that folder
against, one entry per book:

* ``filename`` follows the delivery naming rule, so a book sent to the device
  and the same book downloaded from the library land on the same name;
* ``checksum`` and ``size`` describe the exact bytes ``/file`` serves (the raw
  library file, as the delivery download does), computed from that file and
  cached by path, size and modification time;
* ``rev`` changes whenever what a placeholder shows (title, authors, series,
  cover) or the chosen file changes;
* ``read_status`` and ``progress`` are the ones the website shows, and
  ``last_read`` is when the reader last moved in the book on any device
  (absent for a book never read);
* ``author_sort`` is Calibre's sort form of the authors ("Pratchett, Terry").

The manifest as a whole carries a ``revision`` that changes when anything a
device acts on changes, so an unchanged library answers in one small response.
"""

import hashlib
import json
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .. import db, logger, ub
from . import device_delivery, ereader_scope

log = logger.create()

DEVICE_KIND = "koreader"
DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 500
_IN_CHUNK = 900

READ_UNREAD = "unread"
READ_READING = "reading"
READ_FINISHED = "finished"


class ScopeUnavailable(Exception):
    """The scope could not be computed reliably this time; retry later."""


@dataclass(frozen=True)
class FormatRow:
    book: int
    format: str
    name: str
    uncompressed_size: Optional[int]


@dataclass
class Manifest:
    revision: str
    scope: str
    scope_shelves: list
    shelves: list
    entries: list
    unsupported: int
    paths: dict = field(default_factory=dict)


def _utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_z(value):
    """``2026-09-23T12:00:00Z`` (UTC, second precision), or ``None``."""
    value = _utc(value)
    if value is None:
        return None
    return value.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chunks(ids):
    ordered = sorted(ids)
    for start in range(0, len(ordered), _IN_CHUNK):
        yield ordered[start:start + _IN_CHUNK]


def _digest(value, length):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


# ---------------------------------------------------------------------------
# Book facts, read in bulk from metadata.db
# ---------------------------------------------------------------------------

def _book_rows(cdb, ids):
    rows = {}
    for chunk in _chunks(ids):
        for row in (cdb.session.query(
                db.Books.id, db.Books.title, db.Books.author_sort, db.Books.timestamp,
                db.Books.last_modified, db.Books.has_cover, db.Books.path,
                db.Books.series_index)
                .filter(db.Books.id.in_(chunk))):
            rows[row.id] = row
    return rows


def _authors(cdb, ids):
    names = {}
    link = db.books_authors_link
    for chunk in _chunks(ids):
        for book_id, name in (cdb.session.query(link.c.book, db.Authors.name)
                              .join(db.Authors, db.Authors.id == link.c.author)
                              .filter(link.c.book.in_(chunk))):
            names.setdefault(book_id, []).append(name)
    return names


def _series(cdb, ids):
    names = {}
    link = db.books_series_link
    for chunk in _chunks(ids):
        for book_id, name in (cdb.session.query(link.c.book, db.Series.name)
                              .join(db.Series, db.Series.id == link.c.series)
                              .filter(link.c.book.in_(chunk))):
            names.setdefault(book_id, name)
    return names


def _formats(cdb, ids):
    formats = {}
    for chunk in _chunks(ids):
        for row in (cdb.session.query(db.Data.book, db.Data.format, db.Data.name,
                                      db.Data.uncompressed_size)
                    .filter(db.Data.book.in_(chunk))):
            formats.setdefault(row.book, []).append(
                FormatRow(row.book, row.format, row.name, row.uncompressed_size))
    return formats


# ---------------------------------------------------------------------------
# The user's own state: read status, position, shelves
# ---------------------------------------------------------------------------

def read_statuses(user, ids, *, session=None, cdb=None, read_column=None):
    """``{book_id: "unread"|"reading"|"finished"}`` as the website shows it.

    With a Calibre custom column designated as the read marker, "finished" is
    that column and "reading" is the sync tri-state underneath it, exactly as
    ``helper.book_in_progress_ids`` derives the "Currently reading" marker.
    """
    from .. import helper

    session = session or ub.session
    user_id = int(user.id)
    tri_state = {}
    for chunk in _chunks(ids):
        for book_id, status in (session.query(ub.ReadBook.book_id,
                                              ub.ReadBook.read_status)
                                .filter(ub.ReadBook.user_id == user_id,
                                        ub.ReadBook.book_id.in_(chunk))):
            tri_state[book_id] = status
    if read_column:
        finished = set()
        try:
            column = db.cc_classes[read_column]
            for chunk in _chunks(ids):
                finished.update(
                    book_id for book_id, value in
                    cdb.session.query(column.book, column.value)
                    .filter(column.book.in_(chunk)) if value)
        except (KeyError, AttributeError, IndexError):
            log.error("Custom Column No.%s does not exist in calibre database",
                      read_column)
        reading = helper.book_in_progress_ids(
            ((book_id, book_id in finished) for book_id in ids), True, user)
    else:
        finished = {book_id for book_id, status in tri_state.items()
                    if status == ub.ReadBook.STATUS_FINISHED}
        reading = {book_id for book_id, status in tri_state.items()
                   if status == ub.ReadBook.STATUS_IN_PROGRESS}
    statuses = {}
    for book_id in ids:
        if book_id in finished:
            statuses[book_id] = READ_FINISHED
        elif book_id in reading:
            statuses[book_id] = READ_READING
        else:
            statuses[book_id] = READ_UNREAD
    return statuses


def positions(user_id, ids, *, session=None):
    """``{book_id: (fraction 0..1, last_modified)}`` for books with a position.

    The resolved cross-device position (``helper.get_kosync_progress_display``
    reads the same carrier): KOReader, Kobo and the web reader all advance this
    one bookmark, furthest position first.
    """
    session = session or ub.session
    found = {}
    wanted = set(ids)
    for book_id, percent, modified in (
            session.query(ub.KoboReadingState.book_id,
                          ub.KoboBookmark.progress_percent,
                          ub.KoboBookmark.last_modified)
            .join(ub.KoboBookmark,
                  ub.KoboBookmark.kobo_reading_state_id == ub.KoboReadingState.id)
            .filter(ub.KoboReadingState.user_id == int(user_id),
                    ub.KoboBookmark.progress_percent.isnot(None))):
        if book_id not in wanted:
            continue
        fraction = max(0.0, min(1.0, float(percent) / 100.0))
        found[book_id] = (round(fraction, 4), modified)
    return found


def last_read_times(user_id, ids, bookmarks, *, session=None):
    """``{book_id: datetime}``: when the reader last moved in each book.

    The later of two clocks. ``bookmarks`` (from :func:`positions`) carries
    the shared bookmark's time, which only moves forward: a turn-back leaves
    it at the furthest place. The KOReader position carrier, keyed on the
    book id, is written by KOReader, the Kobo mirror and the web reader alike,
    and takes a device's own turn-back too.
    """
    from ..progress_syncing.models import KOSyncProgress

    session = session or ub.session
    latest = {book_id: _utc(modified)
              for book_id, (_fraction, modified) in bookmarks.items()
              if modified is not None}
    for chunk in _chunks(ids):
        for document, stamp in (
                session.query(KOSyncProgress.document, KOSyncProgress.timestamp)
                .filter(KOSyncProgress.user_id == int(user_id),
                        KOSyncProgress.document.in_([str(book_id) for book_id in chunk]))):
            stamp = _utc(stamp)
            if stamp is None:
                continue
            book_id = int(document)
            if latest.get(book_id) is None or stamp > latest[book_id]:
                latest[book_id] = stamp
    return latest


def _magic_shelves_for_collections(user_id, session):
    """The magic shelves Kobo shows as collections: ``kobo_sync`` ones, when
    the instance syncs magic shelves at all."""
    from .. import config
    if not getattr(config, "config_kobo_sync_magic_shelves", False):
        return []
    return (session.query(ub.MagicShelf)
            .filter(ub.MagicShelf.user_id == user_id,
                    ub.MagicShelf.kobo_sync.is_(True))
            .order_by(ub.MagicShelf.name, ub.MagicShelf.id)
            .all())


def shelf_catalogue(user, *, session=None):
    """Return ``(shelves, members, scope_shelves)``.

    ``shelves`` lists every regular shelf the user owns plus the magic shelves
    Kobo would show, as ``{id, name}``; ids use the collections snapshot's
    format (``uuid``, else the numeric id) so the two never disagree.
    ``members`` maps book id to the ids of the shelves it is on.
    ``scope_shelves`` lists the shelves that define a shelf-only scope.
    """
    from .. import magic_shelf

    session = session or ub.session
    user_id = int(user.id)
    shelves = []
    members = {}
    scope_shelves = []
    shelf_only = ereader_scope.shelf_only(user)

    regular = (session.query(ub.Shelf)
               .filter(ub.Shelf.user_id == user_id)
               .order_by(ub.Shelf.name, ub.Shelf.id).all())
    ids_by_row = {}
    for shelf in regular:
        shelf_id = shelf.uuid or str(shelf.id)
        ids_by_row[shelf.id] = shelf_id
        shelves.append({"id": shelf_id, "name": shelf.name or ""})
        if shelf_only and shelf.kobo_sync:
            scope_shelves.append({"id": shelf_id, "name": shelf.name or "",
                                  "kind": "shelf"})
    if ids_by_row:
        for book_id, shelf_row_id in (
                session.query(ub.BookShelf.book_id, ub.BookShelf.shelf)
                .filter(ub.BookShelf.shelf.in_(list(ids_by_row)))
                .order_by(ub.BookShelf.shelf, ub.BookShelf.order, ub.BookShelf.id)):
            members.setdefault(book_id, []).append(ids_by_row[shelf_row_id])

    for shelf in _magic_shelves_for_collections(user_id, session):
        shelf_id = shelf.uuid or "magic-%d" % shelf.id
        if shelf_only:
            scope_shelves.append({"id": shelf_id, "name": shelf.name or "",
                                  "kind": "magic"})
        try:
            book_ids, _count = magic_shelf.get_book_ids_for_magic_shelf(
                shelf.id, raise_on_error=True)
        except Exception as error:  # a broken rule must not take the list down
            log.warning("KOReader library: magic shelf %s left out of the "
                        "collections: %s", shelf.id, error)
            continue
        shelves.append({"id": shelf_id, "name": shelf.name or ""})
        for book_id in book_ids or ():
            members.setdefault(book_id, []).append(shelf_id)
    return shelves, members, scope_shelves


# ---------------------------------------------------------------------------
# Files: what /file serves, and its KOReader checksum
# ---------------------------------------------------------------------------

_CHECKSUMS = OrderedDict()
_CHECKSUMS_LOCK = threading.Lock()
_CHECKSUMS_MAX = 50000


def inside_library(library_root, *parts):
    """The real path of ``parts`` joined under the library, or ``None``.

    The final path is resolved (``..`` and symlinks) and must lie inside the
    library: checking only the book's folder would let a stored file name such
    as ``../../x`` lead anywhere.
    """
    root = os.path.realpath(library_root)
    path = os.path.realpath(os.path.join(root, *[part or "" for part in parts]))
    try:
        if path == root or os.path.commonpath((root, path)) != root:
            return None
    except ValueError:
        return None
    return path


def library_file_path(library_root, book_path, fmt_row):
    """Absolute path of one format file, or ``None`` if it escapes the library."""
    return inside_library(library_root, book_path,
                          "%s.%s" % (fmt_row.name, fmt_row.format.lower()))


def file_facts(path):
    """``(size, koreader_partial_md5)`` of a file, or ``(None, None)``."""
    from ..progress_syncing.checksums.koreader import calculate_koreader_partial_md5

    try:
        stat = os.stat(path)
    except OSError:
        return None, None
    key = (path, stat.st_size, stat.st_mtime_ns)
    with _CHECKSUMS_LOCK:
        checksum = _CHECKSUMS.get(key)
        if checksum is not None:
            _CHECKSUMS.move_to_end(key)
            return stat.st_size, checksum
    try:
        checksum = calculate_koreader_partial_md5(path)
    except OSError:
        checksum = None
    if checksum:
        with _CHECKSUMS_LOCK:
            _CHECKSUMS[key] = checksum
            while len(_CHECKSUMS) > _CHECKSUMS_MAX:
                _CHECKSUMS.popitem(last=False)
    return stat.st_size, checksum


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------

def book_rev(row, authors, series, fmt_row):
    """What a placeholder shows, plus which file stands behind it."""
    return _digest([
        row.title, authors, series,
        row.series_index if series else None,
        bool(row.has_cover), iso_z(row.last_modified) if row.last_modified else None,
        fmt_row.format.upper(), fmt_row.name, fmt_row.uncompressed_size,
    ], 16)


def _describe(ids, *, user, cdb, session, read_column, members):
    """Manifest entries (without size/checksum) for ``ids``, by book id."""
    rows = _book_rows(cdb, ids)
    authors = _authors(cdb, ids)
    series = _series(cdb, ids)
    formats = _formats(cdb, ids)
    present = [book_id for book_id in sorted(ids) if book_id in rows]
    statuses = read_statuses(user, present, session=session, cdb=cdb,
                             read_column=read_column)
    where = positions(user.id, present, session=session)
    last_read = last_read_times(user.id, present, where, session=session)
    entries = []
    paths = {}
    unsupported = 0
    for book_id in present:
        row = rows[book_id]
        chosen = device_delivery.select_device_format(
            DEVICE_KIND, formats.get(book_id, ()))
        if chosen is None:
            unsupported += 1
            continue
        book_series = series.get(book_id)
        book_authors = authors.get(book_id, [])
        position = where.get(book_id)
        entry = {
            "book_id": book_id,
            "title": row.title or "",
            "authors": book_authors,
            "series": book_series,
            "series_index": (float(row.series_index)
                             if book_series and row.series_index is not None
                             else None),
            "filename": device_delivery._delivery_filename(book_id, chosen),
            "format": chosen.format.upper(),
            "size": None,
            "checksum": None,
            "rev": book_rev(row, book_authors, book_series, chosen),
            "read_status": statuses.get(book_id, READ_UNREAD),
            "progress": position[0] if position else None,
            "shelves": list(members.get(book_id, ())),
            "added": iso_z(row.timestamp),
        }
        if row.author_sort:
            entry["author_sort"] = row.author_sort
        if last_read.get(book_id) is not None:
            entry["last_read"] = iso_z(last_read[book_id])
        entries.append(entry)
        paths[book_id] = (row.path, chosen)
    return entries, paths, unsupported


def _revision(scope, scope_shelves, shelves, entries):
    return _digest({
        "scope": scope,
        "scope_shelves": scope_shelves,
        "shelves": shelves,
        "books": [[entry["book_id"], entry["rev"], entry["filename"],
                   entry["read_status"], entry["progress"], entry["shelves"],
                   entry.get("author_sort"), entry.get("last_read")]
                  for entry in entries],
    }, 24)


def build_manifest(user, *, cdb=None, session=None, read_column=None):
    """Describe every book an e-reader of ``user`` should hold.

    The request must be acting as ``user`` (``current_user``): magic shelves
    evaluate their rules for the current user.
    """
    if cdb is None:
        from .. import calibre_db as cdb
    if read_column is None:
        from .. import config
        read_column = getattr(config, "config_read_column", 0)
    session = session or ub.session
    cdb.ensure_session()
    scope = ereader_scope.membership(user, session=session)
    if not scope.reliable:
        raise ScopeUnavailable(
            "One of the magic shelves that choose this device's books could "
            "not be read")
    ids = ereader_scope.held_book_ids(user, cdb=cdb, session=session, scope=scope)
    shelves, members, scope_shelves = shelf_catalogue(user, session=session)
    entries, paths, unsupported = _describe(
        ids, user=user, cdb=cdb, session=session, read_column=read_column,
        members=members)
    return Manifest(
        revision=_revision(scope.mode, scope_shelves, shelves, entries),
        scope=scope.mode,
        scope_shelves=scope_shelves,
        shelves=shelves,
        entries=entries,
        unsupported=unsupported,
        paths=paths,
    )


def describe_book(user, book_id, *, cdb=None, session=None, read_column=None):
    """The manifest entry for one visible book, whether or not it is in scope.

    Returns ``(entry, (book_path, format_row))`` or ``(None, None)`` when the
    book has no format KOReader reads.
    """
    if cdb is None:
        from .. import calibre_db as cdb
    if read_column is None:
        from .. import config
        read_column = getattr(config, "config_read_column", 0)
    session = session or ub.session
    cdb.ensure_session()
    # Shelves are left out: neither a placeholder nor a file depends on them,
    # and evaluating magic shelves once per file request would be wasted work.
    entries, paths, _unsupported = _describe(
        {int(book_id)}, user=user, cdb=cdb, session=session,
        read_column=read_column, members={})
    if not entries:
        return None, None
    return entries[0], paths[entries[0]["book_id"]]


def fill_file_facts(entries, paths, library_root):
    """Add the served file's ``size`` and ``checksum`` to each entry in place."""
    for entry in entries:
        book_path, fmt_row = paths[entry["book_id"]]
        if library_root is None:
            entry["size"] = fmt_row.uncompressed_size
            continue
        path = library_file_path(library_root, book_path, fmt_row)
        if path is None:
            continue
        entry["size"], entry["checksum"] = file_facts(path)


def set_read_status(user, book_id, status):
    """Apply a read status the reader chose on the device.

    "finished" and "unread" are exactly the website's mark read / mark unread
    (``helper.edit_book_read_status``); marking unread restarts the book on
    every device, as it does from the website (#683). "reading" marks the book
    currently being read without touching the position. The request must be
    acting as ``user``. Returns ``""`` or a problem description.
    """
    from .. import config, helper

    if status == READ_FINISHED:
        return helper.edit_book_read_status(book_id, True)
    if status == READ_UNREAD:
        return helper.edit_book_read_status(book_id, False)

    from ..progress_syncing.protocols.kosync import _ensure_visible_reading_state
    read_column = getattr(config, "config_read_column", 0)
    if read_column:
        problem = _clear_custom_read_column(book_id, read_column)
        if problem:
            return problem
    user_id = int(user.id)
    row = (ub.session.query(ub.ReadBook)
           .filter(ub.ReadBook.user_id == user_id, ub.ReadBook.book_id == book_id)
           .first())
    if row is None:
        row = ub.ReadBook(user_id=user_id, book_id=book_id,
                          read_status=ub.ReadBook.STATUS_UNREAD,
                          times_started_reading=0)
        ub.session.add(row)
    now = datetime.now(timezone.utc)
    if row.read_status != ub.ReadBook.STATUS_IN_PROGRESS:
        row.read_status = ub.ReadBook.STATUS_IN_PROGRESS
        row.times_started_reading = (row.times_started_reading or 0) + 1
        row.last_time_started_reading = now
    row.last_modified = now
    # The reading-state graph is what carries the change to a Kobo and to the
    # book page, as for every other status writer.
    _ensure_visible_reading_state(row, user_id, book_id)
    ub.session_commit("KOReader marked book {} as reading".format(book_id))
    return ""


def _clear_custom_read_column(book_id, read_column):
    """Un-finish a book in the Calibre read column, leaving its position alone."""
    from sqlalchemy.exc import InvalidRequestError, OperationalError
    from .. import calibre_db
    try:
        book = calibre_db.get_filtered_book(book_id, True)
        values = getattr(book, "custom_column_" + str(read_column))
        if len(values) and values[0].value:
            values[0].value = False
            calibre_db.session.commit()
    except (KeyError, AttributeError, IndexError):
        log.error("Custom Column No.%s does not exist in calibre database", read_column)
        return "Custom Column No.{} does not exist in calibre database".format(read_column)
    except (OperationalError, InvalidRequestError) as error:
        calibre_db.session.rollback()
        log.error("Read status could not be set: %s", error)
        return "Read status could not be set"
    return ""


def encode_cursor(revision, last_book_id):
    return "%s:%d" % (revision, last_book_id)


def decode_cursor(cursor):
    """``(revision, last_book_id)``; raises ``ValueError`` when malformed."""
    revision, _sep, last = (cursor or "").partition(":")
    if (not revision or len(revision) > 64
            or any(ch not in "0123456789abcdef" for ch in revision)
            or not last.isascii() or not last.isdigit() or len(last) > 18):
        raise ValueError("invalid cursor")
    return revision, int(last)


def page(entries, *, after=None, limit=DEFAULT_PAGE_SIZE):
    """Entries after book id ``after``, at most ``limit``; and whether more follow."""
    start = 0
    if after is not None:
        start = next((index for index, entry in enumerate(entries)
                      if entry["book_id"] > after), len(entries))
    chunk = entries[start:start + limit]
    more = start + limit < len(entries)
    return chunk, more
