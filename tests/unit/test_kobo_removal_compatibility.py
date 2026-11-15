# SPDX-License-Identifier: GPL-3.0-or-later
"""Owner-selected compatibility and archive/restore edges, outside fixed tests."""
from datetime import timedelta
import pytest
from flask import g
from cps import ub
from tests.unit.kobo_removal_world import KoboRemovalWorld, entitlements, removed, utcnow, observation

pytestmark = pytest.mark.unit


@pytest.fixture
def world(monkeypatch):
    w = KoboRemovalWorld(monkeypatch)
    yield w
    w.close()


def test_marker_only_legacy_kobo_keeps_main_departures_and_adds_no_visibility_removal(world):
    w = world
    for book_id in (1, 5):
        w.session.add(ub.KoboSyncedBooks(user_id=w.user.id, book_id=book_id, book_uuid=w.book(book_id).uuid))
    w.session.commit()
    w.hide(1)  # main's raw membership leaves this marker alone
    assert not w.ledger(w.a)
    response = w.sync(w.a, w.old_cursor(w.a))
    assert removed(response) == {5}  # off the account shelf, as main removes
    assert 1 not in removed(w.settle(w.a))
    # Sealing did not destroy legacy evidence; a later raw membership departure
    # still removes the marker even though the device never got a ledger row.
    w.unshelve(w.to_read, 1)
    assert removed(w.settle(w.a)) == {1}


def test_sealed_kobo_does_not_remove_another_kobos_account_marker(world):
    w = world
    w.set_scope(w.a, 'shelves', [w.kindle])
    w.settle(w.a)
    w.settle(w.b)
    w.unshelve(w.to_read, 3)
    assert removed(w.settle(w.a)) == set()
    assert removed(w.settle(w.b)) == {3}


@pytest.mark.parametrize('cursor_selects', [False, True])
def test_archived_held_book_receives_one_removal_then_stays_removed(world, cursor_selects):
    w = world
    w.settle(w.a)
    w.session.add(ub.ArchivedBook(user_id=w.user.id, book_id=1,
        is_archived=True, last_modified=utcnow()))
    if cursor_selects:
        w.book(1).last_modified = utcnow()
    w.session.commit()
    response = w.sync(w.a)
    assert [e for e in entitlements(response) if e[1] == 1] == [('ChangedEntitlement', 1, True)]
    assert removed(w.settle(w.a)) == set()
    assert 1 not in w.ledger(w.a)


def test_post_download_cover_put_does_not_erase_saved_own_observation(world):
    w = world
    w.settle(w.a)
    w.put_state(w.a, 1, 40, utcnow() - timedelta(hours=1))
    saved = observation(w.journal(w.a, 1))
    w.download(w.a, 1)
    w.put_state(w.a, 1, 0, utcnow())
    assert observation(w.journal(w.a, 1)) == saved
    assert w.get_state(w.a, 1)['CurrentBookmark']['ProgressPercent'] == 40


def test_unchecked_own_restore_sync_replays_its_own_state(world):
    w = world
    w.settle(w.a)
    w.put_state(w.a, 1, 40, utcnow() - timedelta(hours=1))
    w.download(w.a, 1)
    incoming_token = w.tokens[w.a.id]
    response = w.sync(w.a)
    states = [item['ChangedReadingState']['ReadingState'] for item in response.get_json()
              if 'ChangedReadingState' in item]
    assert [s['CurrentBookmark']['ProgressPercent'] for s in states] == [40]
    # The same incoming token replays the pending page, as a lost response does.
    with w.app.test_request_context('/v1/library/sync', headers={w.header: incoming_token}):
        g.annotation_origin_device_id = w.a.id
        replay = w.kobo.HandleSyncRequest.__wrapped__()
    assert replay.get_json() == response.get_json()


def test_checked_library_sync_restores_own_newer_place_and_does_not_consume_it_unserved(world):
    w = world
    w.settle(w.a)
    w.put_state(w.a, 1, 40, utcnow() - timedelta(hours=1))
    w.seed_shared(1, 10, utcnow() - timedelta(days=30))
    w.share(w.a, True)
    w.download(w.a, 1)
    response = w.sync(w.a)
    states = [item['ChangedReadingState']['ReadingState'] for item in response.get_json()
              if 'ChangedReadingState' in item]
    assert [s['CurrentBookmark']['ProgressPercent'] for s in states] == [40]


def test_hard_delete_holds_unsent_activity_then_ack_clears_only_own_delivery(world, monkeypatch):
    from cps import db
    w = world
    w.settle(w.a)
    w.settle(w.b)
    w.put_state(w.a, 1, 40, utcnow())
    saved = observation(w.journal(w.a, 1))
    uuid = w.book(1).uuid
    w.session.query(db.Data).filter_by(book=1).delete()
    w.session.query(ub.BookShelf).filter_by(book_id=1).delete()
    w.session.delete(w.book(1))
    w.session.add(ub.KoboDeletedBook(user_id=w.user.id, book_uuid=uuid, deleted_at=utcnow()))
    w.session.commit()
    monkeypatch.setattr(w.kobo, 'KOBO_REMOVAL_CAPTURE_HOLD_SYNCS', 1)
    assert not w.sync(w.a).get_json()
    assert any(item.get('ChangedEntitlement', {}).get('BookEntitlement', {}).get('Id') == uuid
               for item in w.sync(w.a).get_json())
    w.settle(w.a)
    assert 1 not in w.ledger(w.a)
    assert 1 in w.ledger(w.b)
    assert observation(w.journal(w.a, 1)) == saved


def test_removal_schema_upgrade_and_downgrade_preserve_reading_clocks():
    from sqlalchemy import create_engine, text, inspect
    from cps.migrations.versions.kobo_device_removal import upgrade, downgrade
    engine = create_engine('sqlite://')
    ub.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE kobo_device_book_removal'))
        conn.execute(text('ALTER TABLE device_reading_position DROP COLUMN rehydrate_armed_at'))
        conn.execute(text("INSERT INTO device_reading_position (device_id,book_id,progress_percent,server_modified_at,client_modified_at,status_modified_at,status_received_at,rehydrate_needed) VALUES (1,1,40,'2026-01-01','2025-12-31','2025-12-30','2025-12-29',1)"))
        before = conn.execute(text('SELECT * FROM device_reading_position')).one()
        upgrade(conn)
        upgrade(conn)
        after = conn.execute(text('SELECT * FROM device_reading_position')).one()
        assert tuple(after[:-1]) == tuple(before)
        assert after[-1] == '2026-01-01'
        assert 'kobo_device_book_removal' in inspect(conn).get_table_names()
        downgrade(conn)
        assert conn.execute(text('SELECT * FROM device_reading_position')).one() == before
        assert 'kobo_device_book_removal' not in inspect(conn).get_table_names()
    engine.dispose()


def test_new_kobo_never_inherits_a_previous_kobos_legacy_markers(world):
    w = world
    w.settle(w.a)
    w.session.query(ub.KoboDeviceEntitlementSeed).filter_by(device_id=w.b.id).delete()
    w.b.first_seen_at = utcnow() + timedelta(seconds=1)
    w.session.commit()
    w.unshelve(w.to_read, 3)
    w.set_scope(w.b, 'shelves', [w.kindle])
    assert not w.ledger(w.b)
    assert removed(w.settle(w.b)) == set()


def test_permanent_delete_retains_only_uuid_capture_until_own_ack(world, monkeypatch):
    from cps import db, kobo_sync_status
    from cps.user_book_data import purge_user_book_data
    w = world
    w.settle(w.a)
    w.settle(w.b)
    w.put_state(w.a, 1, 40, utcnow())
    uuid = w.book(1).uuid
    kobo_sync_status.record_book_deletion(1, uuid)
    purge_user_book_data(book_id=1, session=w.session)
    w.session.query(db.Data).filter_by(book=1).delete()
    w.session.delete(w.book(1))
    w.session.commit()
    assert w.journal(w.a, 1) is None  # permanent purge's existing content policy
    assert w.session.get(ub.KoboDeviceDeletionCapture, (w.a.id, uuid)) is not None
    monkeypatch.setattr(w.kobo, 'KOBO_REMOVAL_CAPTURE_HOLD_SYNCS', 1)
    assert not w.sync(w.a).get_json()
    assert any(item.get('ChangedEntitlement', {}).get('BookEntitlement', {}).get('Id') == uuid
               for item in w.sync(w.a).get_json())
    w.settle(w.a)
    assert w.session.get(ub.KoboDeviceDeletionCapture, (w.a.id, uuid)) is None
    assert any(item.get('ChangedEntitlement', {}).get('BookEntitlement', {}).get('Id') == uuid
               for item in w.sync(w.b).get_json())
