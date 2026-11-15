# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Share my place §17b: a device's leftover copies never overwrite or delete
their owner's row.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md §17b. A KOReader
that pulled other devices' highlights before PR 5 still holds those copies and
pushes them back. Read as a spec:

* An identified pushing device sending a row it does not own -- another
  device's row, or a NULL-origin row whose source is outside its legacy set
  (the pull filter's predicate: ``koreader``/``textquote`` for a KOReader) --
  leaves that row untouched and the push counts it ``unchanged``. That holds
  for a stale overwrite, an inline ``hidden`` and a named delete.
* Its own rows and its legacy rows behave as today.
* An unidentified push behaves as today (section 8).

Everything runs through the real ``PUT /kosync/syncs/annotations`` route with
real Basic auth (``LibraryWorld``) and the real device registry; the pushing
device is identified the way the plugin does it, by ``device_id`` in the body.
"""

import pytest

from cps import ub
from cps.progress_syncing.models import BookFormatChecksum
from cps.services import device_registry
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit

BOOK = 1
DIGEST = "0123456789abcdef0123456789abcdef"
XP = "/body/DocFragment[1]/body/p[1]/text().0"
OLD_NOTE = "note as it was"
NEW_NOTE = "note edited in the web reader"


@pytest.fixture
def world(monkeypatch, tmp_path):
    import cps.progress_syncing.protocols.koreader_annotations  # noqa: F401 - routes
    monkeypatch.delenv("CWNG_SHARE_PLACE_GATES", raising=False)
    built = LibraryWorld(monkeypatch, tmp_path)
    built.reader = built.add_user("reader")
    built.add_book(BOOK, "First Light")
    built.session.add(BookFormatChecksum(book=BOOK, format="EPUB", checksum=DIGEST))
    built.session.commit()
    # Not part of these tests: keep the highlight fan-out quiet.
    monkeypatch.setattr(
        built.kosync, "push_reading_state_to_hardcover", lambda *_a, **_k: None,
        raising=False,
    )
    yield built
    built.close()


def _register(world, raw_id, name):
    internal = device_registry.register_koreader_device_best_effort(
        user_id=world.reader.id, device_id=raw_id, device_name=name,
        secret_key=world.app.secret_key,
    )
    assert internal is not None
    world.session.expire_all()
    return world.session.get(ub.Device, internal)


def _device(world, kind, name):
    device = ub.Device(user_id=world.reader.id, kind=kind, display_name=name,
                       active=True, created_by="auto")
    world.session.add(device)
    world.session.commit()
    return device


def _row(world, annotation_id, source, origin=None):
    world.session.add(ub.Annotation(
        user_id=world.reader.id, book_id=BOOK, annotation_id=annotation_id,
        source=source, origin_device_id=origin.id if origin is not None else None,
        highlighted_text="words of " + annotation_id, highlight_color="yellow",
        note_text=OLD_NOTE, hidden=False, annotation_type="highlight",
        position_type="koreader_xpointer", start_xpointer=XP, end_xpointer=XP,
    ))
    world.session.commit()


@pytest.fixture
def household(world):
    w = world
    w.kindle_a = _register(w, "kindle-a", "Kindle A")
    w.kindle_b = _register(w, "kindle-b", "Kindle B")
    w.words = _register(w, "words-c", "Word client")
    w.kobo = _device(w, "kobo", "Libra")
    w.browser = _device(w, "webreader", "Browser")
    _row(w, "a-own", "koreader", w.kindle_a)
    _row(w, "b-own", "koreader", w.kindle_b)
    _row(w, "c-own", "textquote", w.words)
    _row(w, "kobo-own", "kobo", w.kobo)
    _row(w, "web-own", "webreader", w.browser)
    _row(w, "legacy-koreader", "koreader")
    _row(w, "legacy-textquote", "textquote")
    _row(w, "legacy-webreader", "webreader")  # NULL origin, outside A's legacy set
    _row(w, "legacy-kobo", "kobo")            # NULL origin, outside A's legacy set
    return w


def _stored(world, annotation_id):
    world.session.expire_all()
    return world.session.query(ub.Annotation).filter_by(
        user_id=world.reader.id, book_id=BOOK, annotation_id=annotation_id).one()


def _web_edit(world, annotation_id):
    row = _stored(world, annotation_id)
    row.note_text = NEW_NOTE
    world.session.commit()


def _stale_copy(annotation_id, **extra):
    """What a KOReader that pulled this row before PR 5 sends back: its local
    copy, carrying the note as it was when it pulled."""
    payload = {
        "annotation_id": annotation_id, "highlighted_text": "words of " + annotation_id,
        "note_text": OLD_NOTE, "color": "yellow", "type": "highlight",
        "position_type": "koreader_xpointer", "start_xpointer": XP, "end_xpointer": XP,
    }
    payload.update(extra)
    return payload


def _push(world, *, annotations=(), deleted=(), device_id="kindle-a",
          device="Kindle A", delete_source=None):
    body = {"document": DIGEST, "annotations": list(annotations), "deleted": list(deleted)}
    headers = LibraryWorld.basic("reader")
    headers["Accept"] = "application/vnd.koreader.v1+json"
    if device_id is not None:
        body.update(device_id=device_id, device=device)
        headers["X-CWNG-Device-ID"] = device_id
        headers["X-CWNG-Device-Name"] = device
    if delete_source is not None:
        body["delete_source"] = delete_source
    response = world.client.put("/kosync/syncs/annotations", json=body, headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    summary = response.get_json()
    assert summary.get("matched", True) is not False
    return summary


NOT_A_S = ["kobo-own", "b-own", "c-own", "web-own", "legacy-webreader", "legacy-kobo"]


# == (a) A stale copy does not overwrite its owner's row ======================

@pytest.mark.parametrize("annotation_id", NOT_A_S)
def test_a_stale_copy_from_kindle_a_leaves_a_later_web_edit_in_place(household, annotation_id):
    _web_edit(household, annotation_id)

    summary = _push(household, annotations=[_stale_copy(annotation_id)])

    assert _stored(household, annotation_id).note_text == NEW_NOTE
    assert (summary["unchanged"], summary["updated"]) == (1, 0)


# == (b) A named or inline delete of another device's row is ignored =========

@pytest.mark.parametrize("annotation_id,delete_source", [
    ("b-own", None),             # another KOReader
    ("kobo-own", None),          # a Kobo
    ("c-own", "textquote"),      # a text-quote client, under its own source
])
def test_kindle_as_named_delete_of_another_devices_row_leaves_it_visible(
        household, annotation_id, delete_source):
    summary = _push(household, deleted=[annotation_id], delete_source=delete_source)

    assert not _stored(household, annotation_id).hidden
    assert summary["deleted"] == 0


def test_kindle_as_inline_hidden_on_another_kindles_row_leaves_it_visible(household):
    summary = _push(household, annotations=[_stale_copy("b-own", hidden=True)])

    assert not _stored(household, "b-own").hidden
    assert (summary["unchanged"], summary["deleted"]) == (1, 0)


# == (c) Control: A's own rows and its legacy rows behave as today ============

@pytest.mark.parametrize("annotation_id", ["a-own", "legacy-koreader", "legacy-textquote"])
def test_kindle_a_still_edits_its_own_and_legacy_rows(household, annotation_id):
    summary = _push(household, annotations=[_stale_copy(annotation_id, note_text=NEW_NOTE)])

    assert _stored(household, annotation_id).note_text == NEW_NOTE
    assert summary["updated"] == 1


@pytest.mark.parametrize("annotation_id,delete_source", [
    ("a-own", None), ("legacy-koreader", None), ("legacy-textquote", "textquote"),
])
def test_kindle_a_still_deletes_its_own_and_legacy_rows(household, annotation_id, delete_source):
    summary = _push(household, deleted=[annotation_id], delete_source=delete_source)

    assert _stored(household, annotation_id).hidden
    assert summary["deleted"] == 1


# == (d) Control: an unidentified push behaves as today =======================

def test_an_unidentified_stale_push_still_overwrites_as_today(household):
    _web_edit(household, "kobo-own")

    summary = _push(household, annotations=[_stale_copy("kobo-own")], device_id=None)

    assert _stored(household, "kobo-own").note_text == OLD_NOTE
    assert summary["updated"] == 1


def test_an_unidentified_named_delete_still_applies_as_today(household):
    summary = _push(household, deleted=["b-own"], device_id=None)

    assert _stored(household, "b-own").hidden
    assert summary["deleted"] == 1
