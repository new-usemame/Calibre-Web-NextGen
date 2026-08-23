# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Interaction proofs for the Kobo annotation changes merged together.

These tests deliberately cross subsystem boundaries.  The individual fixes
already have focused coverage; this file protects the state handed from one fix
to the next: device import -> startup migration, nested KEPUB split -> guarded
conversion re-split, and live PATCH -> portable export/import -> sync fan-out.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import sqlite3
import zipfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit

BOOK_UUID = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"
OVERFLOWING_CLOCKS = (
    pytest.param("9999-12-31T23:59:59-23:59", id="above-maxyear"),
    pytest.param("0001-01-01T00:00:00+23:59", id="below-minyear"),
)


@pytest.fixture
def annotation_db(tmp_path, monkeypatch):
    from cps import constants, ub
    from cps.services import annotation_backup

    annotation_backup.reset_for_tests()
    monkeypatch.setattr(annotation_backup, "WORKER_AUTOSTART", False)
    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))

    engine = create_engine(
        "sqlite:///{}".format(tmp_path / "annotation-interactions.sqlite"),
        future=True,
    )
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    yield engine, session
    session.close()
    engine.dispose()
    annotation_backup.reset_for_tests()


def _book_lookup(volume_id):
    return SimpleNamespace(id=42) if volume_id == BOOK_UUID else None


def test_device_import_types_survive_the_actual_startup_migration(
        annotation_db, tmp_path, monkeypatch):
    """#1774 rows must already be authoritative when #1777 starts."""
    from cps import ub
    from cps.annotations import ingest_bookmarks
    from scripts.measure_kobo_anchorable_chapters import analyse
    from tests.fixtures.kobo_reader_sqlite import build_kobo_db_with_recovery_rows
    from tests.unit.test_1657_spine_splitter import (
        _book,
        _nested_anchor_chapter,
        _package_state,
        _split,
    )

    engine, session = annotation_db
    package_dir = tmp_path / "import-package"
    package_dir.mkdir()
    package = _book(
        package_dir,
        targets=("chapter.xhtml#ch1", "chapter.xhtml#ch2", "chapter.xhtml#ch3"),
        chapter=_nested_anchor_chapter(),
    )
    assert _split(package) is True
    assert analyse(package)[:4] == (3, 3, 0, 0)
    _contents, _manifest, _spine, targets = _package_state(package)
    imported_content_id = "{}!!{}".format(BOOK_UUID, targets[1])

    device_db = build_kobo_db_with_recovery_rows(tmp_path / "KoboReader.sqlite")
    connection = sqlite3.connect(device_db)
    try:
        connection.execute(
            "UPDATE Bookmark SET ContentID = ? "
            "WHERE BookmarkID IN ('recover-dogear', 'recover-note-only')",
            (imported_content_id,),
        )
        connection.commit()
    finally:
        connection.close()
    result = ingest_bookmarks(
        device_db,
        user_id=7,
        session=session,
        book_lookup=_book_lookup,
        commit=session.commit,
    )
    before = {
        row.annotation_id: (row.annotation_type, row.content_id)
        for row in session.query(ub.Annotation).order_by(ub.Annotation.annotation_id)
    }

    assert result["imported"] == 2, result
    assert before == {
        "recover-dogear": ("dogear", imported_content_id),
        "recover-note-only": ("highlight", imported_content_id),
    }, "the fixture did not produce the #1774 typed rows"

    # Drive the startup entry point, not merely its backfill helper.  Wrap the
    # real helper so the test also proves these rows took the non-NULL branch;
    # unchanged values alone could not distinguish that from an over-broad scan
    # that happened to derive no replacement from this particular row shape.
    real_backfill = ub.backfill_legacy_annotation_types
    backfill_reports = []

    def observe_backfill(migration_engine):
        report = real_backfill(migration_engine)
        backfill_reports.append(report)
        return report

    monkeypatch.setattr(ub, "backfill_legacy_annotation_types", observe_backfill)
    ub.migrate_kobo_two_way_annotation_sync(engine, session)
    session.expire_all()
    after = {
        row.annotation_id: (row.annotation_type, row.content_id)
        for row in session.query(ub.Annotation).order_by(ub.Annotation.annotation_id)
    }

    assert len(backfill_reports) == 1
    assert backfill_reports[0]["total"] == 2
    assert backfill_reports[0]["already_typed"] == 2
    assert backfill_reports[0]["null"] == 0
    assert backfill_reports[0]["would_update"] == 0
    assert backfill_reports[0]["updated"] == 0
    assert after == before


def _archive_contents(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in sorted(archive.namelist())}


def test_nested_split_replacement_keeps_every_existing_highlight_anchor(
        tmp_path, monkeypatch):
    """The convert guard and #1773 naming contract are one invariant."""
    import cps.helper  # noqa: F401 - establish the application's import order

    from cps.services.kepub_spine_splitter import package_was_split_by_us
    from cps.tasks import convert
    from tests.unit.test_1657_spine_splitter import (
        _book,
        _nested_anchor_chapter,
        _package_state,
        _split,
    )
    from tests.unit.test_kepub_package_normalizer import _patch_annotation_store

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = _book(
        source_dir,
        targets=("chapter.xhtml#ch1", "chapter.xhtml#ch2", "chapter.xhtml#ch3"),
        chapter=_nested_anchor_chapter(),
    )
    unsplit_bytes = source.read_bytes()

    # The stored package is the one existing highlights name.
    destination = tmp_path / "book.kepub"
    destination.write_bytes(unsplit_bytes)
    assert _split(destination) is True
    assert package_was_split_by_us(destination) is True
    expected_archive = _archive_contents(destination)
    _contents, _manifest, _spine, existing_anchors = _package_state(destination)
    assert existing_anchors == [
        "chapter-split-1.xhtml",
        "chapter-split-2.xhtml",
        "chapter-split-3.xhtml",
    ], "the fixture did not exercise #1773's nested piece allocation"

    # Model kepubify regenerating the same source for an annotated book.  The
    # real conversion boundary must recognise the stored split, allow a re-split,
    # and reproduce both member names and the bytes containing each KoboSpan.
    (tmp_path / "book.epub").write_bytes(b"source format")
    (tmp_path / "book.kepub.epub").write_bytes(unsplit_bytes)
    process = SimpleNamespace(returncode=0)
    monkeypatch.setattr(convert.config, "config_embed_metadata", False, raising=False)
    monkeypatch.setattr(convert.config, "config_binariesdir", "", raising=False)
    monkeypatch.setattr(convert.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(convert, "process_open", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(convert, "stream_process_output", lambda *_args, **_kwargs: [])
    _patch_annotation_store(monkeypatch, convert, annotations=1)
    task = convert.TaskConvert(str(tmp_path / "book"), 1, "convert", {}, None)

    check, error = task._convert_kepubify(
        str(tmp_path / "book"), ".epub", ".kepub")

    assert check == 0, error
    assert _archive_contents(destination) == expected_archive
    _contents, _manifest, _spine, replacement_anchors = _package_state(destination)
    assert replacement_anchors == existing_anchors


class _CaptureHandler:
    target_name = "interaction-capture"

    def __init__(self):
        self.rows = []

    def is_enabled(self, _user):
        return True

    def for_session(self, _session):
        return self

    def push(self, annotation, _book, _user, payload=None):
        from cps.services.annotation_sync.base import SyncResult

        self.rows.append({
            "annotation_id": annotation.annotation_id,
            "text": annotation.highlighted_text,
            "note": annotation.note_text,
            "color": annotation.highlight_color,
            "type": annotation.annotation_type,
            "content_id": annotation.content_id,
            "start_path": annotation.start_container_path,
            "start_offset": annotation.start_offset,
            "end_path": annotation.end_container_path,
            "end_offset": annotation.end_offset,
            "context": annotation.context_string,
            "progress": annotation.chapter_progress,
            "client_modified_at": annotation.client_modified_at,
        })
        return SyncResult(status="synced", target_record_id="remote-1")

    def delete(self, _sync_target, _user):  # pragma: no cover - not this flow
        raise AssertionError("the round trip unexpectedly deleted its highlight")


@pytest.mark.parametrize("clock", OVERFLOWING_CLOCKS)
def test_live_highlight_portable_round_trip_preserves_user_state(
        annotation_db, monkeypatch, tmp_path, clock):
    """PATCH -> startup migration -> portable pull/push -> sync fan-out."""
    from cps import ub
    from cps.progress_syncing.protocols.koreader_annotations import (
        apply_push,
        build_pull_payload,
    )
    from cps.services.annotation_sync import (
        dispatch_annotation_sync,
        register_handler,
        reset_registry_for_testing,
    )
    from scripts.measure_kobo_anchorable_chapters import analyse
    from tests.unit.test_1657_spine_splitter import (
        _book,
        _nested_anchor_chapter,
        _package_state,
        _split,
    )

    engine, session = annotation_db
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", lambda: session.commit())
    reset_registry_for_testing()

    split_dir = tmp_path / "round-trip-book"
    split_dir.mkdir()
    package = _book(
        split_dir,
        targets=("chapter.xhtml#ch1", "chapter.xhtml#ch2", "chapter.xhtml#ch3"),
        chapter=_nested_anchor_chapter(),
    )
    assert _split(package) is True
    assert analyse(package)[:4] == (3, 3, 0, 0)
    _contents, _manifest, _spine, targets = _package_state(package)
    chapter_href = targets[1]

    user = ub.User(
        name="interaction-user",
        email="interaction@example.invalid",
        role=0,
        password="x",
    )
    session.add(user)
    session.commit()
    source_book = SimpleNamespace(id=42, uuid=BOOK_UUID, title="Source")
    destination_book = SimpleNamespace(id=43, uuid=BOOK_UUID, title="Destination")
    payload = {
        "id": "round-trip-highlight",
        "type": "highlight",
        "highlightedText": "The exact highlighted passage.",
        "noteText": "Keep this note too.",
        "highlightColor": "#E8AFCF",
        "clientLastModifiedUtc": clock,
        "location": {"span": {
            "chapterFilename": chapter_href,
            "startPath": "span#kobo.11.1",
            "startChar": 2,
            "endPath": "span#kobo.11.2",
            "endChar": 7,
            "contextString": "surrounding words",
            "chapterProgress": 0.375,
        }},
    }

    dispatch_annotation_sync([payload], source_book, user)
    source = session.query(ub.Annotation).filter_by(book_id=42).one()
    assert source.client_modified_at is None
    assert source.annotation_type == "highlight"

    # #1777 runs at startup after rows from any writer already exist.
    ub.migrate_kobo_two_way_annotation_sync(engine, session)
    session.expire_all()
    portable = build_pull_payload(user.id, source_book.id, session)
    assert portable["annotation_count"] == 1

    capture = _CaptureHandler()
    register_handler(capture)
    summary = apply_push(
        portable["annotations"],
        user=user,
        book=destination_book,
        session=session,
        commit=session.commit,
    )

    assert summary == {"created": 1, "updated": 0, "deleted": 0, "skipped": 0}
    destination = session.query(ub.Annotation).filter_by(book_id=43).one()
    expected = {
        "annotation_id": source.annotation_id,
        "text": source.highlighted_text,
        "note": source.note_text,
        "color": source.highlight_color,
        "type": source.annotation_type,
        "content_id": source.content_id,
        "start_path": source.start_container_path,
        "start_offset": source.start_offset,
        "end_path": source.end_container_path,
        "end_offset": source.end_offset,
        "context": source.context_string,
        "progress": source.chapter_progress,
        "client_modified_at": None,
    }
    assert capture.rows == [expected]
    assert destination.highlighted_text == source.highlighted_text
    assert destination.note_text == source.note_text
    assert destination.highlight_color == source.highlight_color
    assert destination.annotation_type == source.annotation_type
    assert destination.content_id == source.content_id
    assert destination.start_container_path == source.start_container_path
    assert destination.start_offset == source.start_offset
    assert destination.end_container_path == source.end_container_path
    assert destination.end_offset == source.end_offset
    assert destination.context_string == source.context_string
    assert destination.chapter_progress == source.chapter_progress
    assert destination.client_modified_at is None

    reset_registry_for_testing()
