# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Marking shelves for e-reader sync works on a server that syncs only KOReader.

The per-shelf mark decides what a shelf-only account's e-readers hold, for
Kobo and for the KOReader library alike. It used to be accepted only while
Kobo sync was on, so a household with KOReader devices and no Kobo could not
choose its shelves at all. The chain is checked end to end: the web API marks
the shelf, and the KOReader library manifest then carries exactly its books.
"""

import pytest

from cps import config, ub
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = LibraryWorld(monkeypatch, tmp_path)
    w.enable_web()
    monkeypatch.setattr(config, "config_kobo_sync", False, raising=False)
    w.alice = w.add_user("alice", shelf_only=True)
    w.add_book(1, "Bedtime Stories")
    w.add_book(2, "Tax Law")
    yield w
    w.close()


def manifest(world):
    return world.client.get("/kosync/syncs/library",
                            headers=world.device_headers("alice")).get_json()


def test_a_koreader_only_server_lets_readers_choose_their_shelves(world):
    shelf = world.shelf(world.alice, "Bedtime", [1])
    browser = world.browser("alice")

    marked = browser.post("/api/v1/shelves/%d" % shelf.id, json={"kobo_sync": True})
    assert marked.status_code == 200
    created = browser.post("/api/v1/shelves", json={"name": "Later", "kobo_sync": True})
    assert created.status_code == 201

    world.session.expire_all()
    assert world.session.get(ub.Shelf, shelf.id).kobo_sync
    assert world.session.query(ub.Shelf).filter_by(name="Later").one().kobo_sync
    library = manifest(world)
    assert library["scope"] == "shelves"
    assert [book["book_id"] for book in library["books"]] == [1]


def test_with_no_e_reader_sync_on_the_mark_is_not_stored(world):
    world.sync_switch(False)
    shelf = world.shelf(world.alice, "Bedtime", [1])
    world.browser("alice").post("/api/v1/shelves/%d" % shelf.id, json={"kobo_sync": True})
    world.session.expire_all()
    assert not world.session.get(ub.Shelf, shelf.id).kobo_sync


def test_smart_shelves_can_be_marked_on_a_koreader_only_server(world):
    smart = ub.MagicShelf(name="Short reads", user_id=world.alice.id,
                          rules={"condition": "AND", "rules": []})
    world.session.add(smart)
    world.session.commit()
    browser = world.browser("alice")

    marked = browser.post("/api/v1/magicshelf/%d/kobo-sync" % smart.id, json={"kobo_sync": True})
    assert marked.status_code == 200
    world.session.expire_all()
    assert world.session.get(ub.MagicShelf, smart.id).kobo_sync

    world.sync_switch(False)
    refused = browser.post("/api/v1/magicshelf/%d/kobo-sync" % smart.id, json={"kobo_sync": False})
    assert refused.status_code == 403
