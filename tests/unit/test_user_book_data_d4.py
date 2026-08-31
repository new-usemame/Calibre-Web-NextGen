# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""D4: per-user-book data has ONE enumerator (cps/user_book_data.py).

Merging a duplicate used to copy only file formats — the losing book was
then deleted and the user's annotations, reading progress, Kobo state and
shelf membership on it were orphaned (annotations: silent data loss). The
admin database-change wipe and the per-user delete each kept their own
disagreeing hand-list, both missing annotations (per-user delete also left
on-disk annotation-backup gzips behind — PII surviving account deletion).

migrate_user_book_data / purge_user_book_data are now the only places that
enumerate the per-user-book model set; behavioural tests run against a real
in-memory app.db; source-pins lock the four call sites onto the helpers.
"""

import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO = pathlib.Path(__file__).resolve().parents[2]

WINNER = 101
LOSER = 202
USER = 7


@pytest.fixture
def session(monkeypatch):
    import sys
    # some suites stub cps.*, flask, sqlalchemy… into sys.modules and don't
    # restore — evict the whole affected families so we import the real ones.
    if "cps.ub" in sys.modules and not hasattr(sys.modules["cps.ub"], "Base"):
        stubbed = {"cps", "cwa_db", "flask", "flask_babel", "flask_dance",
                   "sqlalchemy", "werkzeug"}
        for name in [m for m in list(sys.modules) if m.split(".")[0] in stubbed]:
            sys.modules.pop(name, None)
    from cps import ub
    from cps.services import annotation_backup
    annotation_backup.reset_for_tests()
    monkeypatch.setattr(annotation_backup, "WORKER_AUTOSTART", False)
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    # the BookShelf before_flush listener walks link.ub_shelf — give the
    # shelves used in these tests real rows.
    s.add_all([ub.Shelf(id=1, name="one", user_id=USER),
               ub.Shelf(id=2, name="two", user_id=USER)])
    s.commit()
    yield s
    s.close()


def _set_state_timestamps(session, ub, book_to_ts):
    # the ub before_flush listener stamps KoboReadingState.last_modified to
    # now() whenever its bookmark child flushes, clobbering explicit values —
    # set them afterwards with a bulk UPDATE (which the listener ignores).
    for book_id, ts in book_to_ts.items():
        session.query(ub.KoboReadingState).filter(
            ub.KoboReadingState.book_id == book_id).update(
            {ub.KoboReadingState.last_modified: ts}, synchronize_session=False)
    session.commit()
    session.expire_all()


def _shelf_link(session, ub, book_id, shelf_id, order):
    # production creates links through the Shelf.books relationship, which
    # populates the ub_shelf backref the before_flush listener relies on.
    shelf = session.get(ub.Shelf, shelf_id)
    link = ub.BookShelf(book_id=book_id, order=order, ub_shelf=shelf)
    session.add(link)
    return link


def _annotation(ub, book_id, annotation_id="ann-1", user_id=USER, **kw):
    return ub.Annotation(user_id=user_id, annotation_id=annotation_id,
                         book_id=book_id, highlighted_text=kw.pop("text", "hl"),
                         source="webreader", **kw)


@pytest.mark.unit
def test_per_user_book_registry_includes_device_entitlement_ledger():
    from cps.user_book_data import PER_USER_BOOK_MODELS

    assert "KoboDeviceBookEntitlement" in PER_USER_BOOK_MODELS


@pytest.mark.unit
class TestMigrate:
    def test_annotation_moves_to_winner_with_sync_targets(self, session):
        from cps import ub
        from cps.user_book_data import migrate_user_book_data
        ann = _annotation(ub, LOSER)
        ann.sync_targets.append(ub.AnnotationSyncTarget(target="hardcover", status="synced"))
        session.add(ann)
        session.commit()

        migrate_user_book_data(LOSER, WINNER, session=session)
        session.commit()

        moved = session.query(ub.Annotation).one()
        assert moved.book_id == WINNER
        assert moved.highlighted_text == "hl"
        assert session.query(ub.AnnotationSyncTarget).count() == 1

    def test_annotation_clash_keeps_newer_destination_row(self, session):
        from cps import ub
        from cps.user_book_data import migrate_user_book_data
        keep = _annotation(
            ub, WINNER, text="newer-destination-copy", note_text="keep me",
            server_modified_at=datetime(2026, 7, 20), content_revision=9,
        )
        lose = _annotation(
            ub, LOSER, text="stale-source-copy",
            server_modified_at=datetime(2026, 1, 1), content_revision=1,
        )
        keep.sync_targets.append(
            ub.AnnotationSyncTarget(target="readwise", status="synced")
        )
        lose.sync_targets.append(ub.AnnotationSyncTarget(target="hardcover", status="pending"))
        session.add_all([keep, lose])
        session.commit()

        migrate_user_book_data(LOSER, WINNER, session=session)
        session.commit()

        rows = session.query(ub.Annotation).all()
        assert len(rows) == 1
        assert rows[0].book_id == WINNER
        assert rows[0].highlighted_text == "newer-destination-copy"
        assert rows[0].note_text == "keep me"
        # Only the dropped source row's sync-target child is removed.
        targets = session.query(ub.AnnotationSyncTarget).all()
        assert len(targets) == 1
        assert targets[0].annotation_id == rows[0].id
        assert targets[0].target == "readwise"

    def test_annotation_clash_moves_newer_source_row_and_drops_destination_children(
        self, session,
    ):
        from cps import ub
        from cps.user_book_data import migrate_user_book_data
        keep = _annotation(
            ub, WINNER, text="stale-destination-copy", note_text=None,
            server_modified_at=datetime(2026, 1, 1), content_revision=1,
        )
        lose = _annotation(
            ub, LOSER, text="newer-source-copy", note_text="recent note",
            server_modified_at=datetime(2026, 7, 20), content_revision=9,
        )
        keep.sync_targets.append(
            ub.AnnotationSyncTarget(target="hardcover", status="synced")
        )
        lose.sync_targets.append(
            ub.AnnotationSyncTarget(target="readwise", status="pending")
        )
        session.add_all([keep, lose])
        session.commit()
        source_id = lose.id

        migrate_user_book_data(LOSER, WINNER, session=session)
        session.commit()

        row = session.query(ub.Annotation).one()
        assert row.id == source_id
        assert row.book_id == WINNER
        assert row.highlighted_text == "newer-source-copy"
        assert row.note_text == "recent note"
        assert row.content_revision == 9
        targets = session.query(ub.AnnotationSyncTarget).all()
        assert len(targets) == 1
        assert targets[0].annotation_id == row.id
        assert targets[0].target == "readwise"

    def test_annotation_clash_uses_client_clock_when_server_clocks_are_null(
        self, session,
    ):
        from cps import ub
        from cps.user_book_data import migrate_user_book_data
        session.add_all([
            _annotation(
                ub, WINNER, text="stale-destination-copy",
                client_modified_at=datetime(2026, 1, 1), content_revision=9,
            ),
            _annotation(
                ub, LOSER, text="newer-source-copy",
                client_modified_at=datetime(2026, 7, 20), content_revision=1,
            ),
        ])
        session.commit()

        migrate_user_book_data(LOSER, WINNER, session=session)
        session.commit()

        row = session.query(ub.Annotation).one()
        assert row.book_id == WINNER
        assert row.highlighted_text == "newer-source-copy"

    def test_annotation_clash_null_clocks_use_revision_then_destination_tie_break(
        self, session,
    ):
        from cps import ub
        from cps.user_book_data import migrate_user_book_data
        session.add_all([
            _annotation(ub, WINNER, annotation_id="revision", text="revision-2",
                        content_revision=2),
            _annotation(ub, LOSER, annotation_id="revision", text="revision-7",
                        content_revision=7),
            _annotation(ub, WINNER, annotation_id="tie", text="destination-tie",
                        content_revision=4),
            _annotation(ub, LOSER, annotation_id="tie", text="source-tie",
                        content_revision=4),
        ])
        session.commit()

        migrate_user_book_data(LOSER, WINNER, session=session)
        session.commit()

        rows = {
            row.annotation_id: row
            for row in session.query(ub.Annotation).order_by(ub.Annotation.annotation_id)
        }
        assert set(rows) == {"revision", "tie"}
        assert rows["revision"].book_id == WINNER
        assert rows["revision"].highlighted_text == "revision-7"
        assert rows["tie"].book_id == WINNER
        assert rows["tie"].highlighted_text == "destination-tie"

    def test_kobo_reading_state_newer_loser_wins(self, session):
        from cps import ub
        from cps.user_book_data import migrate_user_book_data
        now = datetime.now(timezone.utc)
        old = ub.KoboReadingState(user_id=USER, book_id=WINNER)
        new = ub.KoboReadingState(user_id=USER, book_id=LOSER)
        new.current_bookmark = ub.KoboBookmark(progress_percent=42.0)
        session.add_all([old, new])
        session.commit()
        _set_state_timestamps(session, ub, {WINNER: now - timedelta(days=2), LOSER: now})

        migrate_user_book_data(LOSER, WINNER, session=session)
        session.commit()

        state = session.query(ub.KoboReadingState).one()
        assert state.book_id == WINNER
        assert state.current_bookmark.progress_percent == 42.0

    def test_kobo_reading_state_older_loser_dropped(self, session):
        from cps import ub
        from cps.user_book_data import migrate_user_book_data
        now = datetime.now(timezone.utc)
        keep = ub.KoboReadingState(user_id=USER, book_id=WINNER)
        keep.current_bookmark = ub.KoboBookmark(progress_percent=80.0)
        stale = ub.KoboReadingState(user_id=USER, book_id=LOSER)
        stale.current_bookmark = ub.KoboBookmark(progress_percent=10.0)
        session.add_all([keep, stale])
        session.commit()
        _set_state_timestamps(session, ub, {WINNER: now, LOSER: now - timedelta(days=2)})

        migrate_user_book_data(LOSER, WINNER, session=session)
        session.commit()

        state = session.query(ub.KoboReadingState).one()
        assert state.book_id == WINNER
        assert state.current_bookmark.progress_percent == 80.0
        assert session.query(ub.KoboBookmark).count() == 1

    def test_read_book_merge_keeps_furthest_status(self, session):
        from cps import ub
        from cps.user_book_data import migrate_user_book_data
        session.add_all([
            ub.ReadBook(user_id=USER, book_id=WINNER,
                        read_status=ub.ReadBook.STATUS_IN_PROGRESS, times_started_reading=1),
            ub.ReadBook(user_id=USER, book_id=LOSER,
                        read_status=ub.ReadBook.STATUS_FINISHED, times_started_reading=3),
        ])
        session.commit()

        migrate_user_book_data(LOSER, WINNER, session=session)
        session.commit()

        rb = session.query(ub.ReadBook).one()
        assert rb.book_id == WINNER
        assert rb.read_status == ub.ReadBook.STATUS_FINISHED
        assert rb.times_started_reading == 4

    def test_shelf_membership_repointed_unless_already_on_shelf(self, session):
        from cps import ub
        from cps.user_book_data import migrate_user_book_data
        _shelf_link(session, ub, LOSER, 1, 5)      # winner not on shelf 1
        _shelf_link(session, ub, LOSER, 2, 1)      # clash on shelf 2
        _shelf_link(session, ub, WINNER, 2, 9)
        session.commit()

        migrate_user_book_data(LOSER, WINNER, session=session)
        session.commit()

        links = session.query(ub.BookShelf).order_by(ub.BookShelf.shelf).all()
        assert [
            (link.shelf, link.book_id, link.order) for link in links
        ] == [(1, WINNER, 5), (2, WINNER, 9)]

    def test_kobo_synced_marker_dropped_not_migrated(self, session):
        from cps import ub
        from cps.user_book_data import migrate_user_book_data
        device = ub.Device(
            user_id=USER, kind="kobo", display_name="D4 Kobo",
            active=True, created_by="auto",
        )
        session.add(device)
        session.flush()
        session.add_all([
            ub.KoboSyncedBooks(user_id=USER, book_id=LOSER),
            ub.KoboDeviceBookEntitlement(
                device_id=device.id, book_id=LOSER, fingerprint="a" * 64,
            ),
        ])
        session.commit()

        migrate_user_book_data(LOSER, WINNER, session=session)
        session.commit()

        # the marker means "this file was delivered" — the kept book is a
        # different file, so it must sync fresh, not inherit the marker.
        assert session.query(ub.KoboSyncedBooks).count() == 0
        assert session.query(ub.KoboDeviceBookEntitlement).count() == 0

    def test_simple_flags_migrate_and_dedupe(self, session):
        from cps import ub
        from cps.user_book_data import migrate_user_book_data
        session.add_all([
            ub.ArchivedBook(user_id=USER, book_id=LOSER, is_archived=True),
            ub.Downloads(user_id=USER, book_id=LOSER),
            ub.Downloads(user_id=USER, book_id=WINNER),  # clash → loser row dropped
        ])
        session.commit()

        migrate_user_book_data(LOSER, WINNER, session=session)
        session.commit()

        assert session.query(ub.ArchivedBook).one().book_id == WINNER
        downloads = session.query(ub.Downloads).all()
        assert len(downloads) == 1 and downloads[0].book_id == WINNER


@pytest.mark.unit
class TestPurge:
    def _populate(self, session, ub):
        ann = _annotation(ub, LOSER)
        ann.sync_targets.append(ub.AnnotationSyncTarget(target="hardcover", status="synced"))
        state = ub.KoboReadingState(user_id=USER, book_id=LOSER)
        state.current_bookmark = ub.KoboBookmark(progress_percent=10.0)
        state.statistics = ub.KoboStatistics(spent_reading_minutes=5)
        device = ub.Device(
            user_id=USER, kind="kobo", display_name="D4 Kobo",
            active=True, created_by="auto",
        )
        session.add(device)
        session.flush()
        session.add_all([
            ann, state,
            ub.ReadBook(user_id=USER, book_id=LOSER, read_status=1),
            ub.Bookmark(user_id=USER, book_id=LOSER, format="EPUB", bookmark_key="k"),
            ub.ArchivedBook(user_id=USER, book_id=LOSER, is_archived=False),
            ub.Downloads(user_id=USER, book_id=LOSER),
            ub.KoboSyncedBooks(user_id=USER, book_id=LOSER),
            ub.KoboDeviceBookEntitlement(
                device_id=device.id, book_id=LOSER, fingerprint="b" * 64,
            ),
            ub.KoboDevicePendingSyncPage(
                device_id=device.id,
                incoming_token_hash="a" * 64,
                outgoing_token="pending-outgoing-token",
                response_body='[{"NewEntitlement":{}}]',
                response_headers_json="{}",
                confirmation_json="{}",
            ),
        ])
        _shelf_link(session, ub, LOSER, 1, 1)
        session.commit()

    def test_purge_by_book_removes_everything_for_all_users(self, session):
        from cps import ub
        from cps.user_book_data import purge_user_book_data
        self._populate(session, ub)

        purge_user_book_data(book_id=LOSER, session=session, remove_backup_files=False)
        session.commit()

        for model in (ub.Annotation, ub.AnnotationSyncTarget, ub.KoboReadingState,
                      ub.KoboBookmark, ub.KoboStatistics, ub.ReadBook, ub.Bookmark,
                      ub.ArchivedBook, ub.Downloads, ub.BookShelf, ub.KoboSyncedBooks,
                      ub.KoboDeviceBookEntitlement, ub.KoboDevicePendingSyncPage):
            assert session.query(model).count() == 0, model.__name__

    def test_purge_by_book_leaves_other_books_alone(self, session):
        from cps import ub
        from cps.user_book_data import purge_user_book_data
        self._populate(session, ub)
        session.add(_annotation(ub, WINNER, annotation_id="ann-other"))
        session.commit()

        purge_user_book_data(book_id=LOSER, session=session, remove_backup_files=False)
        session.commit()

        assert session.query(ub.Annotation).one().book_id == WINNER

    def test_purge_by_user_removes_backup_files_on_disk(self, session, tmp_path):
        from cps import ub
        from cps.user_book_data import purge_user_book_data
        gz = tmp_path / "snapshot.json.gz"
        gz.write_bytes(b"x")
        session.add_all([
            _annotation(ub, LOSER),
            ub.KoboAnnotationBackup(user_id=USER, book_id=LOSER, content_hash="h",
                                    file_path=str(gz), size_bytes=1, annotation_count=1),
            _annotation(ub, LOSER, annotation_id="ann-keep", user_id=USER + 1),
        ])
        session.commit()

        purge_user_book_data(user_id=USER, session=session)
        session.commit()

        assert not gz.exists(), "backup gzip (PII) must be removed with the user"
        assert session.query(ub.KoboAnnotationBackup).count() == 0
        remaining = session.query(ub.Annotation).one()
        assert remaining.user_id == USER + 1, "other users' annotations untouched"

    def test_purge_by_user_does_not_touch_shelf_links(self, session):
        from cps import ub
        from cps.user_book_data import purge_user_book_data
        # BookShelf has no user column — shelf membership belongs to the
        # shelf (handled by the user-delete path via the user's shelves).
        _shelf_link(session, ub, LOSER, 1, 1)
        session.commit()

        purge_user_book_data(user_id=USER, session=session)
        session.commit()

        assert session.query(ub.BookShelf).count() == 1

    def test_purge_by_user_scopes_deleted_entitlements_and_seed_markers(self, session):
        from cps import ub
        from cps.user_book_data import purge_user_book_data

        target = ub.Device(
            user_id=USER, kind="kobo", display_name="Target Kobo",
            active=True, created_by="auto",
        )
        other = ub.Device(
            user_id=USER + 1, kind="kobo", display_name="Other Kobo",
            active=True, created_by="auto",
        )
        session.add_all([target, other])
        session.flush()
        for device, suffix in ((target, "target"), (other, "other")):
            session.add_all([
                ub.KoboDeviceDeletedEntitlement(
                    device_id=device.id,
                    book_uuid=f"deleted-{suffix}",
                    fingerprint="d" * 64,
                ),
                ub.KoboDeviceEntitlementSeed(device_id=device.id),
            ])
        session.commit()

        purge_user_book_data(user_id=USER, session=session)
        session.commit()

        assert [
            row.device_id for row in
            session.query(ub.KoboDeviceDeletedEntitlement).all()
        ] == [other.id]
        assert [
            row.device_id for row in
            session.query(ub.KoboDeviceEntitlementSeed).all()
        ] == [other.id]

    def test_complete_database_purge_clears_deleted_entitlements_and_seed_markers(
        self, session,
    ):
        from cps import ub
        from cps.user_book_data import purge_user_book_data

        device = ub.Device(
            user_id=USER, kind="kobo", display_name="Database Swap Kobo",
            active=True, created_by="auto",
        )
        session.add(device)
        session.flush()
        session.add_all([
            ub.KoboDeviceDeletedEntitlement(
                device_id=device.id,
                book_uuid="old-library-deleted",
                fingerprint="e" * 64,
            ),
            ub.KoboDeviceEntitlementSeed(device_id=device.id),
        ])
        session.commit()

        purge_user_book_data(session=session)
        session.commit()

        assert session.query(ub.KoboDeviceDeletedEntitlement).count() == 0
        assert session.query(ub.KoboDeviceEntitlementSeed).count() == 0

    def test_stage0_evidence_is_purged_before_annotation_id_reuse(self, session):
        """Account erasure must not alias old raw bytes onto a recycled id."""
        from cps import ub
        from cps.user_book_data import purge_user_book_data

        user = ub.User(name="stage0-owner", email="owner@example.invalid", role=0)
        user.password = "x"
        session.add(user)
        session.commit()
        annotation = _annotation(
            ub, LOSER, annotation_id="old-annotation", user_id=user.id,
            text="SECRET HIGHLIGHT TEXT",
        )
        session.add(annotation)
        session.commit()
        old_annotation_id = annotation.id
        session.add(ub.KoboAnnotationMaterialization(
            annotation_id=annotation.id,
            raw_annotation_json=b'{"highlightedText":"SECRET HIGHLIGHT TEXT"}',
            raw_location_json=b'{"span":{}}',
            raw_client_modified_utc="t",
            payload_sha256="0" * 64,
            provenance="kobo_patch",
            attachments_state="missing",
            serveable=False,
        ))
        state = ub.KoboAnnotationBookState(
            user_id=user.id, book_id=LOSER, content_id="legacy-book:202",
            generation_id="generation", authority_status="authoritative",
            authority_revision=1, opaque_content_status="present",
        )
        session.add(state)
        session.commit()
        capture = ub.KoboAnnotationSeedCapture(
            book_state_id=state.id, result="accepted",
        )
        snapshot = ub.KoboAnnotationPageSnapshot(
            snapshot_id="purge-snapshot", book_state_id=state.id,
            authority_revision=1, etag="etag", ordered_payload_gzip=b"payload",
            annotation_count=1, page_size=10,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
        session.add_all([
            ub.KoboDeviceBookAnnotationState(
                device_id=999, book_state_id=state.id,
            ),
            capture,
            snapshot,
        ])
        session.commit()
        session.add_all([
            ub.KoboAnnotationSeedCapturePage(
                seed_capture_id=capture.id, page_number=0,
                response_body_gzip=b"page", response_sha256="1" * 64,
            ),
            ub.KoboAnnotationSeedRowBaseline(
                seed_capture_id=capture.id,
                annotation_key="secret-ann",
                annotation_row_id=old_annotation_id,
                content_revision=1,
                content_sha256="2" * 64,
            ),
            ub.KoboAnnotationPageCursor(
                token="purge-cursor", snapshot_id=snapshot.snapshot_id,
                page_offset=0,
            ),
        ])
        session.commit()

        purge_user_book_data(user_id=user.id, session=session)
        session.delete(user)
        session.commit()

        for model in (
            ub.KoboAnnotationMaterialization,
            ub.KoboAnnotationBookState,
            ub.KoboOpaqueContentPresentGuard,
            ub.KoboDeviceBookAnnotationState,
            ub.KoboAnnotationSeedCapture,
            ub.KoboAnnotationSeedCapturePage,
            ub.KoboAnnotationSeedRowBaseline,
            ub.KoboAnnotationPageSnapshot,
            ub.KoboAnnotationPageCursor,
        ):
            assert session.query(model).count() == 0, model.__name__

        replacement_user = ub.User(
            name="replacement-owner", email="replacement@example.invalid", role=0,
        )
        replacement_user.password = "x"
        session.add(replacement_user)
        session.commit()
        replacement = _annotation(
            ub, WINNER, annotation_id="new-annotation",
            user_id=replacement_user.id, text="new owner text",
        )
        session.add(replacement)
        session.commit()

        assert replacement.id == old_annotation_id
        assert session.query(ub.KoboAnnotationMaterialization).filter_by(
            annotation_id=replacement.id,
        ).count() == 0

    def test_book_purge_can_retain_backup_snapshots(self, session, tmp_path):
        from cps import ub
        from cps.user_book_data import purge_user_book_data
        gz = tmp_path / "snapshot.json.gz"
        gz.write_bytes(b"x")
        session.add(ub.KoboAnnotationBackup(user_id=USER, book_id=LOSER, content_hash="h",
                                            file_path=str(gz), size_bytes=1, annotation_count=1))
        session.commit()

        purge_user_book_data(book_id=LOSER, session=session, remove_backup_files=False)
        session.commit()

        assert gz.exists()
        assert session.query(ub.KoboAnnotationBackup).count() == 1, (
            "remove_backup_files=False keeps the recovery snapshots indexed"
        )


@pytest.mark.unit
class TestCallSitesPinned:
    """The four enumeration sites must go through the helpers — RED on main."""

    def test_resolution_loop_migrates_user_data_before_delete(self):
        src = (REPO / "cps" / "duplicates.py").read_text(encoding="utf-8")
        body = src.split("def auto_resolve_duplicates", 1)[1].split("\ndef ", 1)[0]
        migrate = body.find("migrate_user_book_data(deleted_book_id, book_to_keep_id)")
        delete = body.find("delete_whole_book(deleted_book_id, book)")
        assert migrate != -1, (
            "resolving a duplicate must migrate per-user data (annotations, "
            "progress, shelves) to the kept book for EVERY strategy — D4 data loss"
        )
        assert delete != -1 and migrate < delete, (
            "migration must happen BEFORE the loser is deleted"
        )

    def test_delete_whole_book_purges_via_helper(self):
        src = (REPO / "cps" / "editbooks.py").read_text(encoding="utf-8")
        body = src.split("def delete_whole_book", 1)[1].split("\ndef ", 1)[0]
        assert "purge_user_book_data(book_id=book_id" in body
        for stale in ("ub.session.query(ub.BookShelf)", "ub.session.query(ub.ReadBook)",
                      "ub.session.query(ub.ArchivedBook)", "ub.delete_download("):
            assert stale not in body, f"hand-list remnant in delete_whole_book: {stale}"

    def test_admin_db_change_wipe_purges_via_helper(self):
        src = (REPO / "cps" / "admin.py").read_text(encoding="utf-8")
        idx = src.find("Calibre Database changed")
        block = src[idx:idx + 800]
        assert "purge_user_book_data()" in block
        assert "ub.session.query(ub.Downloads).delete()" not in block

    def test_admin_user_delete_purges_via_helper(self):
        src = (REPO / "cps" / "admin.py").read_text(encoding="utf-8")
        body = src.split("def _delete_user", 1)[1].split("\ndef ", 1)[0]
        assert "purge_user_book_data(user_id=content.id)" in body
        for stale in ("ub.ReadBook.user_id", "ub.Bookmark.user_id",
                      "ub.KoboSyncedBooks.user_id", "ub.KoboReadingState.user_id"):
            assert stale not in body, f"hand-list remnant in _delete_user: {stale}"
