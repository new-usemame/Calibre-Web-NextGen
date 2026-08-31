# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP lifecycle for named deletion and shelf collections."""

from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services import device_capabilities, device_registry


pytestmark = pytest.mark.unit


@pytest.fixture
def protocol(tmp_path, monkeypatch):
    import sys
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    module = sys.modules["cps.progress_syncing.protocols.kosync"]
    engine = create_engine(f"sqlite:///{tmp_path / 'capability-protocol.db'}")
    session = sessionmaker(bind=engine)()
    ub.Base.metadata.create_all(engine)
    user = SimpleNamespace(id=1, role_download=lambda: True)
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(module, "ub", ub)
    monkeypatch.setattr(module, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(module, "authenticate_user", lambda: user)
    app = Flask(__name__)
    app.secret_key = "capability-protocol-secret"
    app.register_blueprint(module.kosync)
    with app.app_context():
        internal_id = device_registry.register_koreader_device_best_effort(
            user_id=1, device_id="runtime-reader", device_name="Reader",
        )
    device = session.get(ub.Device, internal_id)
    yield app.test_client(), session, user, device
    session.close()
    engine.dispose()


def _identity():
    return {"device": "Reader", "device_id": "runtime-reader"}


def test_named_delete_claim_and_confirmation_are_idempotent(protocol):
    client, session, _user, device = protocol
    report = ub.DeviceInventoryReport(device_id=device.id, item_count=1, matched_count=1)
    session.add(report)
    session.flush()
    item = ub.DeviceInventoryItem(
        device_id=device.id, book_id=42, lpath="Books/Named.epub",
        checksum="0123456789abcdef0123456789abcdef", size=12, mtime=34,
        last_report_id=report.id,
    )
    session.add(item)
    session.flush()
    requested = device_capabilities.queue_named_deletion(
        session=session, user_id=1, device_public_id=device.public_id,
        inventory_item_id=item.id,
    )
    session.commit()

    claimed = client.post("/kosync/syncs/deletions/claim", json=_identity())
    deletion = claimed.get_json()["deletion"]
    completed = client.put("/kosync/syncs/deletions/complete", json={
        **_identity(), "deletion_id": deletion["id"],
        "claim_token": deletion["claim_token"], "deleted": True,
    })
    repeated = client.put("/kosync/syncs/deletions/complete", json={
        **_identity(), "deletion_id": deletion["id"],
        "claim_token": deletion["claim_token"], "deleted": True,
    })

    assert claimed.status_code == 200
    assert deletion["id"] == requested.id
    assert deletion["lpath"] == "Books/Named.epub"
    assert completed.status_code == repeated.status_code == 200
    assert session.query(ub.DeviceInventoryItem).count() == 0


def test_collection_snapshot_and_ack_are_bound_to_authenticated_user_and_device(protocol):
    client, session, user, device = protocol
    shelf = ub.Shelf(id=10, uuid="server-shelf", name="Reading", user_id=1)
    session.add_all([shelf, ub.BookShelf(book_id=42, ub_shelf=shelf, order=1)])
    report = ub.DeviceInventoryReport(device_id=device.id, item_count=1, matched_count=1)
    session.add(report)
    session.flush()
    session.add(ub.DeviceInventoryItem(
        device_id=device.id, book_id=42, lpath="Books/Here.epub",
        checksum="0123456789abcdef0123456789abcdef", size=12, mtime=34,
        last_report_id=report.id,
    ))
    session.commit()

    response = client.post("/kosync/syncs/collections", json=_identity())
    snapshot = response.get_json()
    ack = client.put("/kosync/syncs/collections/complete", json={
        **_identity(), "revision": snapshot["revision"],
    })
    user.id = 2
    crossed = client.put("/kosync/syncs/collections/complete", json={
        **_identity(), "revision": snapshot["revision"],
    })

    assert response.status_code == 200
    assert snapshot["collections"] == [{
        "id": "server-shelf", "name": "Reading", "books": ["Books/Here.epub"],
    }]
    assert ack.status_code == 200
    assert crossed.status_code == 409
