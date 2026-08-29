# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Protocol regressions for #633: a later, shorter push must not win."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import helper
from cps.progress_syncing.models import AppBase, KOSyncProgress

REPO_ROOT = Path(__file__).resolve().parents[2]


def _kosync_module():
    import sys
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    return sys.modules["cps.progress_syncing.protocols.kosync"]


@pytest.fixture
def protocol(monkeypatch):
    """A real Flask PUT/GET path backed by a real in-memory progress table."""
    module = _kosync_module()
    engine = create_engine("sqlite:///:memory:")
    AppBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    monkeypatch.setattr(module, "ub", MagicMock(session=session))
    monkeypatch.setattr(module, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(module, "authenticate_user", lambda: SimpleNamespace(id=1))
    monkeypatch.setattr(module, "update_book_read_status", lambda *_args: None)
    monkeypatch.setattr(module, "push_reading_state_to_hardcover", lambda *_args: None)
    monkeypatch.setattr(module, "get_book_checksums", lambda book_id: ["digest-a", "digest-b"] if book_id else [])

    def enrich(response, document):
        # Both device-specific digests identify the same Calibre book.
        return response, 42, "EPUB", "Reporter fixture", "koreader"

    monkeypatch.setattr(module, "enrich_response_with_book_info", enrich)

    app = Flask(__name__)
    app.register_blueprint(module.kosync)
    yield app.test_client(), session
    session.close()


def _push(client, document, percentage, device):
    return client.put("/kosync/syncs/progress", json={
        "document": document,
        "progress": f"cre://position/{percentage}",
        "percentage": percentage,
        "device": device,
        "device_id": device,
    })


@pytest.mark.unit
def test_two_devices_lower_late_push_does_not_replace_furthest(protocol):
    """A=80%, then B=67%, then B pulls: the server must return A's 80%."""
    client, session = protocol

    assert _push(client, "digest-a", 0.80, "device-a").status_code == 200
    assert _push(client, "digest-b", 0.67, "device-b").status_code == 200

    response = client.get("/kosync/syncs/progress/digest-b")
    assert response.status_code == 200
    assert response.get_json()["percentage"] == pytest.approx(0.80)

    stored = session.query(KOSyncProgress).one()
    assert stored.percentage == pytest.approx(80.0)
    assert stored.device == "device-a"


@pytest.mark.unit
def test_same_device_backward_navigation_replaces_server_record(protocol):
    """A device is authoritative for its own deliberate rewind."""
    client, session = protocol

    assert _push(client, "digest-a", 0.80, "device-a").status_code == 200
    assert _push(client, "digest-a", 0.67, "device-a").status_code == 200

    response = client.get("/kosync/syncs/progress/digest-a")
    assert response.status_code == 200
    assert response.get_json()["percentage"] == pytest.approx(0.67)
    assert session.query(KOSyncProgress).one().percentage == pytest.approx(67.0)


@pytest.mark.unit
def test_missing_device_id_does_not_bypass_cross_device_guard(protocol):
    """An absent wire identifier cannot prove that the pushing device owns the row."""
    client, session = protocol

    assert _push(client, "digest-a", 0.80, "device-a").status_code == 200
    response = client.put("/kosync/syncs/progress", json={
        "document": "digest-b",
        "progress": "cre://position/67",
        "percentage": 0.67,
        "device": "unnamed",
        "device_id": "",
    })

    assert response.status_code == 200
    pulled = client.get("/kosync/syncs/progress/digest-b")
    assert pulled.status_code == 200
    assert pulled.get_json()["percentage"] == pytest.approx(0.80)
    assert session.query(KOSyncProgress).one().percentage == pytest.approx(80.0)


@pytest.mark.unit
def test_equal_percentage_refreshes_exact_location(protocol):
    """Equal percentage may carry a more precise locator and remains writable."""
    client, session = protocol

    assert _push(client, "digest-a", 0.80, "device-a").status_code == 200
    assert _push(client, "digest-b", 0.80, "device-b").status_code == 200

    stored = session.query(KOSyncProgress).one()
    assert stored.percentage == pytest.approx(80.0)
    assert stored.device == "device-b"


@pytest.mark.unit
def test_pull_chooses_furthest_across_legacy_digest_rows(protocol):
    """A newer lower orphan row must not beat an older further row on GET."""
    client, session = protocol
    now = datetime.now(timezone.utc)
    session.add_all([
        KOSyncProgress(
            user_id=1, document="digest-a", progress="cre://position/80",
            percentage=80.0, device="device-a", device_id="device-a",
            timestamp=now - timedelta(minutes=5),
        ),
        KOSyncProgress(
            user_id=1, document="digest-b", progress="cre://position/67",
            percentage=67.0, device="device-b", device_id="device-b",
            timestamp=now,
        ),
    ])
    session.commit()

    response = client.get("/kosync/syncs/progress/digest-b")
    assert response.status_code == 200
    assert response.get_json()["percentage"] == pytest.approx(0.80)


@pytest.mark.unit
def test_mark_unread_removes_all_kosync_keys_and_get_has_no_stale_progress(
        protocol, monkeypatch):
    """Restarting a finished book clears book-id and legacy digest progress."""
    client, session = protocol
    now = datetime.now(timezone.utc)
    session.add_all([
        KOSyncProgress(
            user_id=1, document="42", progress="cre://position/100",
            percentage=100.0, device="device-a", device_id="device-a",
            timestamp=now,
        ),
        KOSyncProgress(
            user_id=1, document="digest-a", progress="cre://position/100",
            percentage=100.0, device="device-a", device_id="device-a",
            timestamp=now,
        ),
    ])
    session.commit()
    monkeypatch.setattr(
        helper, "_get_kosync_checksums_for_book",
        lambda book_id: ["digest-a", "digest-b"] if book_id == 42 else [],
        raising=False,
    )

    cleared = helper.reset_reading_position(session, 1, 42)
    session.commit()

    assert cleared == 2
    assert session.query(KOSyncProgress).count() == 0
    response = client.get("/kosync/syncs/progress/digest-a")
    assert response.status_code == 200
    assert response.get_json() == {}


@pytest.mark.unit
def test_plugin_classifies_higher_remote_percentage_as_forward_progress():
    """A device clock must not turn a higher remote percentage into 'backward'."""
    source = (REPO_ROOT / "koreader" / "plugins" / "cwngsync.koplugin"
              / "main.lua").read_text(encoding="utf-8")
    expected = """if body.percentage > percentage then
                self_older = true
            elseif body.percentage < percentage then
                self_older = false
            elseif body.timestamp ~= nil then
                self_older = (body.timestamp > self.last_page_turn_timestamp)"""
    assert expected in source
