# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression: ``Bookmark.Hidden`` is a TEXT word, not an integer.

A real Kobo declares ``Hidden BOOL NOT NULL DEFAULT 0`` and then writes the
**strings** ``'true'``/``'false'``. ``BOOL`` carries NUMERIC affinity, but
neither word converts to a number, so SQLite stores both as TEXT. Measured on
Clara BW firmware 4.45.23792: ``typeof(Hidden)`` is ``text`` for all 31 rows,
30 of them ``'false'``.

``hidden=bool(hidden)`` therefore evaluated ``bool('false') is True`` and marked
**every** row on a real device as deleted. ``ingest_bookmarks`` skips hidden
rows, so a genuine ``KoboReader.sqlite`` upload imported nothing and still
answered HTTP 200 with a success summary — observed against the live server as
``{"total_seen": 31, "skipped_hidden": 31, "imported": 0}`` while 14 of those
rows were real annotations absent from the server.

The suite could not see it: ``tests/fixtures/kobo_reader_sqlite.py`` inserted
``0``/``1``, copying what the DDL implies rather than what the device writes,
and ``bool(0)`` is ``False``. These tests pin the device's own representation.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


DEVICE_DDL = (
    "CREATE TABLE Bookmark ("
    " BookmarkID TEXT NOT NULL,"
    " VolumeID TEXT NOT NULL,"
    " ContentID TEXT,"
    " StartContainerPath TEXT, StartContainerChildIndex INTEGER, StartOffset INTEGER,"
    " EndContainerPath TEXT, EndContainerChildIndex INTEGER, EndOffset INTEGER,"
    " Text TEXT, Annotation TEXT, Color INTEGER, ContextString TEXT,"
    " ChapterProgress REAL, DateCreated TEXT, DateModified TEXT,"
    " Hidden BOOL NOT NULL DEFAULT 0, Type TEXT,"
    " PRIMARY KEY (BookmarkID) )"
)


def _device_db(path: Path, hidden_values) -> Path:
    conn = sqlite3.connect(path)
    conn.execute(DEVICE_DDL)
    conn.executemany(
        "INSERT INTO Bookmark (BookmarkID, VolumeID, ContentID, Text, Hidden, Type)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (f"bm-{i}", "b3d1b38b-74fd-43b7-a796-996e5a6a8b04",
             "b3d1b38b-74fd-43b7-a796-996e5a6a8b04!!ch1.html",
             "a real highlight", value, "highlight")
            for i, value in enumerate(hidden_values)
        ],
    )
    conn.commit()
    conn.close()
    return path


@pytest.mark.unit
class TestHiddenIsATextWord:
    def test_device_stores_hidden_as_text_not_integer(self, tmp_path):
        """Pin the storage class itself — the premise the bug rested on."""
        path = _device_db(tmp_path / "d.sqlite", ["false", "true"])
        conn = sqlite3.connect(path)
        kinds = {row[0] for row in conn.execute("SELECT typeof(Hidden) FROM Bookmark")}
        conn.close()
        assert kinds == {"text"}, (
            "Kobo writes the words 'true'/'false' into a column declared BOOL. "
            "If this ever comes back as 'integer' the fixture has drifted from "
            "the device and can no longer detect the coercion bug."
        )

    def test_the_string_false_does_not_hide_a_row(self, tmp_path):
        from cps.services.kobo_import import parse_kobo_bookmarks

        path = _device_db(tmp_path / "d.sqlite", ["false"] * 30 + ["true"])
        rows = list(parse_kobo_bookmarks(path))

        assert len(rows) == 31
        hidden = [r for r in rows if r.hidden]
        assert len(hidden) == 1, (
            f"expected exactly one hidden row, got {len(hidden)} of 31 — "
            "bool('false') is True, which is what silently skipped every "
            "annotation on a real device import"
        )

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("false", False), ("true", True),
            ("False", False), ("TRUE", True),   # casefolded
            (" false ", False),                 # whitespace-tolerant
            (0, False), (1, True),              # integer form still works
            ("0", False), ("1", True),
            ("", False), (None, False),
            ("banana", False),                  # unknown -> visible, never dropped
        ],
    )
    def test_hidden_flag_coercion(self, value, expected):
        from cps.services.kobo_import import kobo_hidden_flag

        assert kobo_hidden_flag(value) is expected

    def test_an_unrecognised_value_keeps_the_row_recoverable(self, tmp_path):
        """A recovery import must never drop a row it cannot classify.

        Restoring something the user deleted is visible and undoable; dropping
        a real annotation is silent and permanent.
        """
        from cps.services.kobo_import import parse_kobo_bookmarks

        path = _device_db(tmp_path / "d.sqlite", ["something-new"])
        rows = list(parse_kobo_bookmarks(path))
        assert len(rows) == 1
        assert rows[0].hidden is False
