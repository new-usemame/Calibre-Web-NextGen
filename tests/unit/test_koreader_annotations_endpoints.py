# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 2 — KOReader-bridge server endpoints (pull/push core).

The Flask routes (GET/PUT /kosync/syncs/annotations) reuse kosync's
authenticate_user + get_book_by_checksum and are exercised over the wire; this
file pins the testable core:

  - build_pull_payload(user_id, book_id, session) → portable dicts for the
    device, INCLUDING hidden rows (so the device can delete locally).
  - apply_push(annotations, user, book, ...) → upsert each (create/update/
    soft-delete), fan out to enabled sync targets, return a counts summary.
"""

from __future__ import annotations

import importlib
import pytest
from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from types import SimpleNamespace

from cps import ub
from cps import calibre_db
from cps.services.annotation_sync import (
    register_handler, reset_registry_for_testing, dispatch_annotation_deletes,
)
from cps.services.annotation_sync.base import AnnotationSyncTargetHandler, SyncResult
from cps.progress_syncing.protocols.koreader_annotations import (
    build_pull_payload, apply_push,
)
annotation_routes = importlib.import_module("cps.progress_syncing.protocols.koreader_annotations")
kosync_routes = importlib.import_module("cps.progress_syncing.protocols.kosync")

pytestmark = pytest.mark.unit


class StubHandler(AnnotationSyncTargetHandler):
    target_name = "stub"

    def __init__(self):
        self.pushes = []
        self.deletes = []

    def is_enabled(self, user):
        return True

    def push(self, annotation, book, user, payload=None):
        self.pushes.append(annotation.annotation_id)
        return SyncResult(status="synced", target_record_id="r1")

    def delete(self, sync_target, user):
        self.deletes.append(sync_target.target_record_id)
        return SyncResult(status="tombstone")


@pytest.fixture(autouse=True)
def _reset():
    reset_registry_for_testing()
    yield
    reset_registry_for_testing()


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()
    s.execute(text("PRAGMA foreign_keys=ON"))
    user = ub.User(name="kr", email="kr@e.com", role=0, password="x")
    s.add(user)
    s.commit()
    monkeypatch.setattr(ub, "session", s)
    monkeypatch.setattr(ub, "session_commit", lambda: s.commit())
    yield s, user


def _book():
    return SimpleNamespace(id=7, uuid="bk-7", title="Book")


def _seed(s, user, aid, book_id=7, hidden=False):
    s.add(ub.Annotation(
        user_id=user.id, annotation_id=aid, book_id=book_id, source="kobo",
        highlighted_text="t", highlight_color="yellow",
        start_container_path="span#kobo.1.1", start_offset=0,
        end_container_path="span#kobo.1.1", end_offset=4, hidden=hidden,
    ))
    s.commit()


# --- pull ------------------------------------------------------------------

def test_pull_returns_user_rows_including_hidden(env):
    s, user = env
    _seed(s, user, "a1")
    _seed(s, user, "a2", hidden=True)
    payload = build_pull_payload(user.id, 7, s)
    ids = {a["annotation_id"] for a in payload["annotations"]}
    assert ids == {"a1", "a2"}           # hidden included so device can delete
    assert payload["annotation_count"] == 2
    a2 = [a for a in payload["annotations"] if a["annotation_id"] == "a2"][0]
    assert a2["hidden"] is True


def test_pull_excludes_other_users_and_books(env):
    s, user = env
    other = ub.User(name="o", email="o@e.com", role=0, password="x")
    s.add(other); s.commit()
    _seed(s, user, "mine", book_id=7)
    _seed(s, user, "otherbook", book_id=99)
    _seed(s, other, "theirs", book_id=7)
    payload = build_pull_payload(user.id, 7, s)
    ids = {a["annotation_id"] for a in payload["annotations"]}
    assert ids == {"mine"}


# --- push ------------------------------------------------------------------

def test_push_creates_updates_deletes(env):
    s, user = env
    _seed(s, user, "existing")
    summary = apply_push([
        {"annotation_id": "new1", "color": "green", "start_kobospan": "kobo.2.1",
         "start_offset": 0, "end_kobospan": "kobo.2.1", "end_offset": 5,
         "content_id": "bk-7!!c.html", "device_origin_id": "bm-new1"},
        {"annotation_id": "existing", "color": "red"},   # update
        {"annotation_id": "new1-del", "hidden": True},    # delete (no prior row → still counts)
    ], user=user, book=_book(), session=s, commit=s.commit)
    assert summary["created"] == 1
    assert summary["updated"] == 1
    assert summary["deleted"] == 1
    # The created row is persisted with koreader source + device_origin_id.
    row = s.query(ub.Annotation).filter_by(user_id=user.id, annotation_id="new1").one()
    assert row.source == "koreader"
    assert row.device_origin_id == "bm-new1"
    # The updated row changed color.
    upd = s.query(ub.Annotation).filter_by(annotation_id="existing").one()
    assert upd.highlight_color == "red"


def test_push_fans_out_to_enabled_target(env):
    s, user = env
    handler = StubHandler()
    register_handler(handler)
    apply_push([
        {"annotation_id": "fan1", "color": "yellow", "start_kobospan": "kobo.1.1",
         "start_offset": 0, "end_kobospan": "kobo.1.1", "end_offset": 3},
    ], user=user, book=_book(), session=s, commit=s.commit)
    assert handler.pushes == ["fan1"]
    tgt = s.query(ub.AnnotationSyncTarget).one()
    assert tgt.target == "stub" and tgt.status == "synced"


def test_duplicate_retry_does_not_fan_out_again(env):
    s, user = env
    handler = StubHandler()
    register_handler(handler)
    payload = {"annotation_id": "fan-once", "highlighted_text": "same"}
    apply_push([payload], user=user, book=_book(), session=s, commit=s.commit)
    apply_push([payload], user=user, book=_book(), session=s, commit=s.commit)
    assert handler.pushes == ["fan-once"]


def test_delete_fanout_is_scoped_to_book(env):
    s, user = env
    _seed(s, user, "shared", book_id=7)
    _seed(s, user, "shared", book_id=8)
    dispatch_annotation_deletes(["shared"], user, book_id=8)
    assert s.query(ub.Annotation).filter_by(book_id=7, annotation_id="shared").one().hidden is False
    assert s.query(ub.Annotation).filter_by(book_id=8, annotation_id="shared").one().hidden is True


def test_push_skips_rows_without_id(env):
    s, user = env
    summary = apply_push([{"color": "yellow"}], user=user, book=_book(),
                         session=s, commit=s.commit)
    assert summary["skipped"] == 1


@pytest.mark.parametrize("value", [None, "", {}, "wrong"])
def test_push_rejects_non_array_annotation_collections(env, value):
    s, user = env
    summary = apply_push(value, user=user, book=_book(), session=s, commit=s.commit)
    assert summary == {"created": 0, "updated": 0, "deleted": 0, "skipped": 0}


@pytest.fixture
def wire(env, monkeypatch):
    """Real Flask routing for KOReader's auth -> push -> pull handshake."""
    session, user = env
    book = _book()
    monkeypatch.setattr(kosync_routes, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(kosync_routes, "authenticate_user", lambda: user)
    monkeypatch.setattr(annotation_routes, "_require_kosync_enabled", lambda: None)
    monkeypatch.setattr(annotation_routes, "authenticate_user", lambda: user)
    monkeypatch.setattr(
        annotation_routes, "get_book_by_checksum",
        lambda document: (book.id, "EPUB", book.title, "book.epub", "koreader")
        if document == "digest-699" else (None, None, None, None, None),
    )
    monkeypatch.setattr(calibre_db, "get_book", lambda _id: book)
    app = Flask(__name__)
    app.register_blueprint(kosync_routes.kosync)
    return app.test_client(), session


def test_exact_koreader_auth_push_pull_conflict_and_duplicate_sequence(wire):
    client, session = wire

    auth = client.get("/kosync/users/auth")
    assert auth.status_code == 200

    device_a = {"annotation_id": "device-a-1", "highlighted_text": "alpha",
                "position_type": "koreader_xpointer", "start_xpointer": "/a"}
    first = client.put("/kosync/syncs/annotations", json={
        "document": "digest-699", "annotations": [device_a],
    })
    assert first.status_code == 200
    assert first.get_json()["created"] == 1

    # Device B pulls A, then contributes a distinct highlight.  A is included
    # again exactly as the phase-1 provider retries its complete local set.
    pulled_a = client.get("/kosync/syncs/annotations/digest-699")
    assert {a["annotation_id"] for a in pulled_a.get_json()["annotations"]} == {"device-a-1"}
    device_b = {"annotation_id": "device-b-1", "highlighted_text": "beta",
                "position_type": "koreader_xpointer", "start_xpointer": "/b"}
    merged = client.put("/kosync/syncs/annotations", json={
        "document": "digest-699", "annotations": [device_a, device_b],
    })
    assert merged.status_code == 200
    assert merged.get_json()["created"] == 1
    assert merged.get_json()["skipped"] == 1

    final = client.get("/kosync/syncs/annotations/digest-699").get_json()
    assert final["annotation_count"] == 2
    assert {a["annotation_id"] for a in final["annotations"]} == {"device-a-1", "device-b-1"}
    assert session.query(ub.Annotation).count() == 2


@pytest.mark.parametrize("payload,error", [
    (None, "invalid_payload"),
    ([], "invalid_payload"),
    ({"document": "digest-699", "annotations": None}, "invalid_annotations"),
    ({"document": "digest-699", "annotations": "wrong"}, "invalid_annotations"),
])
def test_wire_push_rejects_none_empty_and_wrong_types(wire, payload, error):
    client, _session = wire
    response = client.put("/kosync/syncs/annotations", json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"] == error


def test_wire_push_accepts_lua_empty_table_as_an_empty_set(wire):
    """Lua cannot tell an empty list from an empty object, so its JSON encoder
    emits `{}` for a device with no highlights. That is a well-formed empty
    push, and since #920 an empty set asserts nothing, so it is a no-op rather
    than a 400."""
    client, _session = wire
    response = client.put("/kosync/syncs/annotations", json={
        "document": "digest-699", "annotations": {},
    })
    assert response.status_code == 200
    assert response.get_json()["created"] == 0


def test_wire_preflights_entire_batch_before_persisting(wire):
    client, session = wire
    response = client.put("/kosync/syncs/annotations", json={
        "document": "digest-699",
        "annotations": [
            {"annotation_id": "must-not-partially-commit", "highlighted_text": "valid"},
            {"annotation_id": "bad", "start_kobospan": [], "start_offset": "bad"},
        ],
    })
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_annotation"
    assert session.query(ub.Annotation).filter_by(
        annotation_id="must-not-partially-commit"
    ).count() == 0
