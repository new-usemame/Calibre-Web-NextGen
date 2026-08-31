# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 1 device-inventory contract for the CWNGSync KOReader plugin."""

from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub


UNKNOWN_CHECKSUM = "0123456789abcdef0123456789abcdef"
EPUB_CHECKSUM = "11111111111111111111111111111111"
MOBI_CHECKSUM = "22222222222222222222222222222222"


@pytest.fixture
def inventory_protocol(tmp_path, monkeypatch):
    import sys
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    module = sys.modules["cps.progress_syncing.protocols.kosync"]

    engine = create_engine(f"sqlite:///{tmp_path / 'inventory.db'}")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    current_user = SimpleNamespace(id=1)

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(module, "ub", ub)
    monkeypatch.setattr(module, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(module, "authenticate_user", lambda: current_user)

    matches = {
        EPUB_CHECKSUM: (42, "EPUB", "Same work", "Author/Same work", "koreader"),
        MOBI_CHECKSUM: (42, "MOBI", "Same work", "Author/Same work", "koreader"),
    }
    monkeypatch.setattr(
        module,
        "get_book_by_checksum",
        lambda checksum: matches.get(checksum, (None, None, None, None, None)),
    )

    app = Flask(__name__)
    app.secret_key = "inventory-test-secret"
    app.register_blueprint(module.kosync)
    yield app.test_client(), session, current_user
    session.close()
    engine.dispose()


def _entry(path, checksum, *, size=1234, mtime=1_777_777_777, book_id=None):
    entry = {
        "lpath": path,
        "checksum": checksum,
        "size": size,
        "mtime": mtime,
    }
    if book_id is not None:
        entry["book_id"] = book_id
    return entry


def _report(client, entries, *, device_id="runtime-device-id", device="KOReader",
            free_space=None, total_space=None):
    payload = {
        "device": device,
        "device_id": device_id,
        "inventory": entries,
    }
    if free_space is not None:
        payload["free_space"] = free_space
    if total_space is not None:
        payload["total_space"] = total_space
    return client.put("/kosync/syncs/inventory", json=payload)


@pytest.mark.unit
def test_inventory_report_persists_a_point_in_time_storage_measurement(
        inventory_protocol):
    client, session, _user = inventory_protocol

    response = _report(
        client, [], free_space=4_000_000, total_space=8_000_000,
    )

    assert response.status_code == 200
    snapshot = session.query(ub.DeviceStorageSnapshot).one()
    assert snapshot.free_bytes == 4_000_000
    assert snapshot.total_bytes == 8_000_000


@pytest.mark.unit
@pytest.mark.parametrize(("free_space", "total_space"), [
    (1, None), (None, 1), (-1, 1), (2, 1), (True, 1),
])
def test_inventory_rejects_incomplete_or_impossible_storage(
        inventory_protocol, free_space, total_space):
    client, session, _user = inventory_protocol

    response = _report(
        client, [], free_space=free_space, total_space=total_space,
    )

    assert response.status_code == 400
    assert session.query(ub.DeviceStorageSnapshot).count() == 0


@pytest.mark.unit
def test_inventory_preserves_checksum_the_server_has_never_seen(inventory_protocol):
    client, session, _user = inventory_protocol

    response = _report(client, [_entry("Books/Unknown.epub", UNKNOWN_CHECKSUM)])

    assert response.status_code == 200
    assert response.get_json()["accepted"] == 1
    assert response.get_json()["matched"] == 0
    item = session.query(ub.DeviceInventoryItem).one()
    assert item.checksum == UNKNOWN_CHECKSUM
    assert item.book_id is None
    assert item.device.user_id == 1


@pytest.mark.unit
def test_different_format_checksums_converge_on_the_same_work(inventory_protocol):
    client, session, _user = inventory_protocol

    response = _report(client, [
        _entry("Books/Same work.epub", EPUB_CHECKSUM, book_id=999),
        _entry("Books/Same work.mobi", MOBI_CHECKSUM),
    ])

    assert response.status_code == 200
    assert response.get_json()["matched"] == 2
    items = session.query(ub.DeviceInventoryItem).order_by(ub.DeviceInventoryItem.lpath).all()
    assert [item.book_id for item in items] == [42, 42]
    assert {item.checksum for item in items} == {EPUB_CHECKSUM, MOBI_CHECKSUM}
    # A client-supplied hint is never authoritative over checksum resolution.
    assert all(item.book_id != 999 for item in items)


@pytest.mark.unit
def test_same_hardware_identity_cannot_cross_bind_between_users(inventory_protocol):
    client, session, current_user = inventory_protocol
    raw_id = "identity-shared-by-two-authenticated-users"

    first = _report(client, [_entry("Books/One.epub", EPUB_CHECKSUM)], device_id=raw_id)
    current_user.id = 2
    second = _report(client, [_entry("Books/Two.mobi", MOBI_CHECKSUM)], device_id=raw_id)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.get_json()["error"] == "device_identity_unavailable"
    assert session.query(ub.DeviceInventoryItem).count() == 1
    assert session.query(ub.DeviceInventoryItem).one().device.user_id == 1


@pytest.mark.unit
def test_omitting_a_previously_reported_book_deletes_nothing(inventory_protocol):
    client, session, _user = inventory_protocol
    first = _report(client, [
        _entry("Books/One.epub", EPUB_CHECKSUM),
        _entry("Books/Two.mobi", MOBI_CHECKSUM),
    ])
    second = _report(client, [_entry("Books/One.epub", EPUB_CHECKSUM, size=4321)])

    assert first.status_code == 200
    assert second.status_code == 200
    assert session.query(ub.DeviceInventoryReport).count() == 2
    assert session.query(ub.DeviceInventoryItem).count() == 2
    omitted = session.query(ub.DeviceInventoryItem).filter_by(lpath="Books/Two.mobi").one()
    assert omitted.checksum == MOBI_CHECKSUM
    assert omitted.last_report_id != second.get_json()["report_id"]


@pytest.mark.unit
@pytest.mark.parametrize("payload", [
    None,
    {},
    {"device": "KOReader", "device_id": "id", "inventory": "not-a-list"},
    {"device": "KOReader", "device_id": "id", "inventory": [{}]},
    {"device": "KOReader", "device_id": "id", "inventory": [
        {"lpath": "../escape.epub", "checksum": UNKNOWN_CHECKSUM, "size": 1, "mtime": 1},
    ]},
    {"device": "KOReader", "device_id": "id", "inventory": [
        _entry("Books/Bad.epub", "not-a-checksum"),
    ]},
])
def test_malformed_inventory_is_rejected_without_a_500(inventory_protocol, payload):
    client, _session, _user = inventory_protocol

    if payload is None:
        response = client.put(
            "/kosync/syncs/inventory", data="{broken", content_type="application/json",
        )
    else:
        response = client.put("/kosync/syncs/inventory", json=payload)

    assert response.status_code == 400


@pytest.mark.unit
def test_oversized_inventory_is_rejected_without_a_500(inventory_protocol):
    client, _session, _user = inventory_protocol
    oversized = {
        "device": "KOReader",
        "device_id": "id",
        "inventory": [_entry(f"Books/{index}.epub", UNKNOWN_CHECKSUM) for index in range(5001)],
    }

    response = client.put("/kosync/syncs/inventory", json=oversized)

    assert response.status_code == 413
