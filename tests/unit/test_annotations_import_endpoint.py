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
