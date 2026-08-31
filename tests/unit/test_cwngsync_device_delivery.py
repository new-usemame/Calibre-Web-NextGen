# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 2 wanted-queue contract for device-initiated book delivery."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services import device_delivery


pytestmark = pytest.mark.unit


@pytest.fixture
def delivery_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'delivery.db'}")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _device(session, *, user_id=1, kind="koreader", label="Reader"):
    row = ub.Device(
        user_id=user_id,
        kind=kind,
        display_name=label,
        model=label,
        platform="koreader" if kind == "koreader" else "nickel",
    )
    session.add(row)
    session.flush()
    return row


def _book(book_id=42, *formats):
    data = [
        SimpleNamespace(
            format=book_format.upper(),
            name=f"Book {book_id}",
            uncompressed_size=1000 + index,
        )
        for index, book_format in enumerate(formats)
    ]
    return SimpleNamespace(id=book_id, title=f"Book {book_id}", data=data)


def _queue(session, device, book):
    return device_delivery.queue_book_for_device(
        session=session,
        user_id=device.user_id,
        device_public_id=device.public_id,
        book=book,
    )


def test_queue_claim_complete_lifecycle_and_repeat_claim_is_stable(delivery_session):
    device = _device(delivery_session)
    queued = _queue(delivery_session, device, _book(42, "AZW3", "EPUB", "PDF"))

    assert queued.created is True
    assert queued.delivery.state == device_delivery.QUEUED
    assert queued.delivery.format == "EPUB", "KOReader delivery must prefer EPUB"

    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    first = device_delivery.claim_next_delivery(
        session=delivery_session, user_id=1, device_id=device.id, now=now,
    )
    second = device_delivery.claim_next_delivery(
        session=delivery_session,
        user_id=1,
        device_id=device.id,
        now=now + timedelta(minutes=1),
    )

    assert first.id == queued.delivery.id == second.id
    assert first.claim_token == second.claim_token
    assert first.attempt_count == 1

    completed = device_delivery.complete_delivery(
        session=delivery_session,
        user_id=1,
        device_id=device.id,
        delivery_id=first.id,
        claim_token=first.claim_token,
        lpath="Book 42.epub",
        checksum="0123456789abcdef0123456789abcdef",
        size=1001,
        mtime=1_777_777_777,
        now=now + timedelta(minutes=2),
    )
    repeated = device_delivery.complete_delivery(
        session=delivery_session,
        user_id=1,
        device_id=device.id,
        delivery_id=first.id,
        claim_token=first.claim_token,
        lpath="Book 42.epub",
        checksum="0123456789abcdef0123456789abcdef",
        size=1001,
        mtime=1_777_777_777,
        now=now + timedelta(minutes=3),
    )

    assert completed.state == device_delivery.COMPLETED
    assert repeated.id == completed.id
    assert delivery_session.query(ub.DeviceBookDelivery).count() == 1


def test_book_observed_in_inventory_is_never_queued_or_claimed(delivery_session):
    device = _device(delivery_session)
    report = ub.DeviceInventoryReport(
        device_id=device.id, item_count=1, matched_count=1,
    )
    delivery_session.add(report)
    delivery_session.flush()
    delivery_session.add(ub.DeviceInventoryItem(
        device_id=device.id,
        lpath="Already here.epub",
        checksum="11111111111111111111111111111111",
        book_id=42,
        size=2000,
        mtime=1_777_777_777,
        last_report_id=report.id,
    ))
    delivery_session.flush()

    result = _queue(delivery_session, device, _book(42, "EPUB"))

    assert result.created is False
    assert result.delivery is None
    assert result.reason == "already_on_device"
    assert delivery_session.query(ub.DeviceBookDelivery).count() == 0
    assert device_delivery.claim_next_delivery(
        session=delivery_session, user_id=1, device_id=device.id,
    ) is None


def test_queue_refuses_book_larger_than_latest_reported_free_space(delivery_session):
    device = _device(delivery_session)
    delivery_session.add(ub.DeviceStorageSnapshot(
        device_id=device.id, free_bytes=900, total_bytes=10_000,
    ))
    delivery_session.flush()

    result = _queue(delivery_session, device, _book(42, "EPUB"))

    assert result.created is False
    assert result.delivery is None
    assert result.reason == "insufficient_storage"
    assert delivery_session.query(ub.DeviceBookDelivery).count() == 0


def test_claim_uses_fresh_space_and_device_refusal_requeues_without_new_token(
        delivery_session):
    device = _device(delivery_session)
    queued = _queue(delivery_session, device, _book(42, "EPUB")).delivery
    token = queued.claim_token

    assert device_delivery.claim_next_delivery(
        session=delivery_session, user_id=1, device_id=device.id,
        available_bytes=999,
    ) is None
    assert queued.state == device_delivery.QUEUED
    assert queued.attempt_count == 0

    claimed = device_delivery.claim_next_delivery(
        session=delivery_session, user_id=1, device_id=device.id,
        available_bytes=10_000,
    )
    refused = device_delivery.refuse_delivery(
        session=delivery_session, user_id=1, device_id=device.id,
        delivery_id=claimed.id, claim_token=claimed.claim_token,
        reason="insufficient_storage", available_bytes=500,
    )

    assert refused.state == device_delivery.QUEUED
    assert refused.claim_token == token
    assert refused.claim_expires_at is None
    assert refused.failure_reason == "insufficient_storage (500 bytes available)"


def test_abandoned_claim_becomes_reclaimable_after_the_lease(delivery_session):
    device = _device(delivery_session)
    queued = _queue(delivery_session, device, _book(42, "EPUB")).delivery
    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    first = device_delivery.claim_next_delivery(
        session=delivery_session, user_id=1, device_id=device.id, now=now,
    )
    first_token = first.claim_token

    before_timeout = device_delivery.claim_next_delivery(
        session=delivery_session,
        user_id=1,
        device_id=device.id,
        now=now + device_delivery.CLAIM_TTL - timedelta(seconds=1),
    )
    before_token = before_timeout.claim_token
    after_timeout = device_delivery.claim_next_delivery(
        session=delivery_session,
        user_id=1,
        device_id=device.id,
        now=now + device_delivery.CLAIM_TTL + timedelta(seconds=1),
    )

    assert before_timeout.id == queued.id
    assert before_token == first_token
    assert after_timeout.id == queued.id
    assert after_timeout.claim_token == first_token
    assert after_timeout.attempt_count == 2
    assert after_timeout.claim_expires_at > now + device_delivery.CLAIM_TTL


def test_queue_assigns_one_stable_token_before_any_claim(delivery_session):
    device = _device(delivery_session)

    queued = _queue(delivery_session, device, _book(42, "EPUB")).delivery
    token_before_claim = queued.claim_token
    claimed = device_delivery.claim_next_delivery(
        session=delivery_session, user_id=1, device_id=device.id,
    )

    assert token_before_claim
    assert claimed.claim_token == token_before_claim


def test_requeue_and_interrupted_retry_never_duplicate_the_entry(delivery_session):
    device = _device(delivery_session)
    first = _queue(delivery_session, device, _book(42, "EPUB"))
    second = _queue(delivery_session, device, _book(42, "EPUB"))

    assert first.created is True
    assert second.created is False
    assert second.delivery.id == first.delivery.id
    assert delivery_session.query(ub.DeviceBookDelivery).count() == 1


def test_unsupported_only_book_is_a_loud_failed_queue_entry(delivery_session):
    device = _device(delivery_session)

    result = _queue(delivery_session, device, _book(42, "KFX", "AZW3"))

    assert result.created is True
    assert result.delivery.state == device_delivery.FAILED
    assert result.delivery.format is None
    assert "KFX" in result.reason
    assert "AZW3" in result.reason
    assert "readable" in result.reason.lower()
    assert device_delivery.claim_next_delivery(
        session=delivery_session, user_id=1, device_id=device.id,
    ) is None


def test_one_users_queue_is_invisible_to_another_users_device(delivery_session):
    first_device = _device(delivery_session, user_id=1, label="First reader")
    second_device = _device(delivery_session, user_id=2, label="Second reader")
    queued = _queue(delivery_session, first_device, _book(42, "EPUB")).delivery

    assert device_delivery.claim_next_delivery(
        session=delivery_session,
        user_id=2,
        device_id=second_device.id,
    ) is None
    assert device_delivery.get_delivery_for_download(
        session=delivery_session,
        user_id=2,
        device_id=second_device.id,
        delivery_id=queued.id,
        claim_token="not-the-owner-token",
    ) is None
    assert queued.state == device_delivery.QUEUED


@pytest.mark.parametrize("kind,formats,expected", [
    ("koreader", ["KFX", "AZW3", "PDF", "EPUB"], "EPUB"),
    ("koreader", ["KFX", "AZW3", "PDF"], "PDF"),
    ("kobo", ["PDF", "EPUB", "KEPUB"], "KEPUB"),
])
def test_format_selection_respects_the_device(kind, formats, expected):
    selected = device_delivery.select_device_format(
        kind, [SimpleNamespace(format=value) for value in formats],
    )

    assert selected.format == expected


def test_book_purge_removes_queue_but_preserves_inventory_observation(delivery_session):
    from cps import user_book_data

    device = _device(delivery_session)
    queued = _queue(delivery_session, device, _book(42, "EPUB")).delivery
    report = ub.DeviceInventoryReport(device_id=device.id, item_count=1, matched_count=1)
    delivery_session.add(report)
    delivery_session.flush()
    observation = ub.DeviceInventoryItem(
        device_id=device.id, lpath="Observed.epub", checksum="a" * 32,
        book_id=42, size=10, mtime=10, last_report_id=report.id,
    )
    delivery_session.add(observation)
    delivery_session.flush()
    queued_id = queued.id
    observation_id = observation.id

    user_book_data.purge_user_book_data(book_id=42, session=delivery_session)
    delivery_session.flush()
    delivery_session.expire_all()

    assert delivery_session.get(ub.DeviceBookDelivery, queued_id) is None
    assert delivery_session.get(ub.DeviceInventoryItem, observation_id).book_id is None


def test_book_merge_repoints_queue_and_inventory_match(delivery_session):
    from cps import user_book_data

    device = _device(delivery_session)
    queued = _queue(delivery_session, device, _book(42, "EPUB")).delivery
    report = ub.DeviceInventoryReport(device_id=device.id, item_count=1, matched_count=1)
    delivery_session.add(report)
    delivery_session.flush()
    observation = ub.DeviceInventoryItem(
        device_id=device.id, lpath="Observed.epub", checksum="b" * 32,
        book_id=42, size=10, mtime=10, last_report_id=report.id,
    )
    delivery_session.add(observation)
    delivery_session.flush()

    user_book_data.migrate_user_book_data(42, 84, session=delivery_session)
    delivery_session.flush()
    delivery_session.expire_all()

    assert delivery_session.get(ub.DeviceBookDelivery, queued.id).book_id == 84
    assert delivery_session.get(ub.DeviceInventoryItem, observation.id).book_id == 84
