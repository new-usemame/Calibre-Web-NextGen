# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 3 named deletion and Phase 4 collection state contracts."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services import device_capabilities


pytestmark = pytest.mark.unit


@pytest.fixture
def capability_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'capabilities.db'}")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _device(session, user_id, label):
    row = ub.Device(user_id=user_id, kind="koreader", display_name=label)
    session.add(row)
    session.flush()
    return row


def _inventory_item(session, device, *, book_id=42, lpath="Books/Named.epub"):
    report = ub.DeviceInventoryReport(
        device_id=device.id, item_count=1, matched_count=1,
    )
    session.add(report)
    session.flush()
    item = ub.DeviceInventoryItem(
        device_id=device.id, book_id=book_id, lpath=lpath,
        checksum="0123456789abcdef0123456789abcdef",
        size=1234, mtime=1_777_777_777, last_report_id=report.id,
    )
    session.add(item)
    session.flush()
    return item


def test_named_delete_is_claimed_and_only_confirmation_removes_observation(
        capability_session):
    device = _device(capability_session, 1, "Reader")
    item = _inventory_item(capability_session, device)

    deletion = device_capabilities.queue_named_deletion(
        session=capability_session, user_id=1,
        device_public_id=device.public_id, inventory_item_id=item.id,
    )
    claimed = device_capabilities.claim_next_deletion(
        session=capability_session, user_id=1, device_id=device.id,
    )

    assert claimed.id == deletion.id
    assert claimed.lpath == "Books/Named.epub"
    assert capability_session.query(ub.DeviceInventoryItem).count() == 1

    completed = device_capabilities.complete_deletion(
        session=capability_session, user_id=1, device_id=device.id,
        deletion_id=claimed.id, claim_token=claimed.claim_token,
        deleted=True,
    )

    assert completed.state == device_capabilities.COMPLETED
    assert capability_session.query(ub.DeviceInventoryItem).count() == 0


def test_inventory_omission_never_creates_or_completes_a_delete(capability_session):
    device = _device(capability_session, 1, "Reader")
    item = _inventory_item(capability_session, device)
    empty_report = ub.DeviceInventoryReport(
        device_id=device.id, item_count=0, matched_count=0,
    )
    capability_session.add(empty_report)
    capability_session.flush()

    assert capability_session.get(ub.DeviceInventoryItem, item.id) is not None
    assert capability_session.query(ub.DeviceBookDeletion).count() == 0
    assert device_capabilities.claim_next_deletion(
        session=capability_session, user_id=1, device_id=device.id,
    ) is None


def test_named_delete_cannot_target_another_users_inventory(capability_session):
    first = _device(capability_session, 1, "First reader")
    second = _device(capability_session, 2, "Second reader")
    other_item = _inventory_item(capability_session, second)

    with pytest.raises(device_capabilities.CapabilityValidationError):
        device_capabilities.queue_named_deletion(
            session=capability_session, user_id=1,
            device_public_id=first.public_id, inventory_item_id=other_item.id,
        )

    assert capability_session.query(ub.DeviceBookDeletion).count() == 0


def test_failed_named_delete_can_be_requested_again(capability_session):
    device = _device(capability_session, 1, "Reader")
    item = _inventory_item(capability_session, device)
    deletion = device_capabilities.queue_named_deletion(
        session=capability_session, user_id=1,
        device_public_id=device.public_id, inventory_item_id=item.id,
    )
    claimed = device_capabilities.claim_next_deletion(
        session=capability_session, user_id=1, device_id=device.id,
    )
    device_capabilities.complete_deletion(
        session=capability_session, user_id=1, device_id=device.id,
        deletion_id=claimed.id, claim_token=claimed.claim_token,
        deleted=False, failure_reason="checksum mismatch",
    )
    failed_token = deletion.claim_token

    retried = device_capabilities.queue_named_deletion(
        session=capability_session, user_id=1,
        device_public_id=device.public_id, inventory_item_id=item.id,
    )

    assert retried.id == deletion.id
    assert retried.state == device_capabilities.REQUESTED
    assert retried.claim_token != failed_token
    assert retried.failure_reason is None


def test_collection_snapshot_is_scoped_by_user_and_device(capability_session):
    first = _device(capability_session, 1, "First reader")
    second = _device(capability_session, 2, "Second reader")
    _inventory_item(capability_session, first, book_id=42, lpath="First/Book.epub")
    _inventory_item(capability_session, second, book_id=42, lpath="Second/Book.epub")
    first_shelf = ub.Shelf(id=10, uuid="shelf-first", name="Reading", user_id=1)
    second_shelf = ub.Shelf(id=20, uuid="shelf-second", name="Reading", user_id=2)
    capability_session.add_all([
        first_shelf, second_shelf,
        ub.BookShelf(book_id=42, ub_shelf=first_shelf, order=1),
        ub.BookShelf(book_id=42, ub_shelf=second_shelf, order=1),
    ])
    capability_session.flush()

    first_snapshot = device_capabilities.collection_snapshot(
        session=capability_session, user_id=1, device_id=first.id,
    )
    second_snapshot = device_capabilities.collection_snapshot(
        session=capability_session, user_id=2, device_id=second.id,
    )

    assert first_snapshot["scope"] != second_snapshot["scope"]
    assert first_snapshot["collections"] == [{
        "id": "shelf-first", "name": "Reading", "books": ["First/Book.epub"],
    }]
    assert second_snapshot["collections"] == [{
        "id": "shelf-second", "name": "Reading", "books": ["Second/Book.epub"],
    }]

    device_capabilities.acknowledge_collections(
        session=capability_session, user_id=1, device_id=first.id,
        revision=first_snapshot["revision"],
    )
    rows = capability_session.query(ub.DeviceCollectionSync).order_by(
        ub.DeviceCollectionSync.user_id,
    ).all()
    assert len(rows) == 2
    assert rows[0].applied_at is not None
    assert rows[1].applied_at is None
