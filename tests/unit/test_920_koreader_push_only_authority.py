# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A push-only device may only delete what it knows it had (#920).

#906 let a device declare a push COMPLETE, and the server reaped every live row
of that source the push omitted. That inference is unsound, because the server
cannot tell these two pushes apart — they are byte-identical:

  * "the user deleted their last highlight"   (#905, must reap)
  * "I never had those highlights"            (#920, must NOT reap)

and the KOReader-native provider is *push-only* by construction:
``koreader_annotations_provider.applyToDevice`` returns 0 off-Kobo, so a second
device can never receive the first device's highlights — yet it still declared
its empty set complete. Opening the book on a second device therefore destroyed
every highlight from the first, permanently: the reap tombstones the rows, and
``apply_portable`` deliberately never un-hides a tombstone, so the re-push that
follows is ``skipped`` forever.

The device is the only party that can tell the two cases apart, because only it
knows which annotations it previously had. So the decision moves there: the
plugin diffs its own watermark (the ids it last pushed) against its live set and
names the deletions explicitly. The server obeys that list and infers nothing.

These tests pin the authority contract:
  - an empty/omitting push NEVER reaps, however complete it claims to be (#920);
  - explicitly named deletes are applied, so #905's delete-sync still works;
  - deletes stay scoped to (user, book, source) and soft-delete;
  - a malformed ``deleted`` / ``delete_source`` is a 400, never a 500.
"""

from __future__ import annotations

import importlib

import pytest
from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from types import SimpleNamespace

from cps import ub, calibre_db
from cps.services.annotation_sync import register_handler, reset_registry_for_testing
from cps.services.annotation_sync.base import AnnotationSyncTargetHandler, SyncResult
from cps.progress_syncing.protocols.koreader_annotations import apply_push

pytestmark = pytest.mark.unit


class StubHandler(AnnotationSyncTargetHandler):
    target_name = "stub"

    def __init__(self):
        self.deletes = []

    def is_enabled(self, user):
        return True

    def push(self, annotation, book, user, payload=None):
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
    other = ub.User(name="other", email="o@e.com", role=0, password="x")
    s.add_all([user, other])
    s.commit()
    monkeypatch.setattr(ub, "session", s)
    monkeypatch.setattr(ub, "session_commit", lambda: s.commit())
    yield s, user, other


def _book():
    return SimpleNamespace(id=7, uuid="bk-7", title="Book")


def _seed(s, user_id, aid, *, book_id=7, source="koreader", hidden=False):
    s.add(ub.Annotation(
        user_id=user_id, annotation_id=aid, book_id=book_id, source=source,
        highlighted_text="t", highlight_color="yellow",
        start_container_path="span#kobo.1.1", start_offset=0,
        end_container_path="span#kobo.1.1", end_offset=4, hidden=hidden,
    ))
    s.commit()


def _live_ids(s, user_id, book_id=7):
    rows = s.query(ub.Annotation).filter(
        ub.Annotation.user_id == user_id,
        ub.Annotation.book_id == book_id,
    ).all()
    return {r.annotation_id for r in rows if not r.hidden}


@pytest.fixture
def wire(env, monkeypatch):
    """Real Flask routing, so the contract is exercised through the actual PUT
    the plugin makes (auth + blueprint + JSON shape), not just the callable."""
    session, user, other = env
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
        if document == "digest-920" else (None, None, None, None, None),
    )
    monkeypatch.setattr(calibre_db, "get_book", lambda _id: book)
    app = Flask(__name__)
    app.register_blueprint(kosync_routes.kosync)
    return app.test_client(), session, user


# --- the reported bug ------------------------------------------------------

@pytest.mark.parametrize("empty", [{}, []])
def test_second_device_empty_complete_push_does_not_destroy_highlights(wire, empty):
    """#920: device A opens the book, cannot apply device B's rows (push-only),
    and pushes its empty set as complete. The highlights must survive."""
    client, session, user = wire
    for aid in ("kr-1", "kr-2", "kr-3"):
        _seed(session, user.id, aid)

    res = client.put("/kosync/syncs/annotations", json={
        "document": "digest-920",
        "annotations": empty,
        "complete": True,
        "complete_source": "koreader",
    })

    assert res.status_code == 200
    assert res.get_json()["deleted"] == 0
    assert _live_ids(session, user.id) == {"kr-1", "kr-2", "kr-3"}


def test_omitting_a_row_never_reaps_it_even_when_others_are_pushed(wire):
    """A partial set is not evidence of a delete — only an explicit list is."""
    client, session, user = wire
    _seed(session, user.id, "kr-mine")
    _seed(session, user.id, "kr-theirs")

    res = client.put("/kosync/syncs/annotations", json={
        "document": "digest-920",
        "annotations": [{
            "annotation_id": "kr-mine", "source": "koreader",
            "highlighted_text": "t", "color": "yellow",
        }],
        "complete": True,
        "complete_source": "koreader",
    })

    assert res.status_code == 200
    assert _live_ids(session, user.id) == {"kr-mine", "kr-theirs"}


# --- #905 preserved: a named delete still syncs ----------------------------

def test_device_reported_delete_is_applied(wire):
    """#905's requirement, now carried explicitly instead of by omission."""
    client, session, user = wire
    _seed(session, user.id, "kr-gone")
    _seed(session, user.id, "kr-kept")

    res = client.put("/kosync/syncs/annotations", json={
        "document": "digest-920",
        "annotations": [],
        "deleted": ["kr-gone"],
    })

    assert res.status_code == 200
    assert res.get_json()["deleted"] == 1
    assert res.get_json()["reconciled"] is True
    assert _live_ids(session, user.id) == {"kr-kept"}


def test_deleting_the_last_highlight_syncs(wire):
    """The single-device case #905 shipped for: the set legitimately goes empty."""
    client, session, user = wire
    _seed(session, user.id, "kr-last")

    res = client.put("/kosync/syncs/annotations", json={
        "document": "digest-920",
        "annotations": [],
        "deleted": ["kr-last"],
    })

    assert res.status_code == 200
    assert _live_ids(session, user.id) == set()


def test_reported_delete_soft_deletes_so_other_devices_can_mirror_it(wire):
    """Pull hands other devices a tombstone, so the row must survive hidden."""
    client, session, user = wire
    _seed(session, user.id, "kr-gone")

    client.put("/kosync/syncs/annotations", json={
        "document": "digest-920", "annotations": [], "deleted": ["kr-gone"],
    })

    row = session.query(ub.Annotation).filter_by(annotation_id="kr-gone").one()
    assert row.hidden is True


def test_reported_delete_dispatches_delete_fanout(env):
    session, user, _other = env
    handler = StubHandler()
    register_handler(handler)
    _seed(session, user.id, "fanout-me")
    row = session.query(ub.Annotation).filter_by(annotation_id="fanout-me").one()
    session.add(ub.AnnotationSyncTarget(
        annotation_id=row.id, target="stub", target_record_id="r1", status="synced",
    ))
    session.commit()

    apply_push([], user=user, book=_book(), session=session, commit=session.commit,
               deleted_ids=["fanout-me"])

    assert handler.deletes == ["r1"]


def test_already_hidden_row_is_not_deleted_again(env):
    """No duplicate fan-out when a device re-reports a delete it already sent."""
    session, user, _other = env
    _seed(session, user.id, "kr-gone", hidden=True)

    summary = apply_push([], user=user, book=_book(), session=session,
                         commit=session.commit, deleted_ids=["kr-gone"])

    assert summary["deleted"] == 0


# --- scoping: a delete may only reach the device's own rows -----------------

def test_reported_delete_never_crosses_source(wire):
    """A KOReader sync must never delete a Kobo-native or web-reader highlight."""
    client, session, user = wire
    _seed(session, user.id, "web-1", source="webreader")
    _seed(session, user.id, "kobo-1", source="kobo")

    res = client.put("/kosync/syncs/annotations", json={
        "document": "digest-920", "annotations": [],
        "deleted": ["web-1", "kobo-1"],
    })

    assert res.status_code == 200
    assert res.get_json()["deleted"] == 0
    assert _live_ids(session, user.id) == {"web-1", "kobo-1"}


def test_reported_delete_never_crosses_user_or_book(env):
    session, user, other = env
    _seed(session, other.id, "kr-other-user")
    _seed(session, user.id, "kr-other-book", book_id=99)

    summary = apply_push([], user=user, book=_book(), session=session,
                         commit=session.commit,
                         deleted_ids=["kr-other-user", "kr-other-book"])

    assert summary["deleted"] == 0
    assert _live_ids(session, other.id) == {"kr-other-user"}
    assert _live_ids(session, user.id, book_id=99) == {"kr-other-book"}


def test_unknown_delete_source_cannot_delete(wire):
    client, session, user = wire
    _seed(session, user.id, "kr-1")

    res = client.put("/kosync/syncs/annotations", json={
        "document": "digest-920", "annotations": [],
        "deleted": ["kr-1"], "delete_source": "webreader",
    })

    assert res.status_code == 400
    assert _live_ids(session, user.id) == {"kr-1"}


# --- malformed input is a 400, never a 500 ---------------------------------

@pytest.mark.parametrize("deleted", [
    "kr-1",            # a bare string, not a list
    {"a": 1},          # an object
    [""],              # an empty id
    ["  "],            # a whitespace-only id
    [None],            # a non-string id
    [{"id": "kr-1"}],  # a portable dict where an id belongs
])
def test_malformed_deleted_list_is_rejected(wire, deleted):
    client, session, user = wire
    _seed(session, user.id, "kr-1")

    res = client.put("/kosync/syncs/annotations", json={
        "document": "digest-920", "annotations": [], "deleted": deleted,
    })

    assert res.status_code == 400
    assert _live_ids(session, user.id) == {"kr-1"}


@pytest.mark.parametrize("source", [["koreader"], {"a": 1}, 7])
def test_non_string_delete_source_is_a_400_not_a_500(wire, source):
    """#920's companion: an unhashable source used to raise TypeError -> 500."""
    client, session, user = wire
    _seed(session, user.id, "kr-1")

    res = client.put("/kosync/syncs/annotations", json={
        "document": "digest-920", "annotations": [],
        "deleted": ["kr-1"], "delete_source": source,
    })

    assert res.status_code == 400
    assert _live_ids(session, user.id) == {"kr-1"}


def test_a_bogus_delete_source_does_not_reject_a_push_that_deletes_nothing(wire):
    """`delete_source` scopes the deletions; with none to scope it has no
    effect, and rejecting the push over it would throw away the annotations
    the push actually carries."""
    client, session, user = wire

    res = client.put("/kosync/syncs/annotations", json={
        "document": "digest-920",
        "annotations": [{
            "annotation_id": "kr-new", "source": "koreader",
            "highlighted_text": "t", "color": "yellow",
        }],
        "deleted": [],
        "delete_source": "not-a-source",
    })

    assert res.status_code == 200
    assert res.get_json()["created"] == 1
    assert _live_ids(session, user.id) == {"kr-new"}


def test_reconciled_reports_that_deletes_were_named_not_that_rows_matched(wire):
    """Naming an id that matches nothing is still a reconciled push."""
    client, session, user = wire
    _seed(session, user.id, "kr-1")

    res = client.put("/kosync/syncs/annotations", json={
        "document": "digest-920", "annotations": [],
        "deleted": ["never-existed"],
    })

    assert res.status_code == 200
    assert res.get_json()["reconciled"] is True
    assert res.get_json()["deleted"] == 0
    assert _live_ids(session, user.id) == {"kr-1"}


def test_no_deletes_reported_means_nothing_reconciled(wire):
    client, session, user = wire
    _seed(session, user.id, "kr-1")

    res = client.put("/kosync/syncs/annotations", json={
        "document": "digest-920", "annotations": [],
    })

    assert res.status_code == 200
    assert res.get_json()["reconciled"] is False
    assert _live_ids(session, user.id) == {"kr-1"}
