# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Builder for synthetic KoboReader.sqlite fixtures used by the H1
import path tests.

Real device backups contain personal reading history (PII). These
fixtures recreate the Bookmark table schema exactly per
``notes/KOBO-PROTOCOL-REFERENCE.md`` §10.1 without shipping anyone's
data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


BOOKMARK_DDL = """
CREATE TABLE Bookmark (
    BookmarkID TEXT PRIMARY KEY,
    VolumeID TEXT,
    ContentID TEXT,
    StartContainerPath TEXT,
    StartContainerChildIndex INTEGER,
    StartOffset INTEGER,
    EndContainerPath TEXT,
    EndContainerChildIndex INTEGER,
    EndOffset INTEGER,
    Text TEXT,
    Annotation TEXT,
    Color INTEGER,
    ContextString TEXT,
    ChapterProgress REAL,
    DateCreated TEXT,
    DateModified TEXT,
    Hidden BOOL NOT NULL DEFAULT 0
)
"""

BOOKMARK_DDL_WITH_TYPE = BOOKMARK_DDL.replace(
    "Hidden BOOL NOT NULL DEFAULT 0\n)",
    "Hidden BOOL NOT NULL DEFAULT 0,\n    Type TEXT\n)",
)


def build_synthetic_kobo_db(
    path: Path,
    book_uuid: str = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04",
    extra_book_uuid: str = "11111111-2222-3333-4444-555555555555",
    sideloaded_uri: str = "file:///mnt/onboard/sideloaded.epub",
) -> Path:
    """Write a KoboReader.sqlite with a mix of bookmarks the H1 import
    path needs to handle:

    * 3 highlights on a UUID-tagged book (matches CW library)
    * 1 highlight with a typed note (Annotation populated)
    * highlights spanning three distinct ``Bookmark.Color`` codes
      (0 = yellow, 1 = pink, 2 = blue — the measured mapping, F-5769c9)
    * 1 highlight on a sideloaded book (``file://`` URI) — must be skipped
    * 1 hidden highlight (Hidden=1) — must be skipped
    * 1 highlight on an unrelated UUID (no CW book) — must be skipped
    """
    if not isinstance(path, Path):
        path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(BOOKMARK_DDL)

    rows = [
        # bm_id, volume_id, content_id, sp, sci, so, ep, eci, eo, text, ann, color, ctx, prog, dcreated, dmod, hidden
        # Hidden is the STRING 'true'/'false' — that is what a real Kobo writes,
        # measured on firmware 4.45.23792 where typeof(Hidden) is 'text' for every
        # row. The column is declared BOOL (NUMERIC affinity) but neither word
        # converts to a number, so SQLite keeps them as TEXT. Do NOT "fix" these
        # back to 0/1: bool('false') is True, and a fixture using integers cannot
        # detect the coercion bug that silently skipped every row on import.
        ("bm-001", book_uuid, f"{book_uuid}!!chapter1.html",
         "span#kobo\\.1\\.1", -99, 0, "span#kobo\\.1\\.1", -99, 15,
         "All animals are equal.", None, 0, "... All animals are equal. But ...",
         0.01, "2026-01-01T10:00:00Z", "2026-01-01T10:00:00Z", "false"),
        ("bm-002", book_uuid, f"{book_uuid}!!chapter1.html",
         "span#kobo\\.1\\.2", -99, 0, "span#kobo\\.1\\.3", -99, 21,
         "Four legs good, two legs bad.", "my favorite line", 1,
         "Four legs good, two legs bad.", 0.024, "2026-01-01T10:05:00Z",
         "2026-01-01T10:05:00Z", "false"),
        ("bm-003", book_uuid, f"{book_uuid}!!chapter2.html",
         "span#kobo\\.2\\.1", -99, 8, "span#kobo\\.2\\.1", -99, 17,
         "Comrade Napoleon", None, 2, "...Comrade Napoleon is always right...",
         0.5, "2026-01-02T10:00:00Z", "2026-01-02T10:00:00Z", "false"),
        # sideloaded — must be skipped
        ("bm-004", sideloaded_uri, "sideloaded!!ch1.html",
         "span#kobo\\.4\\.1", -99, 0, "span#kobo\\.4\\.1", -99, 10,
         "sideloaded text", None, 0, None, 0.1,
         "2026-01-03T10:00:00Z", "2026-01-03T10:00:00Z", "false"),
        # hidden — must be skipped
        ("bm-005", book_uuid, f"{book_uuid}!!chapter1.html",
         "span#kobo\\.1\\.4", -99, 0, "span#kobo\\.1\\.4", -99, 30,
         "deleted on device", None, 0, None, 0.05,
         "2026-01-04T10:00:00Z", "2026-01-04T10:00:00Z", "true"),
        # unrelated UUID — must be skipped (no CW book matches)
        ("bm-006", extra_book_uuid, f"{extra_book_uuid}!!intro.html",
         "span#kobo\\.5\\.1", -99, 0, "span#kobo\\.5\\.1", -99, 12,
         "orphan highlight", None, 3, None, 0.0,
         "2026-01-05T10:00:00Z", "2026-01-05T10:00:00Z", "false"),
        # malformed: empty BookmarkID — must be skipped silently
        ("", book_uuid, None, None, None, None, None, None, None,
         "malformed", None, 0, None, None, None, None, "false"),
        # empty text — must be skipped (parser WHERE clause filters)
        ("bm-008", book_uuid, f"{book_uuid}!!chapter1.html",
         "span#kobo\\.1\\.7", -99, 0, "span#kobo\\.1\\.7", -99, 5,
         "", None, 0, None, 0.0,
         "2026-01-06T10:00:00Z", "2026-01-06T10:00:00Z", "false"),
    ]

    conn.executemany(
        "INSERT INTO Bookmark VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


def build_kobo_db_with_colors(
    path: Path,
    colors,
    book_uuid: str = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04",
) -> Path:
    """Write a KoboReader.sqlite holding one highlight per entry in
    ``colors``, all on ``book_uuid``.

    ``colors`` is an iterable of raw ``Bookmark.Color`` values — ints, but
    deliberately also ``None`` and out-of-range codes, because those are the
    cases the colour lookup has to answer "unknown" to rather than inventing a
    colour. Bookmark ids are ``clr-<index>`` so a test can address each row
    without depending on the canonical fixture's counts.
    """
    if not isinstance(path, Path):
        path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(BOOKMARK_DDL)
    rows = []
    for index, color in enumerate(colors):
        rows.append((
            f"clr-{index}", book_uuid, f"{book_uuid}!!chapter1.html",
            "span#kobo\\.1\\.{}".format(index), -99, 0,
            "span#kobo\\.1\\.{}".format(index), -99, 10,
            f"passage {index}", None, color, None, 0.1,
            "2026-01-01T10:00:00Z", "2026-01-01T10:00:00Z", 0,
        ))
    conn.executemany(
        "INSERT INTO Bookmark VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


def build_empty_sqlite_no_bookmark_table(path: Path) -> Path:
    """A valid SQLite file with no Bookmark table — exercises the
    schema-validation skip path."""
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE OtherTable (id INTEGER)")
    conn.commit()
    conn.close()
    return path


def build_not_sqlite(path: Path) -> Path:
    """Not a SQLite file at all — exercises the magic-bytes rejection."""
    path.write_bytes(b"This is not a sqlite file. " * 100)
    return path


def build_kobo_db_with_bookmark_type(
    path: Path,
    book_uuid: str = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04",
) -> Path:
    """A Bookmark table that carries the ``Type`` column, as current firmware does.

    ``build_synthetic_kobo_db`` above deliberately omits ``Type`` — it models an
    older schema, and the importer must keep working there. This one models the
    schema a current device actually has, so the two together cover both sides of
    the capability check in ``parse_kobo_bookmarks``.

    ``dogear`` rows carry no ``Text`` on a real device and so cannot reach the
    importer, whose SELECT filters them out. One is included anyway, with text,
    precisely so a test can prove the importer stores whatever word the device
    used rather than assuming everything it sees is a highlight.
    """
    import sqlite3

    conn = sqlite3.connect(path)
    conn.executescript(BOOKMARK_DDL_WITH_TYPE)
    chapter1 = "{}!!chapter1.html".format(book_uuid)
    chapter2 = "{}!!chapter2.html".format(book_uuid)
    rows = [
        ("bt-001", book_uuid, chapter1, "span#kobo\\.1\\.1", -99, 0,
         "span#kobo\\.1\\.2", -99, 5, "a highlight", None, 0, "ctx", 0.1,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", 0, "highlight"),
        ("bt-002", book_uuid, chapter1, "span#kobo\\.2\\.1", -99, 0,
         "span#kobo\\.2\\.2", -99, 5, "with a note", "my note", 1, "ctx", 0.2,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", 0, "highlight"),
        ("bt-003", book_uuid, chapter2, "span#kobo\\.3\\.1", -99, 0,
         "span#kobo\\.3\\.2", -99, 5, "a dogear with text", None, 4, "ctx", 0.3,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", 0, "dogear"),
        ("bt-004", book_uuid, chapter2, "span#kobo\\.4\\.1", -99, 0,
         "span#kobo\\.4\\.2", -99, 5, "type is empty", None, 0, "ctx", 0.4,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", 0, ""),
    ]
    conn.executemany(
        "INSERT INTO Bookmark VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


def build_kobo_db_with_recovery_rows(
    path: Path,
    book_uuid: str = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04",
) -> Path:
    """Current-device shapes that the old ``Text`` SQL gate made invisible.

    A dogear has empty ``Text`` and identifies itself through ``Type``. A Kobo
    highlight with only a typed note keeps ``Type='highlight'`` and puts the
    user's writing in ``Annotation``. The final fully-empty row is a vacuity
    guard: widening the old gate must not turn a row with no annotation evidence
    into an imported annotation, but it must still be counted with a reason.
    """
    if not isinstance(path, Path):
        path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(BOOKMARK_DDL_WITH_TYPE)
    chapter = f"{book_uuid}!!chapter1.html"
    rows = [
        (
            "recover-dogear", book_uuid, chapter,
            "span#kobo\\.7\\.1", -99, 0, "span#kobo\\.7\\.1", -99, 0,
            "", None, None, None, 0.7,
            "2026-08-18T12:00:00Z", "2026-08-18T12:00:00Z", 0, "dogear",
        ),
        (
            "recover-note-only", book_uuid, chapter,
            "span#kobo\\.8\\.1", -99, 3, "span#kobo\\.8\\.1", -99, 3,
            "", "remember this", 4, "near the end", 0.8,
            "2026-08-18T12:01:00Z", "2026-08-18T12:02:00Z", 0, "highlight",
        ),
        (
            "recover-empty", book_uuid, chapter,
            None, None, None, None, None, None,
            "", None, None, None, None,
            "2026-08-18T12:03:00Z", "2026-08-18T12:03:00Z", 0, None,
        ),
    ]
    conn.executemany(
        "INSERT INTO Bookmark VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path
