# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Share my place PR 5: highlights stay on the device that made them.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 17 (with
section 3 items 9-12 and section 8). Read as a spec:

* A device's highlight pull serves only the rows that device created
  (``origin_device_id`` is its own), hidden rows included so its deletions
  mirror back. Another device's rows, Kobo rows and web-reader rows are never
  served to it.
* Legacy rows with no recorded origin: ``koreader`` and ``textquote`` rows go
  to the account's registered KOReader devices; ``webreader`` and ``kobo`` rows
  go to no KOReader device.
* A requester the server cannot tie to a registered device (no
  ``X-CWNG-Device-ID`` header, or a header naming no device of this account)
  gets today's full pull (section 8).
* None of this depends on the place rollout switch: highlights are
  device-only with no checkbox, so ``CWNG_SHARE_PLACE_GATES=0`` changes nothing.

Identity on the annotation GET is the ``X-CWNG-Device-ID`` header (with
``X-CWNG-Device-Name``), the same pair the plugin's progress GET sends through
its ``CWNGDeviceHeaders`` middleware. A GET has no body, so there is nowhere
else for it to go; the plugin side of that contract is pinned by
``test_share_place_plugin_annotation_pull_headers.py``.

Everything runs through the real kosync blueprint over HTTP with real Basic
auth (``LibraryWorld``) and the real device registry.
"""

import pytest

from cps import ub
from cps.progress_syncing.models import BookFormatChecksum
from cps.services import device_registry
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit

BOOK = 1
DIGEST = "0123456789abcdef0123456789abcdef"
SWITCH = "CWNG_SHARE_PLACE_GATES"


@pytest.fixture
def world(monkeypatch, tmp_path):
    import cps.progress_syncing.protocols.koreader_annotations  # noqa: F401 - routes
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


@pytest.fixture(autouse=True)
def switch_unset(monkeypatch):
    """The shipped default; section 17 says highlights do not read it anyway."""
    monkeypatch.delenv(SWITCH, raising=False)


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


def _row(world, annotation_id, source, origin=None, *, hidden=False, user=None):
    world.session.add(ub.Annotation(
        user_id=(user or world.reader).id, book_id=BOOK, annotation_id=annotation_id,
        source=source, origin_device_id=origin.id if origin is not None else None,
        highlighted_text="words of " + annotation_id, highlight_color="yellow",
        hidden=hidden, annotation_type="highlight",
    ))
    world.session.commit()


def _headers(raw_id=None, name=None):
    headers = LibraryWorld.basic("reader")
    headers["Accept"] = "application/vnd.koreader.v1+json"
    if raw_id is not None:
        headers["X-CWNG-Device-ID"] = raw_id
        headers["X-CWNG-Device-Name"] = name
    return headers


def _pull(world, raw_id=None, name=None, *, quotes=False):
    response = world.client.get(
        f"/kosync/syncs/annotations/{DIGEST}",
        query_string={"text_quote": "1"} if quotes else None,
        headers=_headers(raw_id, name),
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["book_known"] is True
    assert body["annotation_count"] == len(body["annotations"])
    return {a["annotation_id"]: a for a in body["annotations"]}


@pytest.fixture
def household(world):
    """One account, every kind of reader, and a row of every provenance."""
    w = world
    w.kindle_a = _register(w, "kindle-a", "Kindle A")
    w.kindle_b = _register(w, "kindle-b", "Kindle B")
    w.words = _register(w, "words-c", "Word client")  # a text-quote client (#2466)
    w.kobo = _device(w, "kobo", "Libra")
    w.browser = _device(w, "webreader", "Browser")

    _row(w, "a-visible", "koreader", w.kindle_a)
    _row(w, "a-deleted", "koreader", w.kindle_a, hidden=True)
    _row(w, "b-visible", "koreader", w.kindle_b)
    _row(w, "b-deleted", "koreader", w.kindle_b, hidden=True)
    _row(w, "c-visible", "textquote", w.words)
    _row(w, "c-deleted", "textquote", w.words, hidden=True)
    _row(w, "kobo-visible", "kobo", w.kobo)
    _row(w, "web-visible", "webreader", w.browser)
    _row(w, "legacy-koreader", "koreader")
    _row(w, "legacy-textquote", "textquote")
    _row(w, "legacy-webreader", "webreader")
    _row(w, "legacy-kobo", "kobo")
    w.everything = {
        "a-visible", "a-deleted", "b-visible", "b-deleted", "c-visible",
        "c-deleted", "kobo-visible", "web-visible", "legacy-koreader",
        "legacy-textquote", "legacy-webreader", "legacy-kobo",
    }
    stranger = w.add_user("stranger")
    _row(w, "stranger-row", "koreader", user=stranger)
    return w


LEGACY_FOR_KOREADER = {"legacy-koreader", "legacy-textquote"}


# == 1. KOReader pull ========================================================

def test_identified_koreader_device_is_served_only_its_own_rows_and_legacy_koreader_rows(household):
    w = household

    served = _pull(w, "kindle-a", "Kindle A")

    assert set(served) == {"a-visible", "a-deleted"} | LEGACY_FOR_KOREADER
    # Its own deletion comes back as a tombstone, so the device mirrors it.
    assert served["a-deleted"]["hidden"] is True
    assert served["a-visible"]["hidden"] is False


def test_each_koreader_device_gets_its_own_rows_not_the_others(household):
    w = household

    a = _pull(w, "kindle-a", "Kindle A")
    b = _pull(w, "kindle-b", "Kindle B")

    assert "b-visible" not in a and "b-deleted" not in a
    assert "a-visible" not in b and "a-deleted" not in b
    assert set(b) == {"b-visible", "b-deleted"} | LEGACY_FOR_KOREADER


def test_kobo_and_web_reader_rows_never_reach_a_koreader_device(household):
    served = _pull(household, "kindle-a", "Kindle A")

    for foreign in ("kobo-visible", "web-visible", "legacy-webreader", "legacy-kobo",
                    "c-visible", "c-deleted"):
        assert foreign not in served


def test_unidentified_pull_is_todays_full_set(household):
    """Stock clients and old plugins send no header: section 8 keeps today's pull."""
    served = _pull(household)

    assert set(served) == household.everything
    assert served["b-deleted"]["hidden"] is True


def test_a_header_naming_no_device_of_this_account_is_unidentified(household):
    """Section 8: a requester the server cannot tie to a registered device behaves
    as today. The header alone does not make a device."""
    before = household.session.query(ub.Device).count()

    served = _pull(household, "never-seen", "New Kindle")

    assert set(served) == household.everything
    household.session.expire_all()
    assert household.session.query(ub.Device).count() == before


def test_a_highlight_pushed_by_one_kindle_is_not_pulled_by_the_other(world):
    """The whole wire, as two paired Kindles use it: today's Kindle-to-Kindle
    copying is what section 1 removes."""
    w = world
    _register(w, "kindle-a", "Kindle A")
    _register(w, "kindle-b", "Kindle B")
    pushed = w.client.put("/kosync/syncs/annotations", json={
        "document": DIGEST, "device": "Kindle A", "device_id": "kindle-a",
        "annotations": [{"annotation_id": "made-on-a", "highlighted_text": "alpha",
                         "position_type": "koreader_xpointer",
                         "start_xpointer": "/body/DocFragment[1]/body/p[1]/text().0"}],
    }, headers=_headers("kindle-a", "Kindle A"))
    assert pushed.status_code == 200, pushed.get_data(as_text=True)
    assert pushed.get_json()["created"] == 1

    assert "made-on-a" in _pull(w, "kindle-a", "Kindle A")
    assert "made-on-a" not in _pull(w, "kindle-b", "Kindle B")


# == 2. Text-quote pull ======================================================

def test_identified_text_quote_client_is_served_only_its_own_rows(household):
    served = _pull(household, "words-c", "Word client", quotes=True)

    assert set(served) == {"c-visible", "c-deleted"} | LEGACY_FOR_KOREADER
    assert served["c-deleted"]["hidden"] is True


def test_text_quote_pull_from_a_koreader_device_keeps_the_same_rule(household):
    served = _pull(household, "kindle-a", "Kindle A", quotes=True)

    assert set(served) == {"a-visible", "a-deleted"} | LEGACY_FOR_KOREADER


def test_unidentified_text_quote_pull_is_todays_full_set(household):
    served = _pull(household, quotes=True)

    assert set(served) == household.everything


# == 4. Independent of the place switch ======================================

@pytest.mark.parametrize("value", ["0", "user:999"])
def test_switching_place_sharing_off_does_not_bring_back_copied_highlights(
        household, monkeypatch, value):
    monkeypatch.setenv(SWITCH, value)

    served = _pull(household, "kindle-a", "Kindle A")

    assert set(served) == {"a-visible", "a-deleted"} | LEGACY_FOR_KOREADER


def test_a_device_with_share_my_place_on_still_gets_only_its_own_highlights(household):
    """The checkbox is place and read status only (section 1)."""
    w = household
    w.kindle_a.share_place = True
    w.kindle_b.share_place = True
    w.session.commit()

    served = _pull(w, "kindle-a", "Kindle A")

    assert set(served) == {"a-visible", "a-deleted"} | LEGACY_FOR_KOREADER


# == 5. Control: the web reader still shows every device's highlights ========
# Section 3.10 and 17: the web reader's view and export stay the union of
# every visible row, labelled by the device that made it. No device filter.

def _web(world, monkeypatch, path, view_name):
    import inspect
    from types import SimpleNamespace
    from cps import annotations

    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=world.reader.id))
    monkeypatch.setattr(annotations, "_resolve_book_or_404",
                        lambda _id: SimpleNamespace(id=BOOK, title="First Light"))
    monkeypatch.setattr(annotations, "_resolve_annotation_anchor",
                        lambda *_a: (None, "unresolved"))
    view = inspect.unwrap(getattr(annotations, view_name))
    with world.app.test_request_context(path, method="GET"):
        response = view(BOOK)
    assert response.status_code == 200
    return response


VISIBLE_EVERYWHERE = {"a-visible", "b-visible", "c-visible", "kobo-visible",
                      "web-visible", "legacy-koreader", "legacy-textquote",
                      "legacy-webreader", "legacy-kobo"}


def test_web_reader_data_shows_every_devices_visible_highlights_with_labels(
        household, monkeypatch):
    w = household

    body = _web(w, monkeypatch, f"/annotations/{BOOK}/data.json",
                "annotations_data").get_json()

    rows = {row["annotation_id"]: row for row in body["annotations"]}
    assert set(rows) == VISIBLE_EVERYWHERE
    labels = {public_id: device["label"] for public_id, device in body["devices"].items()}
    assert labels[rows["a-visible"]["origin_device_id"]] == "Kindle A"
    assert labels[rows["b-visible"]["origin_device_id"]] == "Kindle B"
    assert labels[rows["kobo-visible"]["origin_device_id"]] == "Libra"
    assert labels[rows["web-visible"]["origin_device_id"]] == "Browser"
    assert rows["legacy-kobo"]["origin_device_id"] is None


def test_web_reader_export_holds_every_devices_visible_highlights(household, monkeypatch):
    import json

    response = _web(household, monkeypatch, f"/annotations/{BOOK}/export.json",
                    "annotations_export_json")

    exported = json.loads(response.get_data(as_text=True))
    assert {row["annotation_id"] for row in exported["annotations"]} == VISIBLE_EVERYWHERE
