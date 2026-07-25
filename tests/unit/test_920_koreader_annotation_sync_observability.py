# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""KOReader annotation sync must say what it did, and what it refused (#920).

The device shows the user one bit — "Highlights synced" or "Server push
failed" — and KOReader's plugin cannot report *why* a push failed, because the
only thing it has is the HTTP status. So the server log is the sole diagnostic
surface for every highlight-sync complaint.

It was silent. Verified on the wire against a live container: four distinct
outcomes on ``PUT /kosync/syncs/annotations`` — two 400 rejections, an unknown
document, and a silently-dropped malformed annotation — produced **zero** log
lines between them. The progress route next door logs every save at INFO
(promoted in #312 for exactly this reason); the annotation routes never got the
same treatment, so a reporter running ``docker logs | grep annotation`` after a
failed sync sees nothing at all and has nothing to send us.

Two of those outcomes are worse than silent, because they are HTTP **200** and
the device therefore tells the user the sync succeeded:

  * unknown document  -> ``matched: false``, nothing saved, device says "synced"
  * malformed payload -> ``skipped: N``, the highlight is dropped on the floor

These tests pin that every one of those paths leaves a line naming the user,
the book/document and the reason.
"""

from __future__ import annotations

import importlib
import logging

import pytest
from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from types import SimpleNamespace

from cps import ub, calibre_db
from cps.services.annotation_sync import reset_registry_for_testing

pytestmark = pytest.mark.unit

LOGGER_NAME = "cps.progress_syncing.protocols.koreader_annotations"
DIGEST = "digest-920-obs"


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


def _seed(s, user_id, aid, *, book_id=7, source="koreader", hidden=False):
    s.add(ub.Annotation(
        user_id=user_id, annotation_id=aid, book_id=book_id, source=source,
        highlighted_text="t", highlight_color="yellow",
        start_container_path="span#kobo.1.1", start_offset=0,
        end_container_path="span#kobo.1.1", end_offset=4, hidden=hidden,
    ))
    s.commit()


@pytest.fixture
def wire(env, monkeypatch):
    """Real Flask routing — the contract is exercised through the actual PUT/GET
    the plugin makes, so a rejection that happens before the handler body still
    has to account for itself."""
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
        if document == DIGEST else (None, None, None, None, None),
    )
    monkeypatch.setattr(calibre_db, "get_book", lambda _id: book)
    app = Flask(__name__)
    app.register_blueprint(kosync_routes.kosync)
    return app.test_client(), session, user


@pytest.fixture
def logs(caplog):
    """Capture this module's own logger, whatever handlers cps.logger attached."""
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    logging.getLogger(LOGGER_NAME).propagate = True
    return caplog


def _lines(logs, min_level=logging.INFO):
    return [r.getMessage() for r in logs.records
            if r.name == LOGGER_NAME and r.levelno >= min_level]


def _valid_annotation(aid="koreader-obs-1"):
    return {
        "annotation_id": aid,
        "source": "koreader",
        "highlighted_text": "hello",
        "start_container_path": "span#kobo.1.1",
        "start_offset": 0,
        "end_container_path": "span#kobo.1.1",
        "end_offset": 5,
    }


# --- rejections must not be silent -----------------------------------------

def test_invalid_deleted_400_is_logged(wire, logs):
    """A `deleted` list carrying a non-string is a 400 the device reports only
    as "Server push failed". The log has to name the reason."""
    client, _s, _u = wire
    r = client.put("/kosync/syncs/annotations", json={
        "document": DIGEST, "annotations": {},
        "deleted": [123], "delete_source": "koreader",
    })
    assert r.status_code == 400
    out = " | ".join(_lines(logs))
    assert "invalid_deleted" in out, f"rejection was silent; log was: {out!r}"
    assert DIGEST in out


def test_invalid_delete_source_400_is_logged(wire, logs):
    client, _s, _u = wire
    r = client.put("/kosync/syncs/annotations", json={
        "document": DIGEST, "annotations": [],
        "deleted": ["a"], "delete_source": "kobo",
    })
    assert r.status_code == 400
    out = " | ".join(_lines(logs))
    assert "invalid_delete_source" in out, f"rejection was silent; log was: {out!r}"


def test_invalid_annotation_400_names_the_offending_index(wire, logs):
    client, _s, _u = wire
    # `highlighted_text` as a non-string is what validate_portable_payload
    # actually rejects; a merely unrecognised key is counted as skipped, not
    # refused (covered by test_skipped_annotations_are_logged_as_dropped).
    r = client.put("/kosync/syncs/annotations", json={
        "document": DIGEST,
        "annotations": [_valid_annotation(), {"annotation_id": "x",
                                              "highlighted_text": 5}],
    })
    assert r.status_code == 400
    out = " | ".join(_lines(logs))
    assert "invalid_annotation" in out, f"rejection was silent; log was: {out!r}"
    assert "annotations[1]" in out, \
        f"the rejected annotation's index must be in the log; got: {out!r}"


def test_malformed_document_400_is_logged(wire, logs):
    # A colon is reserved for internal use, so is_valid_key_field refuses it.
    client, _s, _u = wire
    r = client.put("/kosync/syncs/annotations", json={
        "document": "bad:document", "annotations": [],
    })
    assert r.status_code == 400
    assert _lines(logs), "a rejected document field left no log line"


def test_non_object_body_400_is_logged(wire, logs):
    client, _s, _u = wire
    r = client.put("/kosync/syncs/annotations", json=["not", "an", "object"])
    assert r.status_code == 400
    assert _lines(logs), "a malformed body left no log line"


# --- the 200s that save nothing are the dangerous ones ----------------------

def test_unknown_document_logs_that_nothing_was_saved(wire, logs):
    """HTTP 200 + matched:false. The device tells the user the sync worked while
    the server stored nothing, so this must be loud."""
    client, _s, _u = wire
    r = client.put("/kosync/syncs/annotations", json={
        "document": "ffffffffffffffffffffffffffffffff",
        "annotations": [_valid_annotation()],
    })
    assert r.status_code == 200
    assert r.get_json()["matched"] is False
    out = " | ".join(_lines(logs, logging.WARNING))
    assert out, "an unmatched document saved nothing and logged nothing"
    assert "ffffffffffffffffffffffffffffffff" in out


UNMATCHED_BODY = {
    "document": "ffffffffffffffffffffffffffffffff", "matched": False,
    "created": 0, "updated": 0, "deleted": 0, "skipped": 0,
}


@pytest.mark.parametrize("payload", [
    {"annotations": 1},                      # truthy scalar
    {"annotations": True},                   # bool is not a container
    {"annotations": [], "deleted": True},
    {"annotations": {"a": 1, "b": 2}},       # dict: countable, but not an array
    {"annotations": "text"},
    {},                                      # both fields absent
])
def test_unmatched_book_never_500s_on_an_unvalidated_shape(wire, logs, payload):
    """The unmatched-book reply is returned BEFORE annotations/deleted are
    shape-checked, so it sees whatever JSON carried. It has always answered 200
    for these; a diagnostic count that calls len() on them turns that into a
    500. Found by cross-family review of this change and confirmed on the wire
    (`{"annotations": 1}` -> TypeError: object of type 'int' has no len())."""
    client, _s, _u = wire
    r = client.put("/kosync/syncs/annotations", json={
        "document": "ffffffffffffffffffffffffffffffff", **payload,
    })
    assert r.status_code == 200, f"{payload} regressed to {r.status_code}"
    assert r.get_json() == UNMATCHED_BODY
    assert _lines(logs, logging.WARNING), "still has to say nothing was saved"


def test_unmatched_count_does_not_miscount_a_non_array(wire, logs):
    """A dict has a len(), so it counts without raising — and would report
    "2 annotation(s)" for something that is not an annotation array."""
    client, _s, _u = wire
    client.put("/kosync/syncs/annotations", json={
        "document": "ffffffffffffffffffffffffffffffff",
        "annotations": {"a": 1, "b": 2},
    })
    out = " | ".join(_lines(logs, logging.WARNING))
    assert "2 annotation(s)" not in out, f"miscounted a non-array: {out!r}"
    assert "not an array: dict" in out


@pytest.mark.parametrize("payload,expected", [
    (["not", "an", "object"],
     {"error": "invalid_payload", "message": "JSON object required"}),
    ({"document": "bad:document", "annotations": []},
     {"error": 2004, "message": "Invalid document field"}),
    ({"document": DIGEST, "annotations": 1},
     {"error": "invalid_annotations", "message": "annotations must be an array"}),
    ({"document": DIGEST, "annotations": {}, "deleted": [123],
      "delete_source": "koreader"},
     {"error": "invalid_deleted",
      "message": "deleted must be an array of annotation_id strings"}),
    ({"document": DIGEST, "annotations": [], "deleted": ["a"],
      "delete_source": "kobo"},
     {"error": "invalid_delete_source",
      "message": "delete_source must be one of: koreader"}),
    ({"document": DIGEST,
      "annotations": [{"annotation_id": "x", "highlighted_text": 5}]},
     {"error": "invalid_annotation",
      "message": "annotations[0]: highlighted_text must be a string or null"}),
])
def test_rejection_bodies_are_unchanged_by_the_helper(wire, payload, expected):
    """`_reject()` replaced inline `create_sync_response({...}, 400)` calls at
    six sites. Pin the whole body, not the status and not a substring: a
    logging-only assertion would still pass if the helper dropped `message`,
    and a substring would still pass if the wording inverted its meaning."""
    client, _s, _u = wire
    r = client.put("/kosync/syncs/annotations", json=payload)
    assert r.status_code == 400
    assert r.get_json() == expected


def test_skipped_annotations_are_logged_as_dropped(wire, logs):
    """HTTP 200 + skipped:N — the highlight never lands and the device still
    says "synced". Silently losing a user's highlight must leave a trace."""
    client, _s, _u = wire
    r = client.put("/kosync/syncs/annotations", json={
        "document": DIGEST, "annotations": [_valid_annotation()],
    })
    assert r.status_code == 200
    # Re-push the same annotation after it has been tombstoned: apply_portable
    # never un-hides, so this is the `skipped` path.
    _s.query(ub.Annotation).update({ub.Annotation.hidden: True})
    _s.commit()
    logs.clear()
    r2 = client.put("/kosync/syncs/annotations", json={
        "document": DIGEST, "annotations": [_valid_annotation()],
    })
    assert r2.status_code == 200
    assert r2.get_json()["skipped"] == 1
    out = " | ".join(_lines(logs, logging.WARNING))
    assert out, "a dropped (skipped) highlight logged nothing"
    assert "skipped" in out.lower()


# --- the happy paths need a line too, or "it worked" is unfalsifiable -------

def test_successful_push_logs_the_counts(wire, logs):
    client, _s, user = wire
    r = client.put("/kosync/syncs/annotations", json={
        "document": DIGEST, "annotations": [_valid_annotation()],
    })
    assert r.status_code == 200
    out = " | ".join(_lines(logs))
    assert out, "a successful push logged nothing"
    assert "created=1" in out.replace(" ", "") or "created 1" in out
    assert str(user.id) in out


def test_successful_delete_logs_its_counts(wire, logs):
    """The delete path is the one @iroQuai's device exercises; it needs its own
    line so a report can be settled from the log alone. Deliberately the counts
    and not the ids: naming every deleted id on a successful sync is log volume
    with no diagnostic gain, and the ids are logged in the one case where they
    matter (they matched nothing — see below)."""
    client, s, user = wire
    _seed(s, user.id, "koreader-obs-del")
    r = client.put("/kosync/syncs/annotations", json={
        "document": DIGEST, "annotations": {},
        "deleted": ["koreader-obs-del"], "delete_source": "koreader",
    })
    assert r.status_code == 200
    assert r.get_json()["deleted"] == 1
    out = " | ".join(_lines(logs))
    assert "deleted=1" in out.replace(" ", "") or "deleted 1" in out, \
        f"the delete push logged no counts; log was: {out!r}"


def test_delete_naming_unknown_ids_is_logged(wire, logs):
    """Device names ids the server has no live row for — deletes 0 and returns
    200. That is the "my deletes do nothing" report, and it needs a line."""
    client, _s, _u = wire
    r = client.put("/kosync/syncs/annotations", json={
        "document": DIGEST, "annotations": {},
        "deleted": ["koreader-does-not-exist"], "delete_source": "koreader",
    })
    assert r.status_code == 200
    assert r.get_json()["deleted"] == 0
    out = " | ".join(_lines(logs))
    assert "koreader-does-not-exist" in out or "0" in out, \
        "a delete that matched nothing left no trace"


def test_pull_logs_what_it_handed_the_device(wire, logs):
    client, s, user = wire
    _seed(s, user.id, "koreader-obs-pull")
    r = client.get(f"/kosync/syncs/annotations/{DIGEST}")
    assert r.status_code == 200
    out = " | ".join(_lines(logs))
    assert out, "the pull handed the device rows and logged nothing"


def test_device_supplied_values_cannot_forge_log_lines(wire, logs):
    """The log is the diagnostic surface for these reports, so a device must not
    be able to write its own lines into it. `is_valid_key_field` only bars
    colons and over-length, so a newline reaches the logger unless escaped."""
    client, _s, _u = wire
    forged = "aaa\n[2026-01-01 00:00:00]  INFO {cps} everything is fine"
    r = client.put("/kosync/syncs/annotations", json={
        "document": forged, "annotations": [_valid_annotation()],
    })
    # Refused — and the refusal is precisely where the raw, failed-validation
    # value gets logged, so this is the path that has to escape it.
    assert r.status_code == 400
    out = " | ".join(_lines(logs))
    assert out, "the rejection logged nothing"
    assert "\n" not in out, f"a raw newline reached the log: {out!r}"
    assert "\\n" in out, "the newline should be escaped, not stripped"


def test_an_overlong_device_value_cannot_flood_the_log(wire, logs):
    client, _s, _u = wire
    r = client.put("/kosync/syncs/annotations", json={
        "document": "x" * 5000, "annotations": [],
    })
    assert r.status_code == 400
    out = " | ".join(_lines(logs))
    assert out and len(out) < 400, f"one bad push wrote {len(out)} chars of log"


def test_pull_of_unknown_document_is_logged(wire, logs):
    client, _s, _u = wire
    r = client.get("/kosync/syncs/annotations/ffffffffffffffffffffffffffffffff")
    assert r.status_code == 200
    assert r.get_json()["annotation_count"] == 0
    assert _lines(logs), "an unknown document on pull logged nothing"
