# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The progress a device pulls carries the time it was saved, in any time zone.

The ``timestamp`` column is stored as naive UTC. Read back and turned into an
epoch as if it were local time, a server running with ``TZ=America/New_York``
(the household deployment) served every position four hours in the future.
The CWNG KOReader plugin compares that time with when it captured its own
position and keeps quiet when another device looks newer, so a Kindle stopped
sending its place for four hours after the web reader or a Kobo had saved one.
Seen on a real Kindle: "newer position from another device; not sending the
queued one" for a position taken two minutes after the web reader's.
"""

import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps.progress_syncing.models import AppBase

REPO_ROOT = Path(__file__).resolve().parents[2]


def _kosync_module():
    import sys
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    return sys.modules["cps.progress_syncing.protocols.kosync"]


@pytest.fixture
def new_york():
    """Run with the household server's zone, four hours behind UTC in September."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    yield
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


@pytest.fixture
def protocol(monkeypatch, new_york):
    module = _kosync_module()
    engine = create_engine("sqlite:///:memory:")
    AppBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    monkeypatch.setattr(module, "ub", MagicMock(session=session))
    monkeypatch.setattr(module, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(module, "authenticate_user", lambda: SimpleNamespace(id=1))
    monkeypatch.setattr(module, "update_book_read_status", lambda *_args: None)
    monkeypatch.setattr(module, "push_reading_state_to_hardcover", lambda *_args: None)
    monkeypatch.setattr(module, "get_book_checksums", lambda book_id: ["digest-a"] if book_id else [])
    monkeypatch.setattr(module, "enrich_response_with_book_info",
                        lambda response, document: (response, 42, "EPUB", "Reporter fixture", "koreader"))

    app = Flask(__name__)
    app.register_blueprint(module.kosync)
    yield app.test_client()
    session.close()


@pytest.mark.unit
def test_a_pulled_position_carries_the_time_it_was_saved(protocol):
    client = protocol
    before = time.time()
    pushed = client.put("/kosync/syncs/progress", json={
        "document": "digest-a", "progress": "/body/DocFragment[6]/body/div/text().0",
        "percentage": 0.33, "device": "Web reader", "device_id": "web",
    })
    assert pushed.status_code == 200
    after = time.time()

    pulled = client.get("/kosync/syncs/progress/digest-a")
    assert pulled.status_code == 200
    served = pulled.get_json()["timestamp"]
    assert before - 1 <= served <= after + 1, (
        f"served {served}, saved between {before:.0f} and {after:.0f} "
        f"({served - after:+.0f} s): a device would take a later place of its own for an older one")
    assert served == pushed.get_json()["timestamp"], "the pull and the push name the same moment"
