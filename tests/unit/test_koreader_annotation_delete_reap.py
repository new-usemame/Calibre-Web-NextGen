# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""KOReader device-side deletes — reconciliation of a complete push (#905).

KOReader keeps no tombstone when a highlight is deleted: the entry simply
disappears from ``ui.annotation.annotations``. The plugin pushes its complete
local set (``push_all_local = true``), so a device-side delete reaches the
server as an *omission*, and an upsert-only push cannot tell "deleted" apart
from "not mentioned in this push" — the row survived forever (the bug @iroQuai
reported on #699 after v4.1.13).

The fix: a push may declare itself COMPLETE for a source. When it does, the
server reconciles — any live row for that (user, book, source) whose
annotation_id is absent from the pushed set is soft-deleted.

These tests pin the reconciliation contract:
  - a complete push that omits a row tombstones it (the reported bug);
  - a push WITHOUT the completeness marker reaps nothing (a partial/retried
    push must never destroy data);
  - a complete koreader push never reaps kobo/webreader rows (source scoping);
  - reaping soft-deletes (hidden=True) rather than hard-deleting, so pull still
    hands the device a tombstone;
  - the delete fan-out fires for reaped rows;
  - an already-hidden row is not re-reaped (no duplicate fan-out).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from types import SimpleNamespace

from cps import ub
from cps.services.annotation_sync import (
    register_handler, reset_registry_for_testing,
)
from cps.services.annotation_sync.base import AnnotationSyncTargetHandler, SyncResult
from cps.progress_syncing.protocols.koreader_annotations import (
    build_pull_payload, apply_push,
)

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


def _seed(s, user, aid, *, book_id=7, source="koreader", hidden=False):
    s.add(ub.Annotation(
        user_id=user.id, annotation_id=aid, book_id=book_id, source=source,
        highlighted_text="t", highlight_color="yellow",
        start_container_path="span#kobo.1.1", start_offset=0,
        end_container_path="span#kobo.1.1", end_offset=4, hidden=hidden,
    ))
    s.commit()


def _live_ids(s, user, book_id=7):
    rows = s.query(ub.Annotation).filter(
        ub.Annotation.user_id == user.id,
        ub.Annotation.book_id == book_id,
    ).all()
    return {r.annotation_id for r in rows if not r.hidden}


# --- the reported bug ------------------------------------------------------

def test_complete_push_omitting_a_row_tombstones_it(env):
    """The #905 repro: highlight synced, deleted on device, synced again."""
    s, user = env
    _seed(s, user, "kept")
    _seed(s, user, "deleted-on-device")

    # The device now only knows about "kept" — the other was deleted there.
    summary = apply_push(
        [{"annotation_id": "kept", "color": "yellow"}],
        user=user, book=_book(), session=s, commit=s.commit,
        complete_for_source="koreader",
    )

    assert _live_ids(s, user) == {"kept"}
    assert summary["deleted"] == 1


def test_reaped_row_is_soft_deleted_not_destroyed(env):
    """Pull must still hand the device a tombstone, so the row has to survive."""
    s, user = env
    _seed(s, user, "gone")

    apply_push([], user=user, book=_book(), session=s, commit=s.commit,
               complete_for_source="koreader")

    row = s.query(ub.Annotation).filter_by(annotation_id="gone").one()
    assert row.hidden is True
    payload = build_pull_payload(user.id, 7, s)
    tomb = [a for a in payload["annotations"] if a["annotation_id"] == "gone"]
    assert tomb and tomb[0]["hidden"] is True


# --- the guardrails --------------------------------------------------------

def test_push_without_completeness_marker_reaps_nothing(env):
    """A partial or retried push must never destroy un-mentioned rows."""
    s, user = env
    _seed(s, user, "untouched")

    apply_push([{"annotation_id": "other", "color": "red"}],
               user=user, book=_book(), session=s, commit=s.commit)

    assert "untouched" in _live_ids(s, user)


def test_complete_koreader_push_never_reaps_other_sources(env):
    """A KOReader sync must not delete Kobo-native or web-reader highlights."""
    s, user = env
    _seed(s, user, "kobo-row", source="kobo")
    _seed(s, user, "web-row", source="webreader")
    _seed(s, user, "koreader-row", source="koreader")

    apply_push([], user=user, book=_book(), session=s, commit=s.commit,
               complete_for_source="koreader")

    assert _live_ids(s, user) == {"kobo-row", "web-row"}


def test_complete_push_does_not_reap_other_books_or_users(env):
    s, user = env
    other = ub.User(name="o", email="o@e.com", role=0, password="x")
    s.add(other); s.commit()
    _seed(s, user, "other-book", book_id=99)
    _seed(s, other, "other-user", book_id=7)

    apply_push([], user=user, book=_book(), session=s, commit=s.commit,
               complete_for_source="koreader")

    assert _live_ids(s, user, book_id=99) == {"other-book"}
    assert _live_ids(s, other) == {"other-user"}


def test_already_hidden_row_is_not_reaped_again(env):
    """No duplicate fan-out for a row that is already tombstoned."""
    s, user = env
    register_handler(StubHandler())
    _seed(s, user, "already-gone", hidden=True)

    summary = apply_push([], user=user, book=_book(), session=s, commit=s.commit,
                         complete_for_source="koreader")

    assert summary["deleted"] == 0


def test_reaped_row_dispatches_delete_fanout(env):
    """A reaped row must tell downstream targets (Hardcover) to tombstone too."""
    s, user = env
    handler = StubHandler()
    register_handler(handler)
    _seed(s, user, "fanout-me")
    row = s.query(ub.Annotation).filter_by(annotation_id="fanout-me").one()
    s.add(ub.AnnotationSyncTarget(
        annotation_id=row.id,  # FK to annotation.id, not the business key
        target="stub", target_record_id="r1", status="synced",
    ))
    s.commit()

    apply_push([], user=user, book=_book(), session=s, commit=s.commit,
               complete_for_source="koreader")

    assert handler.deletes == ["r1"]


# --- over the wire ---------------------------------------------------------

@pytest.fixture
def wire(env, monkeypatch):
    """Real Flask routing, so the reap is exercised through the actual PUT the
    plugin makes (auth + blueprint + JSON shape), not just the callable."""
    import importlib
    from flask import Flask
    from cps import calibre_db

    session, user = env
    book = _book()
    annotation_routes = importlib.import_module(
        "cps.progress_syncing.protocols.koreader_annotations")
    kosync_routes = importlib.import_module(
        "cps.progress_syncing.protocols.kosync")
    monkeypatch.setattr(kosync_routes, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(annotation_routes, "_require_kosync_enabled", lambda: None)
    monkeypatch.setattr(annotation_routes, "authenticate_user", lambda: user)
    monkeypatch.setattr(
        annotation_routes, "get_book_by_checksum",
        lambda document: (book.id, "EPUB", book.title, "book.epub", "koreader")
        if document == "digest-905" else (None, None, None, None, None),
    )
    monkeypatch.setattr(calibre_db, "get_book", lambda _id: book)
    app = Flask(__name__)
    app.register_blueprint(kosync_routes.kosync)
    return app.test_client(), session, user


def test_wire_reporter_sequence_highlight_then_delete_then_resync(wire):
    """@iroQuai's repro on #905, over the real PUT/GET the plugin makes."""
    client, session, user = wire

    # 1. Highlight on the device, sync -> it shows up in CWNG.
    first = client.put("/kosync/syncs/annotations", json={
        "document": "digest-905",
        "complete": True,
        "complete_source": "koreader",
        "annotations": [
            {"annotation_id": "kr-1", "highlighted_text": "one", "source": "koreader"},
            {"annotation_id": "kr-2", "highlighted_text": "two", "source": "koreader"},
        ],
    })
    assert first.status_code == 200
    assert first.get_json()["created"] == 2
    assert _live_ids(session, user) == {"kr-1", "kr-2"}

    # 2. Delete kr-2 in KOReader, sync again. The device simply stops sending
    #    it — there is no tombstone to send.
    second = client.put("/kosync/syncs/annotations", json={
        "document": "digest-905",
        "complete": True,
        "complete_source": "koreader",
        "annotations": [
            {"annotation_id": "kr-1", "highlighted_text": "one", "source": "koreader"},
        ],
    })
    assert second.status_code == 200
    body = second.get_json()
    assert body["deleted"] == 1
    assert body["reconciled"] is True

    # 3. CWNG no longer lists it; the device still gets a tombstone on pull.
    assert _live_ids(session, user) == {"kr-1"}
    pull = client.get("/kosync/syncs/annotations/digest-905")
    hidden = {a["annotation_id"]: a["hidden"] for a in pull.get_json()["annotations"]}
    assert hidden == {"kr-1": False, "kr-2": True}


def test_wire_legacy_plugin_push_without_marker_reaps_nothing(wire):
    """An older plugin build must never lose the user's highlights."""
    client, session, user = wire
    _seed(session, user, "pre-existing")

    resp = client.put("/kosync/syncs/annotations", json={
        "document": "digest-905",
        "annotations": [{"annotation_id": "kr-new", "highlighted_text": "n"}],
    })
    assert resp.status_code == 200
    assert resp.get_json()["reconciled"] is False
    assert "pre-existing" in _live_ids(session, user)


def test_wire_deleting_the_last_highlight_syncs(wire):
    """Lua encodes an empty table as {}, and this is the one push where the
    payload is legitimately empty — it must reap, not 400."""
    client, session, user = wire
    _seed(session, user, "the-only-one")

    for empty in ({}, []):
        resp = client.put("/kosync/syncs/annotations", json={
            "document": "digest-905",
            "complete": True,
            "complete_source": "koreader",
            "annotations": empty,
        })
        assert resp.status_code == 200, resp.get_json()
    assert _live_ids(session, user) == set()


def test_wire_unknown_complete_source_cannot_reap(wire):
    """A push may only reconcile a source it owns."""
    client, session, user = wire
    _seed(session, user, "kobo-row", source="kobo")

    resp = client.put("/kosync/syncs/annotations", json={
        "document": "digest-905",
        "complete": True,
        "complete_source": "kobo",
        "annotations": [],
    })
    assert resp.status_code == 200
    assert resp.get_json()["reconciled"] is False
    assert "kobo-row" in _live_ids(session, user)


@pytest.mark.parametrize("annotations", [None, "missing"])
def test_wire_complete_push_with_malformed_annotations_cannot_reap(wire, annotations):
    """A null/absent `annotations` is a malformed request, NOT an assertion that
    the device has none. Reading it as an empty authoritative set would reap the
    whole book, and a reap is unrecoverable — apply_portable preserves
    tombstones, so the rows would never come back even though the device still
    has them. Reject, don't reap.
    """
    client, session, user = wire
    _seed(session, user, "must-survive")

    body = {"document": "digest-905", "complete": True, "complete_source": "koreader"}
    if annotations is None:
        body["annotations"] = None       # explicit null
    # "missing" -> omit the key entirely

    resp = client.put("/kosync/syncs/annotations", json=body)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_annotations"
    assert "must-survive" in _live_ids(session, user)


def test_reap_is_not_undone_by_a_later_push(env):
    """Pins WHY the malformed-payload guard above matters: a tombstone is
    permanent by design, so a wrong reap can never be walked back."""
    s, user = env
    _seed(s, user, "kr-1")

    apply_push([], user=user, book=_book(), session=s, commit=s.commit,
               complete_for_source="koreader")
    assert _live_ids(s, user) == set()

    # The device still has it and pushes it again — it stays tombstoned.
    apply_push([{"annotation_id": "kr-1", "highlighted_text": "t"}],
               user=user, book=_book(), session=s, commit=s.commit,
               complete_for_source="koreader")
    assert _live_ids(s, user) == set()
