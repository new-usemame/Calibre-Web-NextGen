# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Integration tests for the H1 Phase 3 import path.

Exercises ``cps.annotations.ingest_bookmarks`` end-to-end against an
in-memory SQLAlchemy session — covers the full INSERT loop including
UUID resolution, orphan-skipping, hidden-row filtering, dedup against
``(user_id, annotation_id)``, and the JSON summary shape the endpoint
returns.

Coverage:

1. End-to-end ingest of the canonical synthetic fixture produces the
   expected counts: imported=3, skipped_orphan=2, skipped_hidden=1.
2. All H1 columns on each inserted row are populated.
3. Re-running the same import is idempotent — second pass counts as
   ``skipped_existing``.
4. Mixed UUID + sideloaded ``file://`` URIs split correctly into
   imported vs skipped_orphan.
5. Multi-user isolation — user A's import never resolves user B's
   existing rows.
6. Commit failure rolls back cleanly + reports imported=0.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.fixtures.kobo_reader_sqlite import (
    build_kobo_db_with_recovery_rows,
    build_synthetic_kobo_db,
)


OVERFLOWING_KOBO_CLOCKS = (
    pytest.param("9999-12-31T23:59:59-23:59", id="above-maxyear"),
    pytest.param("0001-01-01T00:00:00+23:59", id="below-minyear"),
)


@pytest.fixture
def memory_db(tmp_path, monkeypatch):
    """Same shape as the backup-feature fixture — full ub.Base schema
    in-memory + worker autostart disabled so the after_flush hook
    doesn't try to dispatch to a production-DB-bound thread."""
    from cps import ub, constants
    from cps.services import annotation_backup

    annotation_backup.reset_for_tests()
    monkeypatch.setattr(annotation_backup, "WORKER_AUTOSTART", False)

    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
    ub.Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine, future=True)
    session = Session()

    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    yield session, engine, tmp_path
    session.close()
    annotation_backup.reset_for_tests()


def _make_book_lookup(uuid_to_book_id: dict[str, int]):
    """Build a callable that maps Bookmark.VolumeID → fake Book
    object whose ``.id`` is what the production lookup would return.
    Unknown UUIDs return ``None`` to simulate "book not in library"."""
    def lookup(uuid):
        if not uuid or uuid not in uuid_to_book_id:
            return None
        return SimpleNamespace(id=uuid_to_book_id[uuid])
    return lookup


def _accounted(summary):
    return sum(summary[key] for key in (
        "imported", "updated", "skipped_existing", "skipped_orphan",
        "skipped_hidden", "skipped_empty", "skipped_invalid",
        "skipped_newer_server", "skipped_invalid_content_id", "failed",
    ))


@pytest.fixture
def synthetic_db(tmp_path):
    return build_synthetic_kobo_db(tmp_path / "kr.sqlite")


# ---------------------------------------------------------------------------
# 1 + 4. End-to-end ingest produces the expected counts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIngestCounts:
    def test_canonical_fixture_counts(self, memory_db, synthetic_db):
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        # Only the primary UUID maps to a CW book; the extra UUID +
        # the file:// URI are both orphans.
        book_lookup = _make_book_lookup({
            "b3d1b38b-74fd-43b7-a796-996e5a6a8b04": 348,
        })

        result = ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=book_lookup, commit=session.commit,
        )
        assert result["imported"] == 3, result
        assert result["skipped_hidden"] == 1, result
        assert result["skipped_orphan"] == 2, result    # bm-004 sideloaded + bm-006 unknown UUID
        assert result["skipped_existing"] == 0, result
        assert result["skipped_invalid"] == 1, result
        assert result["skipped_empty"] == 1, result
        assert result["total_seen"] == 8, result
        assert _accounted(result) == result["total_seen"]

    def test_inserted_rows_carry_full_h1_payload(self, memory_db, synthetic_db):
        from cps import ub
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        book_lookup = _make_book_lookup({
            "b3d1b38b-74fd-43b7-a796-996e5a6a8b04": 348,
        })
        ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=book_lookup, commit=session.commit,
        )

        # bm-002 has all the bells: multi-span, typed note, Color=1 (pink).
        row = session.query(ub.Annotation).filter_by(
            annotation_id="bm-002"
        ).one()
        assert row.user_id == 7
        assert row.book_id == 348
        assert row.highlighted_text == "Four legs good, two legs bad."
        assert row.highlight_color == "#E8AFCF"   # Color=1 is pink (F-5769c9)
        assert row.note_text == "my favorite line"
        assert row.start_container_path == "span#kobo\\.1\\.2"
        assert row.end_container_path == "span#kobo\\.1\\.3"
        assert row.start_offset == 0
        assert row.end_offset == 21
        assert row.source == "kobo"
        assert row.chapter_progress == 0.024

    def test_inserted_row_keeps_device_date_created(self, memory_db, synthetic_db):
        from cps import ub
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        ingest_bookmarks(
            synthetic_db,
            user_id=7,
            session=session,
            book_lookup=_make_book_lookup({
                "b3d1b38b-74fd-43b7-a796-996e5a6a8b04": 348,
            }),
            commit=session.commit,
        )

        row = session.query(ub.Annotation).filter_by(annotation_id="bm-002").one()
        assert row.created_at == datetime(2026, 1, 1, 10, 5, 0, 123000)

    def test_color_round_trips(self, memory_db, synthetic_db):
        """Device integer -> what lands in the column -> what the reader is told.

        This asserted ``bm-002 == "red"`` and ``bm-003 == "green"``, which was
        the importer's own lookup table restated back at itself — nothing round
        tripped and the name was aspirational. Both values were wrong against
        the hardware (finding F-5769c9): Color=1 is pink, Color=2 is blue, and
        Kobo has no red at all. Colour 4, the one a greyscale device writes for
        every highlight, is covered in
        tests/unit/test_kobo_highlight_colour_vocabulary.py because the
        canonical fixture does not carry it.
        """
        from cps import ub
        from cps.annotations import _data_json_row, ingest_bookmarks

        session, _, _ = memory_db
        book_lookup = _make_book_lookup({
            "b3d1b38b-74fd-43b7-a796-996e5a6a8b04": 348,
        })
        ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=book_lookup, commit=session.commit,
        )
        rows = {r.annotation_id: r for r in
                session.query(ub.Annotation).filter_by(user_id=7).all()}

        # Stored: the canonical wire hex the device itself uses.
        assert rows["bm-001"].highlight_color == "#F6F3B3"   # Color=0
        assert rows["bm-002"].highlight_color == "#E8AFCF"   # Color=1
        assert rows["bm-003"].highlight_color == "#B2E1E8"   # Color=2

        # Displayed: the name the reader renders. This is the half that was
        # missing — the old assertions never left the storage layer.
        displayed = {k: _data_json_row(v, None, None)["highlight_color"]
                     for k, v in rows.items()}
        assert displayed["bm-001"] == "yellow"
        assert displayed["bm-002"] == "pink"
        assert displayed["bm-003"] == "blue"


@pytest.mark.unit
class TestWireAndDatabaseSentinelEquivalence:
    BOOK_UUID = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"

    @staticmethod
    def _write_wire_row(session, monkeypatch):
        from cps import ub
        from cps.services.annotation_sync import (
            dispatch_annotation_sync,
            reset_registry_for_testing,
            set_remote_enqueue,
        )

        def commit():
            session.commit()
            return True

        monkeypatch.setattr(ub, "session", session)
        monkeypatch.setattr(ub, "session_commit", commit)
        reset_registry_for_testing()
        set_remote_enqueue(None)
        book = SimpleNamespace(
            id=348,
            uuid=TestWireAndDatabaseSentinelEquivalence.BOOK_UUID,
            title="Animal Farm",
        )
        user = SimpleNamespace(id=7)
        payload = {
            "id": "bm-001",
            "highlightedText": "All animals are equal.",
            "noteText": None,
            "highlightColor": "#F6F3B3",
            "type": "highlight",
            "clientLastModifiedUtc": "2026-01-01T10:00:00Z",
            "location": {"span": {
                "chapterFilename": "OEBPS/chapter1.html",
                "startPath": "span#kobo\\.1\\.1",
                "startChar": 0,
                "endPath": "span#kobo\\.1\\.1",
                "endChar": 15,
                "contextString": "... All animals are equal. But ...",
                "chapterProgress": 0.01,
            }},
        }

        assert dispatch_annotation_sync([payload], book, user) is True
        wire_row = session.query(ub.Annotation).filter_by(
            annotation_id="bm-001",
        ).one()
        assert wire_row.start_container_child_index is None
        assert wire_row.end_container_child_index is None
        return wire_row, commit

    def test_wire_delivered_annotation_is_already_present(
        self, memory_db, synthetic_db, monkeypatch,
    ):
        """NULL on the wire and -99 in SQLite describe one KoboSpan selector."""
        from cps import ub
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        _, commit = self._write_wire_row(session, monkeypatch)

        result = ingest_bookmarks(
            synthetic_db,
            user_id=7,
            session=session,
            book_lookup=_make_book_lookup({self.BOOK_UUID: 348}),
            commit=commit,
        )

        assert result["skipped_existing"] == 1, result
        assert result["skipped_newer_server"] == 0, result
        assert session.query(ub.Annotation).filter_by(
            user_id=7, book_id=348, annotation_id="bm-001",
        ).count() == 1

    def test_wire_delivered_annotation_with_newer_server_content_is_rejected(
        self, memory_db, synthetic_db, monkeypatch,
    ):
        """Sentinel equivalence must not hide a real server-side edit."""
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        wire_row, commit = self._write_wire_row(session, monkeypatch)
        wire_row.note_text = "newer server note"
        session.commit()

        result = ingest_bookmarks(
            synthetic_db,
            user_id=7,
            session=session,
            book_lookup=_make_book_lookup({self.BOOK_UUID: 348}),
            commit=commit,
        )

        assert result["skipped_existing"] == 0, result
        assert result["skipped_newer_server"] == 1, result
        assert wire_row.note_text == "newer server note"
        assert wire_row.start_container_child_index is None
        assert wire_row.end_container_child_index is None

    def test_newer_device_edit_preserves_wire_child_index_representation(
        self, memory_db, synthetic_db, monkeypatch,
    ):
        """A content update must not turn wire NULLs into equivalent -99s."""
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        wire_row, commit = self._write_wire_row(session, monkeypatch)
        old_clock = datetime(2025, 1, 1)
        wire_row.client_modified_at = old_clock
        wire_row.server_modified_at = old_clock
        wire_row.last_synced = old_clock
        wire_row.created_at = old_clock
        session.commit()

        result = ingest_bookmarks(
            synthetic_db,
            user_id=7,
            session=session,
            book_lookup=_make_book_lookup({self.BOOK_UUID: 348}),
            commit=commit,
        )

        assert result["updated"] == 1, result
        assert result["skipped_newer_server"] == 0, result
        assert wire_row.start_container_child_index is None
        assert wire_row.end_container_child_index is None


@pytest.mark.unit
class TestKoboDeviceContentId:
    BOOK_UUID = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"

    @staticmethod
    def _replace_content_id(sqlite_path, content_id):
        connection = sqlite3.connect(sqlite_path)
        try:
            connection.execute(
                "UPDATE Bookmark SET ContentID = ? WHERE BookmarkID = 'bm-001'",
                (content_id,),
            )
            connection.commit()
        finally:
            connection.close()

    @pytest.mark.parametrize(
        ("device_content_id", "canonical_content_id"),
        [
            pytest.param(
                f"{BOOK_UUID}!OEBPS!5974957359191092168_980-h-0.htm.xhtml",
                f"{BOOK_UUID}!!OEBPS/5974957359191092168_980-h-0.htm.xhtml",
                id="measured-device-shape",
            ),
            pytest.param(
                f"{BOOK_UUID}!OEBPS!chapter.xhtml#pgepubid00001",
                f"{BOOK_UUID}!!OEBPS/chapter.xhtml#pgepubid00001",
                id="fragment",
            ),
            pytest.param(
                f"{BOOK_UUID}!EPUB/package/Text!part/chapter.xhtml",
                f"{BOOK_UUID}!!EPUB/package/Text/part/chapter.xhtml",
                id="nested-opf-directory",
            ),
        ],
    )
    def test_import_folds_device_content_id_to_canonical_server_form(
        self, memory_db, synthetic_db, device_content_id, canonical_content_id,
    ):
        from cps import ub
        from cps.annotations import ingest_bookmarks

        self._replace_content_id(synthetic_db, device_content_id)
        session, _, _ = memory_db

        result = ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=_make_book_lookup({self.BOOK_UUID: 348}), commit=session.commit,
        )
        row = session.query(ub.Annotation).filter_by(annotation_id="bm-001").one()

        assert result["imported"] == 3, result
        assert result["skipped_invalid_content_id"] == 0, result
        assert row.content_id == canonical_content_id

    def test_import_rejects_device_content_id_that_escapes_after_folding(
        self, memory_db, synthetic_db,
    ):
        from cps import ub
        from cps.annotations import ingest_bookmarks

        self._replace_content_id(
            synthetic_db, f"{self.BOOK_UUID}!..!../outside.xhtml",
        )
        session, _, _ = memory_db

        result = ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=_make_book_lookup({self.BOOK_UUID: 348}), commit=session.commit,
        )

        assert result["imported"] == 2, result
        assert result["skipped_invalid_content_id"] == 1, result
        assert session.query(ub.Annotation).filter_by(annotation_id="bm-001").first() is None


@pytest.mark.unit
class TestPreviouslyInvisibleDeviceRows:
    def test_dogear_and_note_only_row_import_and_every_row_is_accounted(
        self, memory_db, tmp_path,
    ):
        from cps import ub
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        book_uuid = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"
        device_db = build_kobo_db_with_recovery_rows(tmp_path / "recovery.sqlite")

        result = ingest_bookmarks(
            device_db, user_id=7, session=session,
            book_lookup=_make_book_lookup({book_uuid: 348}), commit=session.commit,
        )
        rows = {
            row.annotation_id: row
            for row in session.query(ub.Annotation).filter_by(user_id=7).all()
        }

        assert result["imported"] == 2, result
        assert result["skipped_empty"] == 1, result
        assert result["total_seen"] == 3, result
        assert _accounted(result) == result["total_seen"]
        assert set(rows) == {"recover-dogear", "recover-note-only"}
        assert rows["recover-dogear"].highlighted_text == ""
        assert rows["recover-dogear"].annotation_type == "dogear"
        assert rows["recover-note-only"].highlighted_text == ""
        assert rows["recover-note-only"].note_text == "remember this"
        assert rows["recover-note-only"].annotation_type == "highlight"

    def test_every_returned_count_is_presented_in_the_user_summary(self):
        template = (
            Path(__file__).parents[2] / "cps" / "templates" / "annotations_import.html"
        ).read_text(encoding="utf-8")
        for key in (
            "imported", "updated", "skipped_existing", "skipped_orphan",
            "skipped_hidden", "skipped_empty", "skipped_invalid",
            "skipped_newer_server", "skipped_invalid_content_id", "failed",
            "total_seen",
        ):
            assert f"res.body.{key}" in template

    def test_hidden_device_row_is_reported_without_hiding_server_state(
        self, memory_db, synthetic_db,
    ):
        from cps import ub
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        book_uuid = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"
        session.add(ub.Annotation(
            user_id=7, book_id=348, annotation_id="bm-005",
            highlighted_text="server copy stays visible", hidden=False,
            source="kobo", server_modified_at=datetime(2099, 1, 1),
        ))
        session.commit()

        result = ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=_make_book_lookup({book_uuid: 348}), commit=session.commit,
        )
        row = session.query(ub.Annotation).filter_by(annotation_id="bm-005").one()

        assert result["skipped_hidden"] == 1, result
        assert row.hidden is False
        assert row.highlighted_text == "server copy stays visible"


@pytest.mark.unit
class TestKoboDeviceClockParsing:
    def test_naive_clock_requires_explicit_date_created_opt_in(self):
        from cps.annotations import _parse_kobo_datetime

        clock = "2026-08-15T22:27:08.567"
        assert _parse_kobo_datetime(clock) is None
        assert _parse_kobo_datetime(
            clock,
            assume_naive_utc=True,
        ) == datetime(2026, 8, 15, 22, 27, 8, 567000)


@pytest.mark.unit
class TestOutOfRangeDeviceClock:
    @pytest.mark.parametrize("clock", OVERFLOWING_KOBO_CLOCKS)
    def test_parser_rejects_both_utc_overflows(self, clock):
        from cps.annotations import _parse_kobo_datetime

        assert _parse_kobo_datetime(clock) is None

    @pytest.mark.parametrize("clock", OVERFLOWING_KOBO_CLOCKS)
    def test_row_with_overflowing_clock_is_imported_and_accounted(
        self, memory_db, synthetic_db, clock,
    ):
        from cps import ub
        from cps.annotations import ingest_bookmarks

        connection = sqlite3.connect(synthetic_db)
        try:
            connection.execute(
                "UPDATE Bookmark SET DateModified = ? WHERE BookmarkID = 'bm-001'",
                (clock,),
            )
            connection.commit()
        finally:
            connection.close()

        session, _, _ = memory_db
        result = ingest_bookmarks(
            synthetic_db,
            user_id=7,
            session=session,
            book_lookup=_make_book_lookup({
                "b3d1b38b-74fd-43b7-a796-996e5a6a8b04": 348,
            }),
            commit=session.commit,
        )

        row = session.query(ub.Annotation).filter_by(annotation_id="bm-001").one()
        assert result["imported"] == 3, result
        assert result["total_seen"] == 8, result
        assert _accounted(result) == result["total_seen"]
        assert row.client_modified_at is None

    def test_valid_clock_keeps_its_existing_naive_utc_value(self):
        from cps.annotations import _parse_kobo_datetime

        assert _parse_kobo_datetime(
            "2026-08-20T15:30:45.123456+02:30"
        ) == datetime(2026, 8, 20, 13, 0, 45, 123456)


@pytest.mark.unit
class TestNewerDeviceMerge:
    BOOK_UUID = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"

    @staticmethod
    def _edit_fixture(path, *, modified, text, note, color=3):
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                """
                UPDATE Bookmark
                SET Text = ?, Annotation = ?, Color = ?,
                    ContentID = ?,
                    StartContainerPath = ?, StartContainerChildIndex = ?, StartOffset = ?,
                    EndContainerPath = ?, EndContainerChildIndex = ?, EndOffset = ?,
                    ContextString = ?, ChapterProgress = ?, DateModified = ?
                WHERE BookmarkID = 'bm-002'
                """,
                (
                    text, note, color, f"{TestNewerDeviceMerge.BOOK_UUID}!!chapter9.html",
                    "span#kobo\\.9\\.1", -99, 4,
                    "span#kobo\\.9\\.2", -99, 17,
                    "replacement context", 0.91, modified,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_reimport_applies_every_field_from_a_newer_device_database(
        self, memory_db, synthetic_db,
    ):
        from cps import ub
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        lookup = _make_book_lookup({self.BOOK_UUID: 348})
        ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=lookup, commit=session.commit,
        )
        before = session.query(ub.Annotation).filter_by(annotation_id="bm-002").one()
        before_revision = before.content_revision
        self._edit_fixture(
            synthetic_db, modified="2099-01-01T00:00:00Z",
            text="edited passage", note="edited note", color=3,
        )

        result = ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=lookup, commit=session.commit,
        )
        row = session.query(ub.Annotation).filter_by(annotation_id="bm-002").one()

        assert result["updated"] == 1, result
        assert result["skipped_existing"] == 2, result
        assert _accounted(result) == result["total_seen"]
        assert row.highlighted_text == "edited passage"
        assert row.note_text == "edited note"
        assert row.highlight_color == "#C6E09E"
        assert row.content_id == f"{self.BOOK_UUID}!!chapter9.html"
        assert row.start_container_path == "span#kobo\\.9\\.1"
        assert row.start_container_child_index == -99
        assert row.start_offset == 4
        assert row.end_container_path == "span#kobo\\.9\\.2"
        assert row.end_container_child_index == -99
        assert row.end_offset == 17
        assert row.context_string == "replacement context"
        assert row.chapter_progress == 0.91
        assert row.client_modified_at == datetime(2099, 1, 1)
        assert row.content_revision == before_revision + 1

    def test_older_device_copy_reports_conflict_without_overwriting_server(
        self, memory_db, synthetic_db,
    ):
        from cps import ub
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        lookup = _make_book_lookup({self.BOOK_UUID: 348})
        ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=lookup, commit=session.commit,
        )
        row = session.query(ub.Annotation).filter_by(annotation_id="bm-002").one()
        row.note_text = "newer server note"
        row.server_modified_at = datetime(2098, 1, 1)
        session.commit()
        self._edit_fixture(
            synthetic_db, modified="2097-01-01T00:00:00Z",
            text="stale device passage", note="stale device note",
        )

        result = ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=lookup, commit=session.commit,
        )
        row = session.query(ub.Annotation).filter_by(annotation_id="bm-002").one()

        assert result["updated"] == 0, result
        assert result["skipped_newer_server"] == 1, result
        assert _accounted(result) == result["total_seen"]
        assert row.highlighted_text == "Four legs good, two legs bad."
        assert row.note_text == "newer server note"
        assert row.highlight_color == "#E8AFCF"

    def test_naive_device_modified_clock_cannot_overwrite_server(
        self, memory_db, synthetic_db,
    ):
        from cps import ub
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        lookup = _make_book_lookup({self.BOOK_UUID: 348})
        ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=lookup, commit=session.commit,
        )
        row = session.query(ub.Annotation).filter_by(annotation_id="bm-002").one()
        row.note_text = "newer server note"
        row.server_modified_at = datetime(2027, 6, 1, 11, 30)
        session.commit()

        # Without an offset, noon could be a local UTC+N clock representing an
        # instant before the 11:30 UTC server edit. Treating it as noon UTC is a
        # fail-open guess that lets this ambiguous device snapshot overwrite.
        self._edit_fixture(
            synthetic_db,
            modified="2027-06-01T12:00:00.000",
            text="ambiguous device passage",
            note="ambiguous device note",
        )

        result = ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=lookup, commit=session.commit,
        )
        row = session.query(ub.Annotation).filter_by(annotation_id="bm-002").one()

        assert result["updated"] == 0, result
        assert result["skipped_newer_server"] == 1, result
        assert _accounted(result) == result["total_seen"]
        assert row.highlighted_text == "Four legs good, two legs bad."
        assert row.note_text == "newer server note"
        assert row.highlight_color == "#E8AFCF"


# ---------------------------------------------------------------------------
# 2. Re-import is idempotent
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdempotency:
    def test_second_import_skips_existing(self, memory_db, synthetic_db):
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        book_lookup = _make_book_lookup({
            "b3d1b38b-74fd-43b7-a796-996e5a6a8b04": 348,
        })

        first = ingest_bookmarks(synthetic_db, user_id=7, session=session,
                                  book_lookup=book_lookup, commit=session.commit)
        assert first["imported"] == 3

        second = ingest_bookmarks(synthetic_db, user_id=7, session=session,
                                   book_lookup=book_lookup, commit=session.commit)
        assert second["imported"] == 0
        assert second["skipped_existing"] == 3, second
        # Orphans are still orphans on re-import; that count stays.
        assert second["skipped_orphan"] == 2

    def test_no_duplicate_rows_after_double_import(self, memory_db, synthetic_db):
        from cps import ub
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        book_lookup = _make_book_lookup({
            "b3d1b38b-74fd-43b7-a796-996e5a6a8b04": 348,
        })

        for _i in range(3):
            ingest_bookmarks(synthetic_db, user_id=7, session=session,
                              book_lookup=book_lookup, commit=session.commit)

        total = session.query(ub.Annotation).filter_by(user_id=7).count()
        assert total == 3, "Re-import must never duplicate rows"


# ---------------------------------------------------------------------------
# 3. Multi-user isolation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMultiUserIsolation:
    def test_user_a_import_does_not_collide_with_user_b(self, memory_db, synthetic_db):
        from cps import ub
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        # User B has already imported the same annotations earlier.
        ingest_bookmarks(
            synthetic_db, user_id=99, session=session,
            book_lookup=_make_book_lookup({"b3d1b38b-74fd-43b7-a796-996e5a6a8b04": 348}),
            commit=session.commit,
        )
        # User A imports for the first time — must NOT see user B's
        # rows as "existing" — annotation_id is scoped per-user.
        result = ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=_make_book_lookup({"b3d1b38b-74fd-43b7-a796-996e5a6a8b04": 348}),
            commit=session.commit,
        )
        assert result["imported"] == 3
        assert result["skipped_existing"] == 0

        a_rows = session.query(ub.Annotation).filter_by(user_id=7).count()
        b_rows = session.query(ub.Annotation).filter_by(user_id=99).count()
        assert a_rows == 3
        assert b_rows == 3


# ---------------------------------------------------------------------------
# 4. Sideloaded URI handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSideloadedBookHandling:
    def test_file_uri_volume_id_counted_as_orphan(self, memory_db, synthetic_db):
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        # Only the UUID-format VolumeID maps; file://... doesn't.
        result = ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=_make_book_lookup({"b3d1b38b-74fd-43b7-a796-996e5a6a8b04": 348}),
            commit=session.commit,
        )
        # bm-004 (file:// URI) counts as orphan + bm-006 (unknown UUID)
        # also orphan = 2.
        assert result["skipped_orphan"] == 2


# ---------------------------------------------------------------------------
# 5. Commit failure rolls back
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCommitFailure:
    def test_commit_failure_reports_imported_zero(self, memory_db, synthetic_db):
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        book_lookup = _make_book_lookup({
            "b3d1b38b-74fd-43b7-a796-996e5a6a8b04": 348,
        })

        def boom():
            raise RuntimeError("synthetic commit failure")

        result = ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=book_lookup, commit=boom,
        )
        assert result["imported"] == 0
        # Other counts are still reported honestly so the user sees
        # what would have been imported if the commit had succeeded.
        assert result["skipped_orphan"] == 2
        assert result["skipped_hidden"] == 1


@pytest.mark.unit
class TestImportHonestyAndIdentity:
    """Two ways the import misreports what it actually did."""

    def test_commit_returning_false_reports_imported_zero(self, memory_db, synthetic_db):
        """The production commit signals failure by RETURNING False, not by raising.

        ``ub.session_commit`` catches OperationalError/InvalidRequestError,
        rolls back, and returns False — the sibling test above only covers a
        commit that raises, which is the path production does not take. With
        only that coverage the endpoint answers HTTP 200 and ``imported: N``
        after writing nothing at all.
        """
        from cps import ub
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        book_lookup = _make_book_lookup({
            "b3d1b38b-74fd-43b7-a796-996e5a6a8b04": 348,
        })

        result = ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=book_lookup, commit=lambda: False,
        )

        assert result["imported"] == 0, (
            "a rolled-back import must not report rows as imported"
        )
        assert session.query(ub.Annotation).filter_by(user_id=7).count() == 0
        # The other counts stay honest, as with a raising commit.
        assert result["skipped_orphan"] == 2
        assert result["skipped_hidden"] == 1

    def test_same_annotation_id_against_a_different_book_is_not_skipped(
        self, memory_db, synthetic_db,
    ):
        """Dedup must use the canonical key, which includes the book.

        ``uq_annotation_user_book_annotation`` is on
        ``(user_id, book_id, annotation_id)`` and the live PATCH dispatcher
        upserts on that triple. The import checked only
        ``(user_id, annotation_id)``, so one book's row suppressed a row the
        schema explicitly permits in another book.
        """
        from cps import ub
        from cps.annotations import ingest_bookmarks

        session, _, _ = memory_db
        uuid = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"

        first = ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=_make_book_lookup({uuid: 348}), commit=session.commit,
        )
        assert first["imported"] == 3

        # Same bookmark ids, resolved to a DIFFERENT book.
        second = ingest_bookmarks(
            synthetic_db, user_id=7, session=session,
            book_lookup=_make_book_lookup({uuid: 349}), commit=session.commit,
        )

        assert second["imported"] == 3, (
            "rows for a different book were suppressed by the wrong dedup key"
        )
        assert session.query(ub.Annotation).filter_by(user_id=7, book_id=348).count() == 3
        assert session.query(ub.Annotation).filter_by(user_id=7, book_id=349).count() == 3
