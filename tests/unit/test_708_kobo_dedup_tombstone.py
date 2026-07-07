"""Regression test for fork #708 — Kobo sync duplicating books.

When the duplicate scanner auto-removes the losing copy of a book, the Kobo must
be told to drop it: kobo_sync_status.record_book_deletion snapshots a
kobo_deleted_book tombstone for every user who had the book on a device, and the
sync handler emits a DeletedEntitlement from it. record_book_deletion learns the
affected users by reading the book's kobo_synced_books rows.

The bug: the dedup path ran user_book_data.migrate_user_book_data FIRST, and that
function deletes the loser's kobo_synced_books rows (the kept book's file is a
different file, so the "already delivered" marker must not migrate). By the time
record_book_deletion ran there were no synced rows left, so no tombstone was
recorded and the removed duplicate lingered on the Kobo forever (reporter
@Chronosmage-alt). The fix records the tombstone BEFORE the migration.
"""
import inspect

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub, kobo_sync_status, user_book_data

pytestmark = pytest.mark.unit


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _tombstone_count(session, user_id, uuid):
    return session.query(ub.KoboDeletedBook).filter(
        ub.KoboDeletedBook.user_id == user_id,
        ub.KoboDeletedBook.book_uuid == uuid).count()


def test_tombstone_recorded_when_deletion_precedes_migration(session):
    """The fixed order: record the tombstone, THEN migrate. The user who had the
    loser synced to a device gets a tombstone for its UUID."""
    session.add(ub.KoboSyncedBooks(user_id=1, book_id=42))
    session.commit()

    # Fixed order (mirrors duplicates.py after the #708 fix)
    kobo_sync_status.record_book_deletion(42, "uuid-loser", session=session)
    user_book_data.migrate_user_book_data(42, 99, session=session)
    session.commit()

    assert _tombstone_count(session, 1, "uuid-loser") == 1, \
        "deleted duplicate must leave a Kobo tombstone so the device drops it (#708)"
    # the loser's synced marker is gone either way (record_book_deletion clears it)
    assert session.query(ub.KoboSyncedBooks).filter_by(book_id=42).count() == 0


def test_migration_before_deletion_loses_tombstone_documents_the_bug(session):
    """The old order silently drops the tombstone — this documents exactly why
    #708 happened and guards against re-introducing that ordering."""
    session.add(ub.KoboSyncedBooks(user_id=1, book_id=42))
    session.commit()

    # Buggy order: migrate first (deletes kobo_synced_books), then try to record.
    user_book_data.migrate_user_book_data(42, 99, session=session)
    kobo_sync_status.record_book_deletion(42, "uuid-loser", session=session)
    session.commit()

    assert _tombstone_count(session, 1, "uuid-loser") == 0, \
        "sanity: proves the migrate-first order is what dropped the tombstone"


def test_tombstone_recorded_for_every_synced_user(session):
    session.add(ub.KoboSyncedBooks(user_id=1, book_id=42))
    session.add(ub.KoboSyncedBooks(user_id=2, book_id=42))
    session.commit()

    kobo_sync_status.record_book_deletion(42, "uuid-loser", session=session)
    session.commit()

    assert _tombstone_count(session, 1, "uuid-loser") == 1
    assert _tombstone_count(session, 2, "uuid-loser") == 1


def test_dedup_source_records_deletion_before_migration():
    """Source-pin: the auto-resolve path must call record_book_deletion before
    migrate_user_book_data, or the tombstone is dropped (#708)."""
    from cps import duplicates
    src = inspect.getsource(duplicates)
    rec = src.find("record_book_deletion")
    mig = src.find("migrate_user_book_data(deleted_book_id")
    assert rec != -1 and mig != -1
    assert rec < mig, \
        "record_book_deletion must precede migrate_user_book_data in the dedup path (#708)"
