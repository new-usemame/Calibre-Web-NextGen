# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Kobo `KoboReader.sqlite` parser for H1 Phase 3.

Pure-Python module — no Flask, no `ub` direct imports — so it stays
testable in isolation. The blueprint at ``cps/annotations.py`` calls
:func:`parse_kobo_bookmarks` to extract annotation rows from an
uploaded sqlite file, then translates them into ``KoboAnnotationSync``
inserts.

See ``notes/KOBO-WEB-READER-ANNOTATIONS-DESIGN.md`` §7 for the parser
sketch this module implements.

Security shape:

* :func:`temporary_kobo_database` caps and validates uploads before
  either parser opens them. Both parsers validate the SQLite header
  again as defense-in-depth.
* We open the upload read-only via ``mode=ro`` URI and never write
  back to it.
* Annotation import reads only ``Bookmark``. Stranded-entitlement
  reconciliation reads only UUID-addressable volume rows from ``content``.
  Device columns are not treated as delivery provenance; ownership is an
  explicit administrator decision. All other device data is ignored.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from .annotation_colors import hex_for_bookmark_color

log = logging.getLogger(__name__)

# Kobo's Bookmark.Color encoding lives in cps/services/annotation_colors.py —
# one table, measured on real hardware (finding F-5769c9), shared with every
# other path that touches a highlight colour.

# SQLite database file magic — first 16 bytes of any valid SQLite 3.x file.
_SQLITE_MAGIC = b"SQLite format 3\x00"

MAX_KOBO_DATABASE_UPLOAD_BYTES = 100 * 1024 * 1024


class KoboUploadError(ValueError):
    def __init__(self, code, status_code):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class KoboContentDatabaseError(ValueError):
    pass


class KoboBookmarkDatabaseError(KoboUploadError):
    """The upload is SQLite, but not a readable Kobo annotation source."""

    def __init__(self, detail="Could not read the Kobo Bookmark table"):
        super().__init__("not_kobo", 400)
        self.detail = detail
        self.args = (detail,)


@dataclass(frozen=True)
class ParsedBookmark:
    """One row from a Kobo Bookmark table, plus the bits we derive
    from it for the H1 model. ``volume_id`` is the device's
    book-id (typically the EPUB UUID); the caller maps it to a CW
    ``books.id`` separately."""

    bookmark_id: Optional[str]     # Bookmark.BookmarkID (UUID)
    volume_id: Optional[str]       # Bookmark.VolumeID — match against Books.uuid
    content_id: Optional[str]      # Bookmark.ContentID — chapter pointer
    start_container_path: Optional[str]
    start_container_child_index: Optional[int]
    start_offset: Optional[int]
    end_container_path: Optional[str]
    end_container_child_index: Optional[int]
    end_offset: Optional[int]
    text: Optional[str]            # Bookmark.Text (the highlighted passage)
    annotation: Optional[str]      # Bookmark.Annotation (user's typed note)
    context_string: Optional[str]
    chapter_progress: Optional[float]
    color: Optional[str]           # canonical wire hex (e.g. '#A0A0A0'), or
                                   # None when Bookmark.Color is absent or is
                                   # an index the measured table doesn't cover
    hidden: bool
    date_created: Optional[str]    # ISO-8601 strings as stored by Kobo
    date_modified: Optional[str]
    annotation_type: Optional[str] = None
    # Bookmark.Type verbatim — the device's own word, normally "highlight".
    # None when the column is absent (older firmware) so a missing value is
    # never confused with a device that said something.


@dataclass(frozen=True)
class ParsedKoboBook:
    uuid: str
    title: str
    author: Optional[str] = None
    has_isbn: Optional[bool] = None
    file_size: Optional[int] = None
    synced: bool = False


@dataclass(frozen=True)
class KoboContentScan:
    books: tuple[ParsedKoboBook, ...]
    volume_rows: int
    skipped_invalid: int
    skipped_preview: int
    skipped_unclassified: int


@contextmanager
def temporary_kobo_database(upload, content_length=0,
                            max_bytes=MAX_KOBO_DATABASE_UPLOAD_BYTES):
    """Copy one uploaded Kobo database to a capped temporary file."""
    if not upload or not upload.filename:
        raise KoboUploadError("no_file", 400)
    if content_length and content_length > max_bytes:
        raise KoboUploadError("too_large", 413)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".sqlite", delete=False) as tmp:
            tmp_path = tmp.name
            total = 0
            while True:
                chunk = upload.stream.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise KoboUploadError("too_large", 413)
                tmp.write(chunk)
        if not looks_like_sqlite(tmp_path):
            raise KoboUploadError("not_sqlite", 400)
        yield Path(tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError as error:
                log.warning("kobo_import: failed to remove temp %s: %s", tmp_path, error)


def parse_kobo_device_books(sqlite_path):
    """Return UUID-addressable top-level books for explicit human review."""
    path = Path(sqlite_path)
    if not looks_like_sqlite(path):
        raise KoboContentDatabaseError("Uploaded file is not a SQLite database")

    uri = "file:{}?mode=ro&immutable=1".format(path)
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as error:
        raise KoboContentDatabaseError("Could not open the device database") from error

    try:
        connection.execute("PRAGMA query_only = ON")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='content'"
        ).fetchone()
        if table is None:
            raise KoboContentDatabaseError("Device database has no content table")
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(content)").fetchall()
        }
        required = {"ContentID", "ContentType", "BookID", "Title"}
        if not required.issubset(columns):
            raise KoboContentDatabaseError(
                "Device content table lacks reconciliation columns")
        volume_rows = connection.execute("""
            SELECT COUNT(*) FROM content
            WHERE BookID IS NULL AND CAST(ContentType AS TEXT) = '6'
        """).fetchone()[0]
        if "Accessibility" not in columns:
            rows = []
            skipped_unclassified = volume_rows
        else:
            author_column = "Attribution" if "Attribution" in columns else "NULL"
            isbn_column = "ISBN" if "ISBN" in columns else "NULL"
            file_size_column = "___FileSize" if "___FileSize" in columns else "NULL"
            rows = connection.execute("""
                SELECT ContentID, Title, Accessibility, {author}, {isbn}, {file_size}
                FROM content
                WHERE BookID IS NULL AND CAST(ContentType AS TEXT) = '6'
            """.format(
                author=author_column,
                isbn=isbn_column,
                file_size=file_size_column,
            )).fetchall()
            skipped_unclassified = 0
    except sqlite3.Error as error:
        raise KoboContentDatabaseError("Could not read the device content table") from error
    finally:
        connection.close()

    books_by_uuid = {}
    ambiguous = set()
    skipped_preview = 0
    isbn_available = "ISBN" in columns
    for content_id, title, accessibility, author, isbn, file_size in rows:
        # Accessibility=-1 is Kobo's preview/sample state, not a full book
        # entitlement, so those rows are excluded rather than offered for a
        # tombstone. The hardware sample validating this exclusion contained
        # no genuine Kobo purchases; author, ISBN and file size remain human
        # evidence only and are never used as ownership rules.
        if str(accessibility).strip() == "-1":
            skipped_preview += 1
            continue
        try:
            content_uuid = str(uuid.UUID(str(content_id)))
        except (TypeError, ValueError, AttributeError):
            continue
        try:
            parsed_file_size = int(file_size) if file_size is not None else None
        except (TypeError, ValueError):
            parsed_file_size = None
        parsed_has_isbn = None
        if isbn_available:
            parsed_has_isbn = bool(str(isbn).strip()) if isbn is not None else False
        parsed = ParsedKoboBook(
            uuid=content_uuid,
            title=str(title or content_uuid),
            author=str(author).strip() if author else None,
            has_isbn=parsed_has_isbn,
            file_size=parsed_file_size,
        )
        existing = books_by_uuid.get(content_uuid)
        if existing is not None and existing != parsed:
            ambiguous.add(content_uuid)
            books_by_uuid.pop(content_uuid, None)
        elif content_uuid not in ambiguous:
            books_by_uuid[content_uuid] = parsed

    books = tuple(sorted(
        books_by_uuid.values(), key=lambda book: (book.title.casefold(), book.uuid)))
    return KoboContentScan(
        books=books,
        volume_rows=volume_rows,
        skipped_invalid=(
            volume_rows - skipped_preview - skipped_unclassified - len(books)),
        skipped_preview=skipped_preview,
        skipped_unclassified=skipped_unclassified,
    )


def looks_like_sqlite(blob_or_path) -> bool:
    """Cheap magic-bytes check. Accepts either bytes (first 16+
    bytes of the file) or a path-like the caller wants us to read."""
    if isinstance(blob_or_path, (bytes, bytearray)):
        return bytes(blob_or_path[:16]) == _SQLITE_MAGIC
    p = Path(blob_or_path)
    if not p.is_file():
        return False
    try:
        with open(p, "rb") as fh:
            return fh.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def kobo_hidden_flag(value) -> bool:
    """Whether a ``Bookmark.Hidden`` value means the device deleted the row.

    Kobo declares the column ``Hidden BOOL NOT NULL DEFAULT 0`` and then writes
    the **strings** ``'true'``/``'false'`` into it. SQLite's ``BOOL`` carries
    NUMERIC affinity, but neither string converts to a number, so both are
    stored as TEXT. The declared type therefore reads like an integer while the
    stored value is a word — measured on firmware 4.45.23792, where
    ``typeof(Hidden)`` is ``text`` for all 31 rows.

    ``bool("false")`` is ``True``, so coercing the raw value marks **every** row
    on a real device as hidden and the recovery import skips all of them while
    still answering 200 with a success summary.

    Unrecognised values are treated as **not** hidden. An import is a recovery
    operation: restoring a row the user had deleted is a visible annoyance they
    can undo, whereas dropping one is the silent loss this function exists to
    prevent.
    """
    if isinstance(value, str):
        word = value.strip().casefold()
        if word in ("true", "1"):
            return True
        if word in ("false", "0", ""):
            return False
        log.warning(
            "kobo_import: unrecognised Bookmark.Hidden value %r; "
            "treating the row as visible so a recovery cannot silently drop it",
            value,
        )
        return False
    return bool(value)


def parse_kobo_bookmarks(sqlite_path: Path) -> Iterator[ParsedBookmark]:
    """Open ``sqlite_path`` read-only and yield every ``Bookmark`` row.

    Yields nothing if the file is not SQLite or does not exist (the upload
    boundary rejects those before calling this parser). A valid empty Bookmark
    table yields nothing. A missing or unreadable Bookmark table raises so the
    import cannot misreport a failed recovery as a successful zero-row scan.
    """
    if not isinstance(sqlite_path, Path):
        sqlite_path = Path(sqlite_path)
    if not looks_like_sqlite(sqlite_path):
        log.warning("kobo_import: %s is not a SQLite file", sqlite_path)
        return

    # mode=ro + immutable=1 — never touch the file, no journal, no
    # write attempts. Even if the user maliciously crafted a sqlite
    # with triggers, we can't fire them in read-only mode.
    uri = f"file:{sqlite_path}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as e:
        log.warning("kobo_import: cannot open %s read-only: %s", sqlite_path, e)
        raise KoboBookmarkDatabaseError("Could not open the Kobo database read-only") from e

    try:
        # Keep the connection non-writing at both layers. ``mode=ro`` is the
        # filesystem boundary; query_only also prevents a future refactor from
        # accidentally issuing a write through this already-open connection.
        conn.execute("PRAGMA query_only = ON")
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='Bookmark'"
        )
        if cur.fetchone() is None:
            raise KoboBookmarkDatabaseError("Kobo database has no Bookmark table")

        # Bookmark.Type is the device's own word for what a row is
        # (normally "highlight" or "dogear"). Selected conditionally: naming a
        # column an older firmware lacks would fail the whole query and lose
        # every annotation on the device, which is a far worse outcome than
        # importing them untyped.
        has_type = any(
            row[1] == "Type"
            for row in conn.execute("PRAGMA table_info(Bookmark)").fetchall()
        )
        rows = conn.execute("""
            SELECT
                BookmarkID, VolumeID, ContentID,
                StartContainerPath, StartContainerChildIndex, StartOffset,
                EndContainerPath, EndContainerChildIndex, EndOffset,
                Text, Annotation, Color, ContextString,
                ChapterProgress, DateCreated, DateModified, Hidden, {type_column}
            FROM Bookmark
        """.format(type_column="Type" if has_type else "NULL")).fetchall()
    except sqlite3.DatabaseError as e:
        log.warning("kobo_import: SQL error on %s: %s", sqlite_path, e)
        raise KoboBookmarkDatabaseError() from e
    finally:
        conn.close()

    for r in rows:
        (bm_id, volume_id, content_id,
         sp, sci, so, ep, eci, eo,
         text, annotation, color, ctx,
         chapter_progress, dcreated, dmod, hidden, bm_type) = r
        yield ParsedBookmark(
            bookmark_id=bm_id,
            volume_id=volume_id,
            content_id=content_id,
            start_container_path=sp,
            start_container_child_index=sci,
            start_offset=so,
            end_container_path=ep,
            end_container_child_index=eci,
            end_offset=eo,
            text=text,
            annotation=annotation,
            context_string=ctx,
            chapter_progress=chapter_progress,
            # An unrecognised Color yields None — "unknown" — never a
            # specific colour. A default here is what made every greyscale
            # device's highlights indistinguishable from real yellow ones.
            color=hex_for_bookmark_color(color),
            # Stored verbatim, not derived. The device's vocabulary and the
            # live PATCH path's `payload["type"]` are the same word — the
            # KOReader plugin both writes `Type = "highlight"` and selects
            # `WHERE Type = 'highlight'` — so preserving it keeps the two
            # writers to this column speaking one language instead of
            # inventing a third (the mistake annotation_colors.py exists to
            # undo for highlight_color).
            annotation_type=(bm_type if isinstance(bm_type, str) and bm_type else None),
            hidden=kobo_hidden_flag(hidden),
            date_created=dcreated,
            date_modified=dmod,
        )
