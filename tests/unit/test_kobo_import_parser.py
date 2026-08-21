# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the H1 Phase 3 ``cps.services.kobo_import`` module.

Coverage:

1. ``looks_like_sqlite`` accepts a real sqlite, rejects garbage.
2. Parser yields one ``ParsedBookmark`` per valid Bookmark row.
3. Hidden rows + empty-text rows + empty-BookmarkID rows are filtered.
4. Color integers map to the MEASURED wire hex (finding F-5769c9).
5. Multi-span highlight preserves both start + end fields.
6. Note (Annotation) field round-trips.
7. ``ContextString`` field round-trips for re-anchoring downstream.
8. Missing Bookmark table → empty iterator, no raise.
9. Non-SQLite file → empty iterator, no raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.kobo_reader_sqlite import (
    build_synthetic_kobo_db,
    build_empty_sqlite_no_bookmark_table,
    build_not_sqlite,
)


@pytest.mark.unit
class TestLooksLikeSqlite:
    def test_accepts_real_sqlite(self, tmp_path):
        from cps.services.kobo_import import looks_like_sqlite

        p = build_synthetic_kobo_db(tmp_path / "test.sqlite")
        assert looks_like_sqlite(p) is True

    def test_rejects_garbage_file(self, tmp_path):
        from cps.services.kobo_import import looks_like_sqlite

        p = build_not_sqlite(tmp_path / "garbage.sqlite")
        assert looks_like_sqlite(p) is False

    def test_rejects_missing_file(self, tmp_path):
        from cps.services.kobo_import import looks_like_sqlite

        assert looks_like_sqlite(tmp_path / "nonexistent.sqlite") is False

    def test_accepts_blob_form(self):
        from cps.services.kobo_import import looks_like_sqlite

        magic = b"SQLite format 3\x00" + b"trailing payload"
        assert looks_like_sqlite(magic) is True
        assert looks_like_sqlite(b"not even close") is False


@pytest.mark.unit
class TestParseKoboBookmarks:
    def test_yields_one_per_valid_row(self, tmp_path):
        from cps.services.kobo_import import parse_kobo_bookmarks

        p = build_synthetic_kobo_db(tmp_path / "k.sqlite")
        rows = list(parse_kobo_bookmarks(p))
        # Fixture has 6 valid-text rows (bm-001..006); bm-007 has empty
        # BookmarkID + bm-008 has empty Text — both filtered.
        bm_ids = {r.bookmark_id for r in rows}
        assert "bm-001" in bm_ids
        assert "bm-002" in bm_ids
        assert "bm-003" in bm_ids
        assert "bm-004" in bm_ids  # sideloaded — yielded; caller decides
        assert "bm-005" in bm_ids  # hidden — yielded with hidden=True; caller filters
        assert "bm-006" in bm_ids
        # malformed (empty BookmarkID) — filtered
        assert "" not in bm_ids
        # empty-Text — filtered at SQL level
        assert "bm-008" not in bm_ids

    def test_color_map_normalized(self, tmp_path):
        from cps.services.kobo_import import parse_kobo_bookmarks

        p = build_synthetic_kobo_db(tmp_path / "k.sqlite")
        rows = {r.bookmark_id: r for r in parse_kobo_bookmarks(p)}
        # The MEASURED mapping (Kobo Clara BW 4.45.23792, finding F-5769c9):
        # 0 yellow / 1 pink / 2 blue / 3 green / 4 grey. The parser stores the
        # wire hex the device itself sends and accepts; names are a read-time
        # projection, so this asserts what actually lands in the column.
        assert rows["bm-001"].color == "#F6F3B3"  # Color=0 -> yellow
        assert rows["bm-002"].color == "#E8AFCF"  # Color=1 -> pink, NOT red
        assert rows["bm-003"].color == "#B2E1E8"  # Color=2 -> blue, NOT green
        assert rows["bm-006"].color == "#C6E09E"  # Color=3 -> green, NOT blue

    def test_typed_note_round_trips(self, tmp_path):
        from cps.services.kobo_import import parse_kobo_bookmarks

        p = build_synthetic_kobo_db(tmp_path / "k.sqlite")
        rows = {r.bookmark_id: r for r in parse_kobo_bookmarks(p)}
        assert rows["bm-002"].annotation == "my favorite line"
        assert rows["bm-001"].annotation is None

    def test_context_string_round_trips(self, tmp_path):
        from cps.services.kobo_import import parse_kobo_bookmarks

        p = build_synthetic_kobo_db(tmp_path / "k.sqlite")
        rows = {r.bookmark_id: r for r in parse_kobo_bookmarks(p)}
        assert "All animals are equal" in rows["bm-001"].context_string

    def test_multi_span_preserves_end_fields(self, tmp_path):
        from cps.services.kobo_import import parse_kobo_bookmarks

        p = build_synthetic_kobo_db(tmp_path / "k.sqlite")
        rows = {r.bookmark_id: r for r in parse_kobo_bookmarks(p)}
        bm = rows["bm-002"]
        # Multi-span: start = kobo.1.2, end = kobo.1.3
        assert bm.start_container_path == "span#kobo\\.1\\.2"
        assert bm.end_container_path == "span#kobo\\.1\\.3"
        assert bm.start_offset == 0
        assert bm.end_offset == 21

    def test_hidden_flag_preserved(self, tmp_path):
        """Parser yields hidden rows; downstream code decides whether
        to skip. Pin that the flag is correctly surfaced."""
        from cps.services.kobo_import import parse_kobo_bookmarks

        p = build_synthetic_kobo_db(tmp_path / "k.sqlite")
        rows = {r.bookmark_id: r for r in parse_kobo_bookmarks(p)}
        assert rows["bm-005"].hidden is True
        assert rows["bm-001"].hidden is False

    def test_sideloaded_volume_id_preserved_as_uri(self, tmp_path):
        from cps.services.kobo_import import parse_kobo_bookmarks

        p = build_synthetic_kobo_db(tmp_path / "k.sqlite")
        rows = {r.bookmark_id: r for r in parse_kobo_bookmarks(p)}
        assert rows["bm-004"].volume_id.startswith("file:///")


@pytest.mark.unit
class TestParserEdgeCases:
    def test_no_bookmark_table_yields_empty(self, tmp_path):
        from cps.services.kobo_import import parse_kobo_bookmarks

        p = build_empty_sqlite_no_bookmark_table(tmp_path / "empty.sqlite")
        assert list(parse_kobo_bookmarks(p)) == []

    def test_not_sqlite_yields_empty(self, tmp_path):
        from cps.services.kobo_import import parse_kobo_bookmarks

        p = build_not_sqlite(tmp_path / "garbage.sqlite")
        assert list(parse_kobo_bookmarks(p)) == []

    def test_missing_file_yields_empty(self, tmp_path):
        from cps.services.kobo_import import parse_kobo_bookmarks

        assert list(parse_kobo_bookmarks(tmp_path / "nope.sqlite")) == []


@pytest.mark.unit
class TestBookmarkTypeIsRecovered:
    """F-7e418c: the importer never recorded what the device said a row WAS.

    `cps/services/annotation_sync/__init__.py` stores `payload["type"]` for an
    annotation arriving over the wire, so the same highlight recovered from
    KoboReader.sqlite used to land with `annotation_type` NULL while its live
    twin carried a value. Two writers to one column, disagreeing.

    The value is taken from `Bookmark.Type` verbatim rather than derived. That is
    not a guess about the device's vocabulary: the KOReader plugin both writes
    `Type = "highlight"` and selects `WHERE Type = 'highlight'`
    (koreader/plugins/cwasync.koplugin/kobo_sqlite_provider.lua), and the wire
    payload uses the same word. Deriving one instead would have invented a third
    vocabulary for this column — exactly what cps/services/annotation_colors.py
    exists to undo for highlight_color.
    """

    def test_the_device_word_is_stored_verbatim(self, tmp_path):
        from cps.services.kobo_import import parse_kobo_bookmarks
        from tests.fixtures.kobo_reader_sqlite import build_kobo_db_with_bookmark_type

        p = build_kobo_db_with_bookmark_type(tmp_path / "typed.sqlite")
        by_id = {r.bookmark_id: r for r in parse_kobo_bookmarks(p)}

        assert by_id["bt-001"].annotation_type == "highlight"
        assert by_id["bt-002"].annotation_type == "highlight"

    def test_a_word_that_is_not_highlight_is_not_rewritten_to_one(self, tmp_path):
        """Preserve, don't classify.

        A row whose Type is "dogear" must arrive as "dogear". Normalising it to
        "highlight" would be the same class of mistake as the colour table that
        answered "yellow" for every code it did not know.
        """
        from cps.services.kobo_import import parse_kobo_bookmarks
        from tests.fixtures.kobo_reader_sqlite import build_kobo_db_with_bookmark_type

        p = build_kobo_db_with_bookmark_type(tmp_path / "typed.sqlite")
        by_id = {r.bookmark_id: r for r in parse_kobo_bookmarks(p)}
        assert by_id["bt-003"].annotation_type == "dogear"

    def test_an_empty_type_becomes_none_not_an_empty_string(self, tmp_path):
        """"" and NULL both mean "the device did not say"."""
        from cps.services.kobo_import import parse_kobo_bookmarks
        from tests.fixtures.kobo_reader_sqlite import build_kobo_db_with_bookmark_type

        p = build_kobo_db_with_bookmark_type(tmp_path / "typed.sqlite")
        by_id = {r.bookmark_id: r for r in parse_kobo_bookmarks(p)}
        assert by_id["bt-004"].annotation_type is None

    def test_a_schema_without_the_type_column_still_imports_everything(self, tmp_path):
        """The capability check is the point.

        Naming a column an older firmware lacks would fail the whole query and
        lose EVERY annotation on that device — far worse than importing them
        untyped. The long-standing synthetic fixture has no Type column, so it is
        the older-schema case; assert both that it still yields its rows and that
        their type is None rather than a fabricated default.
        """
        from cps.services.kobo_import import parse_kobo_bookmarks
        from tests.fixtures.kobo_reader_sqlite import build_synthetic_kobo_db

        p = build_synthetic_kobo_db(tmp_path / "old.sqlite")
        rows = list(parse_kobo_bookmarks(p))
        assert len(rows) >= 6, "the older-schema import lost rows"
        assert {r.annotation_type for r in rows} == {None}

    def test_the_two_fixtures_really_differ(self, tmp_path):
        """Vacuity guard.

        If both fixtures lacked the Type column, every assertion above would pass
        while testing one branch twice.
        """
        import sqlite3

        from tests.fixtures.kobo_reader_sqlite import (
            build_kobo_db_with_bookmark_type,
            build_synthetic_kobo_db,
        )

        def columns(path):
            conn = sqlite3.connect(path)
            try:
                return {row[1] for row in conn.execute("PRAGMA table_info(Bookmark)")}
            finally:
                conn.close()

        assert "Type" in columns(build_kobo_db_with_bookmark_type(tmp_path / "new.sqlite"))
        assert "Type" not in columns(build_synthetic_kobo_db(tmp_path / "old.sqlite"))
