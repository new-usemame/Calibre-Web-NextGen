# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP contract for claiming and completing pull-based device deliveries."""

from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services import device_delivery, device_registry


pytestmark = pytest.mark.unit


@pytest.fixture
def delivery_protocol(tmp_path, monkeypatch):
    import sys
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    module = sys.modules["cps.progress_syncing.protocols.kosync"]

    engine = create_engine(f"sqlite:///{tmp_path / 'delivery-protocol.db'}")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    current_user = SimpleNamespace(
        id=1,
        role_download=lambda: True,
        book_visible=True,
        filtered_book_calls=[],
    )

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(module, "ub", ub)
    monkeypatch.setattr(module, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(module, "authenticate_user", lambda: current_user)

    app = Flask(__name__)
    app.secret_key = "delivery-protocol-secret"
    app.register_blueprint(module.kosync)
    with app.app_context():
        internal_id = device_registry.register_koreader_device_best_effort(
            user_id=1, device_id="first-runtime-device", device_name="First reader",
        )
    device = session.query(ub.Device).filter_by(id=internal_id).one()
    book = SimpleNamespace(
        id=42,
        title="Queued book",
        path="Author/Queued book",
        data=[SimpleNamespace(
            format="EPUB", name="Queued book", uncompressed_size=1234,
        )],
    )
    from cps import calibre_db

    def get_filtered_book(book_id, *args, **kwargs):
        explicit_user = kwargs.get("user")
        current_user.filtered_book_calls.append({
            "book_id": book_id,
            "user": explicit_user,
            "allow_show_archived": kwargs.get("allow_show_archived", False),
            "allow_show_hidden": kwargs.get("allow_show_hidden", False),
        })
        if (book_id == book.id and explicit_user is current_user
                and current_user.book_visible):
            return book
        return None

    monkeypatch.setattr(calibre_db, "get_filtered_book", get_filtered_book)
    # Keep the old unfiltered path live so the revocation test is genuinely red
    # until the download route switches to the explicit-user filtered lookup.
    monkeypatch.setattr(
        calibre_db, "get_book",
        lambda book_id: book if book_id == book.id else None,
    )
    visible_book = calibre_db.get_filtered_book(
        book.id,
        allow_show_archived=True,
        allow_show_hidden=True,
        user=current_user,
    )
    assert visible_book is book
    device_delivery.queue_book_for_device(
        session=session,
        user_id=1,
        device_public_id=device.public_id,
        book=visible_book,
    )
    session.commit()
    current_user.filtered_book_calls.clear()

    yield app.test_client(), session, current_user, device, module
    session.close()
    engine.dispose()


def _claim(client, *, device_id="first-runtime-device", device="First reader",
           free_space=10_000, total_space=20_000):
    return client.post("/kosync/syncs/deliveries/claim", json={
        "device": device,
        "device_id": device_id,
        "free_space": free_space,
        "total_space": total_space,
    })


def _complete(client, delivery, **overrides):
    payload = {
        "device": "First reader",
        "device_id": "first-runtime-device",
        "delivery_id": delivery["id"],
        "claim_token": delivery["claim_token"],
        "lpath": delivery["filename"],
        "checksum": "0123456789abcdef0123456789abcdef",
        "size": 1234,
        "mtime": 1_777_777_777,
    }
    payload.update(overrides)
    return client.put("/kosync/syncs/deliveries/complete", json=payload)


def _download_headers(delivery):
    return {
        "X-CWNG-Device-ID": "first-runtime-device",
        "X-CWNG-Device-Name": "First reader",
        "X-CWNG-Claim-Token": delivery["claim_token"],
    }


def _prepare_delivery_file(tmp_path, monkeypatch):
    root = tmp_path / "library"
    book_dir = root / "Author" / "Queued book"
    book_dir.mkdir(parents=True)
    payload = b"phase-two-delivery-bytes"
    (book_dir / "Queued book.epub").write_bytes(payload)

    from cps import calibre_db, config
    data = SimpleNamespace(
        format="EPUB", name="Queued book", uncompressed_size=len(payload),
    )
    monkeypatch.setattr(
        calibre_db, "get_book_format",
        lambda book_id, fmt: data if book_id == 42 and fmt == "EPUB" else None,
    )
    monkeypatch.setattr(config, "get_book_path", lambda: str(root))
    return payload


def _expected_download_filter_call(current_user):
    return [{
        "book_id": 42,
        "user": current_user,
        "allow_show_archived": True,
        "allow_show_hidden": True,
    }]


def test_real_route_claim_repeat_and_complete_are_idempotent(delivery_protocol):
    client, session, _user, _device, _module = delivery_protocol

    first = _claim(client)
    second = _claim(client)

    assert first.status_code == 200
    assert second.status_code == 200
    first_delivery = first.get_json()["delivery"]
    second_delivery = second.get_json()["delivery"]
    assert first_delivery["id"] == second_delivery["id"]
    assert first_delivery["claim_token"] == second_delivery["claim_token"]
    assert first_delivery["download_path"].endswith(
        f"/{first_delivery['id']}/download"
    )

    completed = _complete(client, first_delivery)
    repeated = _complete(client, first_delivery)

    assert completed.status_code == 200
    assert repeated.status_code == 200
    assert completed.get_json() == {"completed": True, "delivery_id": first_delivery["id"]}
    row = session.query(ub.DeviceBookDelivery).one()
    assert row.state == device_delivery.COMPLETED
    assert row.installed_lpath == first_delivery["filename"]


def test_claimed_download_streams_only_to_the_owning_device(
        delivery_protocol, tmp_path, monkeypatch):
    client, _session, current_user, _device, _module = delivery_protocol
    delivery = _claim(client).get_json()["delivery"]
    payload = _prepare_delivery_file(tmp_path, monkeypatch)

    response = client.get(
        delivery["download_path"].replace("/syncs/", "/kosync/syncs/", 1),
        headers=_download_headers(delivery),
    )
    current_user.id = 2
    stolen = client.get(
        delivery["download_path"].replace("/syncs/", "/kosync/syncs/", 1),
        headers={
            "X-CWNG-Device-ID": "second-runtime-device",
            "X-CWNG-Device-Name": "Second reader",
            "X-CWNG-Claim-Token": delivery["claim_token"],
        },
    )

    assert response.status_code == 200
    assert response.data == payload
    assert "attachment" in response.headers["Content-Disposition"]
    assert current_user.filtered_book_calls == _expected_download_filter_call(
        current_user,
    )
    assert stolen.status_code == 409


def test_download_rechecks_visibility_after_claim_and_refuses_revoked_book(
        delivery_protocol, tmp_path, monkeypatch):
    client, session, current_user, _device, _module = delivery_protocol
    assert current_user.book_visible is True
    delivery = _claim(client).get_json()["delivery"]
    _prepare_delivery_file(tmp_path, monkeypatch)

    # The delivery was queued and claimed while this user could see the book.
    # A later library/restriction change must revoke the byte stream too.
    current_user.book_visible = False
    response = client.get(
        delivery["download_path"].replace("/syncs/", "/kosync/syncs/", 1),
        headers=_download_headers(delivery),
    )

    assert response.status_code == 410
    assert response.get_json() == {
        "error": "delivery_file_unavailable",
        "message": "Delivery is no longer available",
    }
    assert current_user.filtered_book_calls == _expected_download_filter_call(
        current_user,
    )
    row = session.query(ub.DeviceBookDelivery).one()
    assert row.state == device_delivery.FAILED
    assert row.failure_reason == "Delivery is no longer available"


def test_empty_queue_is_an_explicit_success(delivery_protocol):
    client, _session, _user, _device, _module = delivery_protocol
    delivery = _claim(client).get_json()["delivery"]
    assert _complete(client, delivery).status_code == 200

    response = _claim(client)

    assert response.status_code == 200
    assert response.get_json() == {"delivery": None}


def test_other_users_device_cannot_see_or_complete_the_queue(delivery_protocol):
    client, _session, current_user, _device, _module = delivery_protocol
    owned = _claim(client).get_json()["delivery"]
    current_user.id = 2

    other_claim = _claim(
        client, device_id="second-runtime-device", device="Second reader",
    )
    stolen_complete = _complete(
        client,
        owned,
        device="Second reader",
        device_id="second-runtime-device",
    )

    assert other_claim.status_code == 200
    assert other_claim.get_json() == {"delivery": None}
    assert stolen_complete.status_code == 409
    assert stolen_complete.get_json()["error"] == "invalid_delivery_claim"


@pytest.mark.parametrize("overrides", [
    {"checksum": "not-a-checksum"},
    {"lpath": "../escape.epub"},
    {"size": -1},
    {"delivery_id": "not-an-integer"},
])
def test_malformed_completion_is_rejected_without_a_500(delivery_protocol, overrides):
    client, _session, _user, _device, _module = delivery_protocol
    delivery = _claim(client).get_json()["delivery"]

    response = _complete(client, delivery, **overrides)

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_delivery"


@pytest.mark.parametrize("method,path", [
    ("post", "/kosync/syncs/deliveries/claim"),
    ("put", "/kosync/syncs/deliveries/complete"),
])
def test_oversized_delivery_payload_is_rejected_without_parsing(
        delivery_protocol, method, path):
    client, _session, _user, _device, _module = delivery_protocol

    response = getattr(client, method)(
        path,
        data=b'{"padding":"' + (b"x" * 70_000) + b'"}',
        content_type="application/json",
    )

    assert response.status_code == 413
    assert response.get_json()["error"] == "delivery_too_large"


def test_claim_rejects_unexpected_fields(delivery_protocol):
    client, _session, _user, _device, _module = delivery_protocol

    response = client.post("/kosync/syncs/deliveries/claim", json={
        "device": "First reader",
        "device_id": "first-runtime-device",
        "free_space": 10_000,
        "total_space": 20_000,
        "unexpected": "not part of the wire contract",
    })

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_delivery"


def test_claim_records_fresh_space_and_does_not_lease_an_oversized_book(
        delivery_protocol):
    client, session, _user, _device, _module = delivery_protocol

    response = _claim(client, free_space=100, total_space=20_000)

    assert response.status_code == 200
    assert response.get_json() == {
        "delivery": None,
        "refusal": {
            "reason": "insufficient_storage",
            "required_bytes": 1234,
            "available_bytes": 100,
        },
    }
    assert session.query(ub.DeviceBookDelivery).one().state == device_delivery.QUEUED
    assert session.query(ub.DeviceStorageSnapshot).order_by(
        ub.DeviceStorageSnapshot.id.desc(),
    ).first().free_bytes == 100


def test_device_side_space_refusal_releases_claim_for_a_later_retry(
        delivery_protocol):
    client, session, _user, _device, _module = delivery_protocol
    delivery = _claim(client).get_json()["delivery"]

    response = client.put("/kosync/syncs/deliveries/refuse", json={
        "device": "First reader",
        "device_id": "first-runtime-device",
        "delivery_id": delivery["id"],
        "claim_token": delivery["claim_token"],
        "reason": "insufficient_storage",
        "free_space": 50,
        "total_space": 20_000,
    })

    assert response.status_code == 200
    assert response.get_json() == {"requeued": True, "delivery_id": delivery["id"]}
    row = session.query(ub.DeviceBookDelivery).one()
    assert row.state == device_delivery.QUEUED
    assert row.claim_token == delivery["claim_token"]
