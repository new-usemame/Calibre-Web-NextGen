# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""One physical reader, several accounts on one server.

A household reader moves between accounts: the KOReader plugin sets the old
account's books aside and pairs with the next. Each account must get its own
device row for that hardware, and going back must find the original row.
"""

import hashlib
import hmac
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services import device_delivery, device_registry


pytestmark = pytest.mark.unit

SECRET = "per-account-identity-secret"
KINDLE_ID = "shared-household-kindle"
ACCOUNT_A = 1
ACCOUNT_B = 2


@pytest.fixture
def server(tmp_path, monkeypatch):
    import sys
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    module = sys.modules["cps.progress_syncing.protocols.kosync"]

    engine = create_engine(f"sqlite:///{tmp_path / 'per-account.db'}")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    current = SimpleNamespace(id=ACCOUNT_A, role_download=lambda: True)

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(module, "ub", ub)
    monkeypatch.setattr(module, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(module, "authenticate_user", lambda: current)

    app = Flask(__name__)
    app.secret_key = SECRET
    app.register_blueprint(module.kosync)
    yield SimpleNamespace(
        app=app, client=app.test_client(), session=session, current=current,
    )
    session.close()
    engine.dispose()


def _register(server, user_id, device_id=KINDLE_ID, name="Kindle"):
    with server.app.app_context():
        return device_registry.register_koreader_device_best_effort(
            user_id=user_id, device_id=device_id, device_name=name,
        )


def _identity():
    return {"device": "Kindle", "device_id": KINDLE_ID}


def _inventory(server):
    return server.client.put("/kosync/syncs/inventory", json={
        **_identity(), "inventory": [],
    })


def _claim_delivery(server):
    return server.client.post("/kosync/syncs/deliveries/claim", json={
        **_identity(), "free_space": 10_000, "total_space": 20_000,
    })


def _row(server, device_id):
    row = server.session.get(ub.Device, device_id)
    server.session.refresh(row)
    return (row.user_id, row.public_id, row.display_name, row.active)


def test_reader_paired_to_a_second_account_works_there_and_leaves_the_first_alone(server):
    a_device = _register(server, ACCOUNT_A)
    assert a_device is not None
    a_before = _row(server, a_device)

    # The reader now signs in as account B: the plugin's first calls after
    # pairing are the inventory report and the deletion claim.
    server.current.id = ACCOUNT_B
    inventory = _inventory(server)
    deletion = server.client.post("/kosync/syncs/deletions/claim", json=_identity())
    empty_claim = _claim_delivery(server)

    assert inventory.status_code == 200, inventory.get_json()
    assert deletion.status_code == 200, deletion.get_json()
    assert deletion.get_json() == {"deletion": None}
    assert empty_claim.status_code == 200, empty_claim.get_json()

    b_devices = server.session.query(ub.Device).filter_by(
        user_id=ACCOUNT_B, kind="koreader").all()
    assert len(b_devices) == 1
    b_device = b_devices[0]
    assert b_device.id != a_device
    assert _row(server, a_device) == a_before

    # Send to device for account B reaches this reader.
    book = SimpleNamespace(id=42, title="Queued", path="Author/Queued", data=[
        SimpleNamespace(format="EPUB", name="Queued", uncompressed_size=1234),
    ])
    device_delivery.queue_book_for_device(
        session=server.session, user_id=ACCOUNT_B,
        device_public_id=b_device.public_id, book=book,
    )
    server.session.commit()
    claimed = _claim_delivery(server)
    assert claimed.status_code == 200, claimed.get_json()
    assert claimed.get_json()["delivery"]["book_id"] == 42

    # Back on account A: the same row, public id and name as before.
    assert _register(server, ACCOUNT_A) == a_device
    assert _row(server, a_device) == a_before
    # And B keeps resolving to its own row, not A's.
    assert _register(server, ACCOUNT_B) == b_device.id


def test_pre_upgrade_identity_keeps_its_owner_and_does_not_block_another_account(server):
    # A row stored before per-account identities: one server-wide fingerprint.
    legacy = ub.Device(user_id=ACCOUNT_A, kind="koreader", display_name="Kindle",
                       platform="koreader", active=True, created_by="auto")
    server.session.add(legacy)
    server.session.add(ub.DeviceIdentity(
        device=legacy, scheme=device_registry.KOREADER_SCHEME, key_version=1,
        fingerprint=hmac.new(
            SECRET.encode(), b"cwng-device:koreader:v1\0" + KINDLE_ID.encode(),
            hashlib.sha256,
        ).hexdigest(),
    ))
    server.session.commit()
    legacy_id, legacy_public_id = legacy.id, legacy.public_id

    assert _register(server, ACCOUNT_A) == legacy_id
    assert _register(server, ACCOUNT_A) == legacy_id
    b_device = _register(server, ACCOUNT_B)

    assert b_device is not None and b_device != legacy_id
    assert server.session.query(ub.Device).filter_by(
        user_id=ACCOUNT_A, kind="koreader").count() == 1
    assert server.session.get(ub.Device, legacy_id).public_id == legacy_public_id
    # The device cap counts devices, not their identity rows.
    with server.app.app_context():
        assert device_registry._koreader_identity_count(
            server.session, ub, user_id=ACCOUNT_A) == 1


def test_deletion_claim_reports_an_unregistrable_device_as_a_conflict(server, monkeypatch):
    monkeypatch.setattr(device_registry, "MAX_KOREADER_DEVICES_PER_USER", 1)
    assert _register(server, ACCOUNT_A, device_id="first-reader") is not None

    response = server.client.post("/kosync/syncs/deletions/claim", json=_identity())

    # Same client contract as the delivery claim for the same condition.
    assert response.status_code == 409
    assert response.get_json()["error"] == "device_identity_unavailable"


def test_first_registration_race_resolves_the_winning_row(server, monkeypatch):
    # A freshly paired reader fires its inventory, deletion and delivery
    # requests together. The request that loses the insert race must answer
    # with the row the winner created, not fail its own call.
    real_lookup = device_registry._account_identity
    winner = []

    def lookup_before_the_winner_commits(*args, **kwargs):
        monkeypatch.setattr(device_registry, "_account_identity", real_lookup)
        winner.append(_register(server, ACCOUNT_B))
        return None, False

    monkeypatch.setattr(
        device_registry, "_account_identity", lookup_before_the_winner_commits)
    loser = _register(server, ACCOUNT_B)

    assert winner[0] is not None
    assert loser == winner[0]
    assert server.session.query(ub.Device).filter_by(user_id=ACCOUNT_B).count() == 1


def test_kobo_signed_in_to_a_second_account_gets_its_own_row():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    raw = "E" * 64
    headers = {"x-kobo-deviceid": raw, "x-kobo-devicemodel": "Kobo Clara"}
    # Stored before per-account identities, exactly as that code derived it.
    a_device = ub.Device(user_id=ACCOUNT_A, kind="kobo", display_name="Kobo Clara",
                         platform="nickel", active=True, created_by="auto")
    session.add(a_device)
    session.add(ub.DeviceIdentity(
        device=a_device, scheme=device_registry.SCHEME, key_version=1,
        fingerprint=hmac.new(
            SECRET.encode(), b"cwng-device:kobo:v1\0" + raw.lower().encode(),
            hashlib.sha256,
        ).hexdigest(),
    ))
    session.commit()

    b_device = device_registry.upsert_kobo_device(
        session, user_id=ACCOUNT_B, headers=headers, secret_key=SECRET)
    session.commit()
    again_a = device_registry.upsert_kobo_device(
        session, user_id=ACCOUNT_A, headers=headers, secret_key=SECRET)
    session.commit()
    again_b = device_registry.upsert_kobo_device(
        session, user_id=ACCOUNT_B, headers=headers, secret_key=SECRET)

    assert b_device is not None
    assert b_device.user_id == ACCOUNT_B and b_device.id != a_device.id
    assert again_a.id == a_device.id and again_a.user_id == ACCOUNT_A
    assert again_b.id == b_device.id
    assert session.query(ub.Device).filter_by(kind="kobo").count() == 2
    session.close()
    engine.dispose()
