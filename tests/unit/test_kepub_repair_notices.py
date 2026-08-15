# -*- coding: utf-8 -*-
"""Regression coverage for generic notices and resumable KEPUB repair."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services.user_notices import (
    create_notice_event,
    dismiss_notice,
    dismiss_notices,
    list_active_notices,
)
from cps.tasks.kepub_package_repair import (
    REPAIR_STATUS_COMPLETED,
    REPAIR_STATUS_FILE_REPAIRED,
    process_kepub_candidate,
)


@pytest.fixture
def app_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(
        engine,
        tables=[
            ub.NoticeEvent.__table__,
            ub.UserNoticeDelivery.__table__,
            ub.KepubPackageRepair.__table__,
            ub.KoboSyncedBooks.__table__,
        ],
    )
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()


def _book_notice(session, *, occurrence, users=(11,), book_id=7):
    return create_notice_event(
        session,
        notice_type="test-book-repair",
        occurrence_key=occurrence,
        scope="book",
        audience_user_ids=users,
        book_id=book_id,
        book_uuid="11111111-2222-3333-4444-555555555555",
        title_snapshot="A repaired book",
        payload={"message_key": "test_repair"},
    )


@pytest.mark.unit
def test_dismissing_first_occurrence_does_not_suppress_second(app_session):
    first = _book_notice(app_session, occurrence="occurrence-1")
    dismiss_notice(app_session, user_id=11, notice_id=first.id)
    second = _book_notice(app_session, occurrence="occurrence-2")

    active = list_active_notices(app_session, user_id=11)

    assert [item.id for item in active] == [second.id]


@pytest.mark.unit
def test_bulk_dismissal_is_idempotent_and_scoped_to_current_user(app_session):
    first = _book_notice(app_session, occurrence="bulk-1", users=(11, 12))
    second = _book_notice(app_session, occurrence="bulk-2", users=(11, 12), book_id=8)

    assert dismiss_notices(app_session, user_id=11, notice_ids=[first.id, second.id]) == 2
    assert dismiss_notices(app_session, user_id=11, notice_ids=[first.id, second.id]) == 0
    assert list_active_notices(app_session, user_id=11) == []
    assert {item.id for item in list_active_notices(app_session, user_id=12)} == {
        first.id, second.id,
    }


@pytest.mark.unit
def test_notice_scope_check_accepts_global_and_book_rows_and_rejects_mismatch(app_session):
    create_notice_event(
        app_session,
        notice_type="test-global",
        occurrence_key="global-1",
        scope="global",
        audience_user_ids=(11,),
        payload={},
    )
    _book_notice(app_session, occurrence="book-1")

    app_session.add(ub.NoticeEvent(
        notice_type="broken",
        occurrence_key="broken-1",
        scope="global",
        book_id=7,
        payload_json={},
        created_at=datetime.now(timezone.utc),
        active=True,
    ))
    with pytest.raises(IntegrityError):
        app_session.commit()
    app_session.rollback()

    app_session.add(ub.NoticeEvent(
        notice_type="broken",
        occurrence_key="broken-2",
        scope="book",
        book_id=None,
        payload_json={},
        created_at=datetime.now(timezone.utc),
        active=True,
    ))
    with pytest.raises(IntegrityError):
        app_session.commit()
    app_session.rollback()


@pytest.mark.unit
def test_listing_can_filter_type_and_records_first_presentation(app_session):
    wanted = _book_notice(app_session, occurrence="presented-1")
    create_notice_event(
        app_session,
        notice_type="another-consumer",
        occurrence_key="presented-2",
        scope="global",
        audience_user_ids=(11,),
        payload={"message": "Another notice"},
    )

    active = list_active_notices(
        app_session, user_id=11, notice_type="test-book-repair", mark_presented=True,
    )

    assert [event.id for event in active] == [wanted.id]
    wanted_delivery = app_session.get(ub.UserNoticeDelivery, (wanted.id, 11))
    assert wanted_delivery.first_presented_at is not None
    other_delivery = app_session.query(ub.UserNoticeDelivery).filter(
        ub.UserNoticeDelivery.event_id != wanted.id,
    ).one()
    assert other_delivery.first_presented_at is None


@pytest.mark.unit
def test_repair_notice_targets_only_users_with_kobo_sync_record(app_session):
    app_session.add_all([
        ub.KoboSyncedBooks(user_id=21, book_id=9),
        ub.KoboSyncedBooks(user_id=22, book_id=9),
        ub.KoboSyncedBooks(user_id=23, book_id=10),
    ])
    app_session.commit()

    event = create_notice_event(
        app_session,
        notice_type="kepub-package-repair",
        occurrence_key="audience-1",
        scope="book",
        audience_user_ids=[row[0] for row in app_session.query(ub.KoboSyncedBooks.user_id)
                           .filter(ub.KoboSyncedBooks.book_id == 9).distinct().all()],
        book_id=9,
        payload={},
    )

    assert event is not None
    assert {row.user_id for row in event.deliveries} == {21, 22}
    no_audience = create_notice_event(
        app_session,
        notice_type="kepub-package-repair",
        occurrence_key="audience-empty",
        scope="book",
        audience_user_ids=(),
        book_id=11,
        payload={},
    )
    assert no_audience is None


@pytest.mark.unit
def test_notice_table_migration_is_idempotent():
    from cps.ub import migrate_notice_tables

    engine = create_engine("sqlite:///:memory:", future=True)
    migrate_notice_tables(engine, None)
    migrate_notice_tables(engine, None)

    assert {"notice_event", "user_notice_delivery", "kepub_package_repair"} <= set(
        inspect(engine).get_table_names()
    )


class _Book:
    id = 31
    uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    title = "Crash-safe book"
    last_modified = None


class _Data:
    book = 31
    uncompressed_size = 100


@pytest.mark.unit
def test_repair_resumes_after_crash_between_file_repair_and_metadata_bump(
        app_session, tmp_path):
    package = tmp_path / "book.kepub"
    package.write_bytes(b"before")
    calls = {"normalize": 0, "mark": 0}

    def normalize(_path):
        calls["normalize"] += 1
        package.write_bytes(b"after")
        return True

    def crashing_mark(_book):
        calls["mark"] += 1
        raise RuntimeError("simulated metadata DB outage")

    with pytest.raises(RuntimeError, match="metadata DB outage"):
        process_kepub_candidate(
            app_session=app_session, book=_Book(), data=_Data(), path=package,
            normalize=normalize, mark_modified=crashing_mark, commit_metadata=lambda: None,
        )

    repair = app_session.query(ub.KepubPackageRepair).one()
    assert repair.status == REPAIR_STATUS_FILE_REPAIRED
    assert package.read_bytes() == b"after"

    def should_not_normalize_again(_path):
        raise AssertionError("a file-repaired occurrence must resume after normalization")

    process_kepub_candidate(
        app_session=app_session, book=_Book(), data=_Data(), path=package,
        normalize=should_not_normalize_again,
        mark_modified=lambda book: setattr(book, "last_modified", datetime.now(timezone.utc)),
        commit_metadata=lambda: None,
    )

    app_session.refresh(repair)
    assert repair.status == REPAIR_STATUS_COMPLETED
    assert calls["normalize"] == 1


@pytest.mark.unit
def test_clean_candidate_creates_no_repair_state_and_does_no_followup_work(
        app_session, tmp_path):
    package = tmp_path / "clean.kepub"
    package.write_bytes(b"clean")
    calls = {"mark": 0, "commit": 0}

    result = process_kepub_candidate(
        app_session=app_session, book=_Book(), data=_Data(), path=package,
        inspect_package=lambda _path: False,
        normalize=lambda _path: pytest.fail("a clean archive must not be rewritten"),
        mark_modified=lambda _book: calls.__setitem__("mark", calls["mark"] + 1),
        commit_metadata=lambda: calls.__setitem__("commit", calls["commit"] + 1),
    )

    assert result == "clean"
    assert app_session.query(ub.KepubPackageRepair).count() == 0
    assert calls == {"mark": 0, "commit": 0}


@pytest.mark.unit
def test_affected_candidate_is_backed_up_before_normalization(app_session, tmp_path):
    package = tmp_path / "affected.kepub"
    package.write_bytes(b"defective")
    order = []

    def backup(_path, _book, _occurrence, expected_sha256):
        order.append(("backup", expected_sha256))
        return str(tmp_path / "verified-backup.kepub")

    def normalize(_path):
        order.append(("normalize", None))
        package.write_bytes(b"repaired")
        return True

    process_kepub_candidate(
        app_session=app_session, book=_Book(), data=_Data(), path=package,
        inspect_package=lambda _path: True, normalize=normalize,
        backup_original=backup,
        mark_modified=lambda book: setattr(book, "last_modified", datetime.now(timezone.utc)),
        commit_metadata=lambda: None,
    )

    assert [step for step, _ in order] == ["backup", "normalize"]
    repair = app_session.query(ub.KepubPackageRepair).one()
    assert repair.backup_path.endswith("verified-backup.kepub")
    assert repair.source_sha256 == order[0][1]
