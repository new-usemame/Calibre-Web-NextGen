# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2765 follow-up: a KOReader removed from the account stops syncing progress.

The Remove dialog says "This device will no longer sync." The inventory route
honours that (#2768, 409 device_inactive); progress sync kept accepting and
storing the removed reader's position. Restoring the device resumes sync.
"""

from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.progress_syncing.models import AppBase, KOSyncProgress

DOCUMENT = "11111111111111111111111111111111"


@pytest.fixture
def progress_protocol(tmp_path, monkeypatch):
    import sys
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    module = sys.modules["cps.progress_syncing.protocols.kosync"]

    engine = create_engine(f"sqlite:///{tmp_path / 'progress.db'}")
    ub.Base.metadata.create_all(engine)
    AppBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(module, "ub", ub)
    monkeypatch.setattr(module, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(module, "authenticate_user", lambda: SimpleNamespace(id=1))
    monkeypatch.setattr(module, "update_book_read_status", lambda *_args, **_kw: None)
    monkeypatch.setattr(module, "push_reading_state_to_hardcover", lambda *_args, **_kw: None)
    # An unmatched document keeps the legacy shared path, which is the one
    # every stock KOReader uses.
    monkeypatch.setattr(module, "enrich_response_with_book_info",
                        lambda response, document: (response, None, None, None, None))

    app = Flask(__name__)
    app.secret_key = "removed-device-progress-secret"
    app.register_blueprint(module.kosync)
    yield app.test_client(), session
    session.close()
    engine.dispose()


def _push(client, percentage, *, device_id="reader-1"):
    return client.put("/kosync/syncs/progress", json={
        "document": DOCUMENT,
        "progress": f"/body/DocFragment[{int(percentage * 100)}]",
        "percentage": percentage,
        "device": "KOReader",
        "device_id": device_id,
    })


def _stored_percentages(session):
    session.expire_all()
    return sorted(row.percentage for row in session.query(KOSyncProgress).all())


@pytest.mark.unit
def test_progress_from_a_removed_device_is_refused_and_restore_resumes_it(progress_protocol):
    client, session = progress_protocol
    assert _push(client, 0.25).status_code == 200
    device = session.query(ub.Device).filter_by(kind="koreader").one()

    device.active = False
    session.commit()
    refused = _push(client, 0.60)

    assert refused.status_code == 409
    body = refused.get_json()
    assert body["error"] == "device_inactive"
    assert body["message"]
    # Nothing from the removed reader was stored, and it was not silently
    # brought back by checking in.
    assert _stored_percentages(session) == [pytest.approx(25.0)]
    session.expire_all()
    assert session.get(ub.Device, device.id).active is False

    session.get(ub.Device, device.id).active = True
    session.commit()
    assert _push(client, 0.60).status_code == 200
    assert _stored_percentages(session) == [pytest.approx(60.0)]


@pytest.mark.unit
def test_a_different_active_reader_still_syncs_after_another_is_removed(progress_protocol):
    client, session = progress_protocol
    assert _push(client, 0.25, device_id="reader-1").status_code == 200
    session.query(ub.Device).filter_by(kind="koreader").one().active = False
    session.commit()

    assert _push(client, 0.40, device_id="reader-2").status_code == 200
    assert _stored_percentages(session) == [pytest.approx(40.0)]
