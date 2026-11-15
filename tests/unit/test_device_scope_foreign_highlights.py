# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sync-removal gap G6: a kept book sheds only PROVEN-foreign highlights.

Spec: state/kindle-kids/specs/2026-10-12-sync-removal.md, G6. Contract:
CONTRACT.md at the root of the PR A branch.

Before device-only highlights (share my place, section 17) a KOReader device
drew every highlight on the account into the book's sidecar. The pull now
serves only the device's own, so those older copies of another device's
highlights would sit on the device forever. The server proves which ones are
foreign; the plugin removes only those.

Server side, read as a spec: an identified KOReader device's highlight pull
also answers ``foreign_annotation_ids``, the server ids of this account's rows
for this book that another known device made (``origin_device_id`` set and not
this device), hidden rows included. A row with no recorded origin is not
proof and is never listed, nor is another account's row. A requester the
server cannot tie to a device gets no list (or an empty one).

Plugin side: ``foreign_highlight_cleanup_test.lua``, run below.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from cps import ub
from cps.progress_syncing.models import BookFormatChecksum
from cps.services import device_registry
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit

BOOK, OTHER_BOOK = 1, 2
DIGEST = "0123456789abcdef0123456789abcdef"
LUA_TEST = (Path(__file__).resolve().parents[2] / "koreader" / "plugins"
            / "cwngsync.koplugin" / "tests" / "foreign_highlight_cleanup_test.lua")


@pytest.fixture
def world(monkeypatch, tmp_path):
    import cps.progress_syncing.protocols.koreader_annotations  # noqa: F401 - routes
    built = LibraryWorld(monkeypatch, tmp_path)
    built.reader = built.add_user("user-a")
    built.other_user = built.add_user("user-b")
    built.add_book(BOOK, "First Light")
    built.add_book(OTHER_BOOK, "Second Light")
    built.session.add(BookFormatChecksum(book=BOOK, format="EPUB", checksum=DIGEST))
    built.session.commit()
    monkeypatch.setattr(
        built.kosync, "push_reading_state_to_hardcover", lambda *_a, **_k: None,
        raising=False,
    )
    monkeypatch.delenv("CWNG_SHARE_PLACE_GATES", raising=False)
    yield built
    built.close()


def _register(world, raw_id, name, user=None):
    internal = device_registry.register_koreader_device_best_effort(
        user_id=(user or world.reader).id, device_id=raw_id, device_name=name,
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


def _row(world, annotation_id, source, origin=None, *, hidden=False, user=None, book=BOOK):
    world.session.add(ub.Annotation(
        user_id=(user or world.reader).id, book_id=book, annotation_id=annotation_id,
        source=source, origin_device_id=origin.id if origin is not None else None,
        highlighted_text="words of " + annotation_id, highlight_color="yellow",
        hidden=hidden, annotation_type="highlight",
    ))
    world.session.commit()


def _pull(world, raw_id=None, name=None):
    headers = LibraryWorld.basic("user-a")
    headers["Accept"] = "application/vnd.koreader.v1+json"
    if raw_id is not None:
        headers["X-CWNG-Device-ID"] = raw_id
        headers["X-CWNG-Device-Name"] = name
    response = world.client.get(f"/kosync/syncs/annotations/{DIGEST}", headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


@pytest.fixture
def household(world):
    w = world
    w.kindle_a = _register(w, "kindle-a", "Kindle A")
    w.kindle_b = _register(w, "kindle-b", "Kindle B")
    w.kobo = _device(w, "kobo", "Kobo")
    w.browser = _device(w, "webreader", "Browser")
    _row(w, "a-visible", "koreader", w.kindle_a)
    _row(w, "a-deleted", "koreader", w.kindle_a, hidden=True)
    _row(w, "b-visible", "koreader", w.kindle_b)
    _row(w, "b-deleted", "koreader", w.kindle_b, hidden=True)
    _row(w, "kobo-visible", "kobo", w.kobo)
    _row(w, "web-visible", "webreader", w.browser)
    _row(w, "legacy-koreader", "koreader")
    _row(w, "legacy-kobo", "kobo")
    _row(w, "kobo-other-book", "kobo", w.kobo, book=OTHER_BOOK)
    other_device = _register(w, "their-kindle", "Kindle", user=w.other_user)
    _row(w, "theirs", "koreader", other_device, user=w.other_user)
    return w


def test_a_device_is_told_which_highlights_another_device_made(household):
    body = _pull(household, "kindle-a", "Kindle A")

    assert set(body["foreign_annotation_ids"]) == {
        "b-visible", "b-deleted", "kobo-visible", "web-visible"}
    # The highlights it is served are still only its own.
    assert {a["annotation_id"] for a in body["annotations"]} >= {"a-visible"}
    assert not {a["annotation_id"] for a in body["annotations"]} & set(
        body["foreign_annotation_ids"])


def test_each_device_gets_its_own_list(household):
    body = _pull(household, "kindle-b", "Kindle B")

    assert set(body["foreign_annotation_ids"]) == {
        "a-visible", "a-deleted", "kobo-visible", "web-visible"}


def test_a_highlight_with_no_recorded_origin_is_never_called_foreign(household):
    for raw_id, name in (("kindle-a", "Kindle A"), ("kindle-b", "Kindle B")):
        listed = set(_pull(household, raw_id, name)["foreign_annotation_ids"])
        assert not listed & {"legacy-koreader", "legacy-kobo"}
        assert "theirs" not in listed
        assert "kobo-other-book" not in listed


def test_an_unidentified_requester_is_told_nothing_is_foreign(household):
    body = _pull(household)

    assert not body.get("foreign_annotation_ids")


def test_a_device_with_nothing_foreign_gets_an_empty_list(world):
    _register(world, "kindle-a", "Kindle A")
    kindle = world.session.query(ub.Device).filter_by(user_id=world.reader.id,
                                                      kind="koreader").one()
    _row(world, "mine", "koreader", kindle)
    _row(world, "legacy", "koreader")

    body = _pull(world, "kindle-a", "Kindle A")

    assert body["foreign_annotation_ids"] == []


def _lua():
    for candidate in ("lua", "lua5.4", "lua5.3", "lua5.1", "luajit"):
        found = shutil.which(candidate)
        if found:
            return found
    pytest.skip("no Lua interpreter installed")


def test_the_plugin_removes_only_proven_foreign_highlights():
    result = subprocess.run([_lua(), LUA_TEST.name], cwd=LUA_TEST.parent,
                            capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "foreign_highlight_cleanup tests passed (6)" in result.stdout
