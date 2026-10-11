# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for the per-device "Share my place" setting (PR 1).

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 3 criteria
1, 6 and 7, section 8 PR order item 1. Every device starts with the box off;
the owner turns it on or off through the devices API; turning it on records
when, so later PRs can serve the shared place on the device's next sync.

Routes are resolved through the blueprint's URL map (path + method), not by
Python function name, and results are re-read through a second session on the
same database file so an uncommitted change does not count.
"""

import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit

OWNER = 7
STRANGER = 8


@pytest.fixture
def engine(tmp_path):
    from cps import ub
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    ub.Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    db = sessionmaker(bind=engine)()
    yield db
    db.close()


@pytest.fixture
def reread(engine):
    """Read committed state through an independent session."""
    sessions = []

    def read_device(public_id):
        from cps import ub
        db = sessionmaker(bind=engine)()
        sessions.append(db)
        return db.query(ub.Device).filter_by(public_id=public_id).one()

    yield read_device
    for db in sessions:
        db.close()


@pytest.fixture(autouse=True)
def filtered_library_stub(monkeypatch):
    """Keep app.db-only fixtures explicit without weakening production authz."""
    from cps import annotations

    def scopes(owners, _session, candidates_by_owner):
        return {
            int(owner.id): frozenset(candidates_by_owner.get(int(owner.id), ()))
            for owner in owners if owner is not None
        }

    monkeypatch.setattr(annotations, "_visible_book_scopes_for_owners", scopes)
    monkeypatch.setattr(
        annotations.calibre_db,
        "get_filtered_book",
        lambda book_id, user=None, allow_show_global=False: SimpleNamespace(
            id=book_id, title=f"Book {book_id}",
        ),
    )


@pytest.fixture
def api(session, monkeypatch):
    """Call a devices API route as user OWNER, by path and method."""
    from cps import annotations, ub

    app = Flask(__name__)
    app.register_blueprint(annotations.annotations_bp)
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=OWNER))
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", session.commit)

    def call(method, path, json=None):
        from werkzeug.exceptions import HTTPException
        with app.test_request_context(path, method=method, json=json):
            endpoint, args = app.url_map.bind("localhost").match(path, method=method)
            view = inspect.unwrap(app.view_functions[endpoint])
            try:
                result = view(**args)
            except HTTPException as error:
                return error.code, None
        if isinstance(result, tuple):
            response, status = result[0], result[1]
        else:
            response, status = result, result.status_code
        return status, response.get_json()

    return call


def _user(session, user_id):
    from cps import ub
    if session.get(ub.User, user_id) is None:
        session.add(ub.User(id=user_id, name=f"User {user_id}",
                            email=f"user-{user_id}@example.invalid", role=0))
        session.flush()


def _device(session, *, user_id=OWNER, kind="kobo", label="Reader"):
    from cps import ub
    _user(session, user_id)
    row = ub.Device(user_id=user_id, kind=kind, display_name=label, active=True,
                    created_by="auto")
    session.add(row)
    session.commit()
    return row


def _utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _patch(api, public_id, body):
    return api("PATCH", f"/api/annotations/devices/{public_id}", json=body)


# --- Criterion 1: everything starts off -------------------------------------

@pytest.mark.parametrize("kind", ["kobo", "koreader", "webreader"])
def test_a_newly_registered_device_starts_with_share_my_place_off(session, reread, kind):
    device = _device(session, kind=kind)
    assert reread(device.public_id).share_place is False


def test_the_accounts_web_reader_entry_starts_off(session, reread):
    from cps.services.device_registry import upsert_webreader_device
    _user(session, OWNER)
    device = upsert_webreader_device(session, user_id=OWNER)
    session.commit()
    assert reread(device.public_id).share_place is False


def test_device_list_reports_share_my_place_for_each_device(session, api):
    from cps.services.device_registry import upsert_webreader_device
    kobo = _device(session, kind="kobo", label="Kobo")
    _user(session, OWNER)
    browser = upsert_webreader_device(session, user_id=OWNER)
    session.commit()

    status, body = api("GET", "/api/annotations/devices")
    assert status == 200
    rows = {row["public_id"]: row for row in body["devices"]}
    assert rows[kobo.public_id]["share_place"] is False
    assert rows[browser.public_id]["share_place"] is False

    assert _patch(api, kobo.public_id, {"share_place": True})[0] == 200
    status, body = api("GET", "/api/annotations/devices")
    rows = {row["public_id"]: row for row in body["devices"]}
    assert rows[kobo.public_id]["share_place"] is True
    assert rows[browser.public_id]["share_place"] is False


# --- Turning it on and off ---------------------------------------------------

def test_owner_turns_share_my_place_on_without_a_label(session, api, reread):
    device = _device(session, label="Kobo Clara")
    before = datetime.now(timezone.utc) - timedelta(seconds=1)

    status, body = _patch(api, device.public_id, {"share_place": True})
    after = datetime.now(timezone.utc) + timedelta(seconds=1)

    assert status == 200
    assert body["share_place"] is True
    assert body["public_id"] == device.public_id
    stored = reread(device.public_id)
    assert stored.share_place is True
    assert stored.display_name == "Kobo Clara"
    assert before <= _utc(stored.share_place_enabled_at) <= after


def test_owner_turns_share_my_place_off_and_the_enable_time_is_kept(session, api, reread):
    device = _device(session)
    assert _patch(api, device.public_id, {"share_place": True})[0] == 200
    enabled_at = reread(device.public_id).share_place_enabled_at
    assert enabled_at is not None

    status, body = _patch(api, device.public_id, {"share_place": False})

    assert status == 200
    assert body["share_place"] is False
    stored = reread(device.public_id)
    assert stored.share_place is False
    assert _utc(stored.share_place_enabled_at) == _utc(enabled_at)


def test_turning_on_again_while_on_keeps_the_original_enable_time(session, api, reread):
    from cps import ub
    device = _device(session)
    long_ago = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = session.query(ub.Device).filter_by(public_id=device.public_id).one()
    row.share_place = True
    row.share_place_enabled_at = long_ago
    session.commit()

    status, body = _patch(api, device.public_id, {"share_place": True})

    assert status == 200
    assert body["share_place"] is True
    assert _utc(reread(device.public_id).share_place_enabled_at) == long_ago


def test_turning_on_after_off_records_a_new_enable_time(session, api, reread):
    from cps import ub
    device = _device(session)
    long_ago = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = session.query(ub.Device).filter_by(public_id=device.public_id).one()
    row.share_place = False
    row.share_place_enabled_at = long_ago
    session.commit()
    before = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert _patch(api, device.public_id, {"share_place": True})[0] == 200

    stored = reread(device.public_id)
    assert stored.share_place is True
    assert _utc(stored.share_place_enabled_at) >= before


def test_label_and_share_my_place_change_together(session, api, reread):
    device = _device(session, label="Old name")

    status, body = _patch(api, device.public_id, {"label": "Bedside Kobo", "share_place": True})

    assert status == 200
    assert body["label"] == "Bedside Kobo"
    assert body["share_place"] is True
    stored = reread(device.public_id)
    assert stored.display_name == "Bedside Kobo"
    assert stored.share_place is True


@pytest.mark.parametrize("initial", [False, True])
def test_a_rename_alone_leaves_share_my_place_unchanged(session, api, reread, initial):
    from cps import ub
    device = _device(session, label="Old name")
    row = session.query(ub.Device).filter_by(public_id=device.public_id).one()
    row.share_place = initial
    session.commit()

    status, body = _patch(api, device.public_id, {"label": "New name"})

    assert status == 200
    assert body["share_place"] is initial
    stored = reread(device.public_id)
    assert stored.display_name == "New name"
    assert stored.share_place is initial


@pytest.mark.parametrize("bad_value", ["yes", "true", 1, 0, None, [], {}])
def test_a_non_boolean_share_my_place_is_rejected_and_changes_nothing(
        session, api, reread, bad_value):
    device = _device(session, label="Old name")

    status, body = _patch(api, device.public_id, {"label": "New name", "share_place": bad_value})

    assert status == 400
    assert body["error"] == "invalid_share_place"
    stored = reread(device.public_id)
    assert stored.share_place is False
    assert stored.share_place_enabled_at is None
    assert stored.display_name == "Old name"


def test_another_users_device_is_not_found_and_stays_unchanged(session, api, reread):
    _device(session, user_id=OWNER)
    theirs = _device(session, user_id=STRANGER, label="Their Kobo")

    status, _ = _patch(api, theirs.public_id, {"share_place": True})

    assert status == 404
    stored = reread(theirs.public_id)
    assert stored.share_place is False
    assert stored.share_place_enabled_at is None


# --- Per-device read status storage -----------------------------------------

def test_a_device_reading_position_carries_its_own_read_status(session, engine):
    from cps import ub
    device = _device(session)
    stamped = datetime(2026, 10, 11, 9, 30, tzinfo=timezone.utc)
    session.add_all([
        ub.DeviceReadingPosition(device_id=device.id, book_id=1),
        ub.DeviceReadingPosition(device_id=device.id, book_id=2,
                                 reading_status=ub.ReadBook.STATUS_FINISHED,
                                 status_modified_at=stamped),
        ub.DeviceReadingPosition(device_id=device.id, book_id=3,
                                 reading_status=ub.ReadBook.STATUS_IN_PROGRESS,
                                 status_modified_at=stamped),
        ub.DeviceReadingPosition(device_id=device.id, book_id=4,
                                 reading_status=ub.ReadBook.STATUS_UNREAD,
                                 status_modified_at=stamped),
    ])
    session.commit()

    other = sessionmaker(bind=engine)()
    try:
        rows = {row.book_id: row for row in other.query(ub.DeviceReadingPosition).all()}
    finally:
        other.close()
    assert rows[1].reading_status is None
    assert rows[1].status_modified_at is None
    assert rows[2].reading_status == ub.ReadBook.STATUS_FINISHED
    assert rows[3].reading_status == ub.ReadBook.STATUS_IN_PROGRESS
    assert rows[4].reading_status == ub.ReadBook.STATUS_UNREAD
    assert _utc(rows[2].status_modified_at) == stamped
