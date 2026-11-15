# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-device book scope (PR A): the Devices page's "Books on this device".

Spec: state/kindle-kids/specs/2026-10-12-per-device-scope.md, "UI: Devices
page" and the UI line of "Tests": the control saves and reloads, offers only
the user's own shelves, and is translated (fr and nl ship with it). Contract
for the JSON the page reads and sends: CONTRACT.md at the root of the PR A
branch.

The devices API is driven over HTTP as a signed-in owner, the same calls the
SPA makes; translations are read through the SPA's own catalog endpoint.
"""

import inspect
import json

import flask
import pytest

from cps import ub
from tests.unit.device_scope_world import DEVICES, ScopeWorld

pytestmark = pytest.mark.unit

# What the control says (spec wording), plus the two fallback notices
# (CONTRACT.md). Each ships translated in fr and nl.
CONTROL_STRINGS = [
    "Books on this device",
    "Same as my account setting",
    "Everything in my library",
    "Only these shelves",
    "Choose which books this device keeps. Books you take off are removed from "
    "the device at its next sync; your place, highlights and notes stay saved.",
    "The shelf chosen for this device was deleted. It uses your account setting "
    "until you choose again.",
    "The shelf chosen for this device is no longer available. It uses your "
    "account setting until you choose again.",
]


@pytest.fixture
def world(monkeypatch, tmp_path):
    built = ScopeWorld(monkeypatch, tmp_path)
    yield built
    built.close()


def _account(world, *, shelf_only=True, my_library=False):
    a = world.add_user("user-a", shelf_only=shelf_only, my_library=my_library)
    b = world.add_user("user-b")
    world.add_book(1, "One")
    world.add_book(2, "Two")
    world.a, world.b = a, b
    world.to_read = world.shelf(a, "To Read", [1], kobo_sync=True)
    world.kindle = world.shelf(a, "Kindle", [2])
    world.theirs = world.shelf(b, "Theirs", [2], kobo_sync=True)
    world.theirs.is_public = 1
    world.session.commit()
    world.koreader_device = world.koreader(a, "kindle-a")
    world.kobo_device = world.kobo(a)
    return a


def _scope(world, device, user=None):
    return world.device_rows(user or world.a)[device.public_id]["book_scope"]


@pytest.mark.parametrize("kind", ["koreader", "kobo"])
def test_a_device_starts_on_the_account_setting_and_says_what_that_is(world, kind):
    _account(world)
    device = getattr(world, kind + "_device")

    scope = _scope(world, device)

    assert scope["mode"] == "unset"
    assert scope["account_mode"] == "shelves"
    assert scope["effective_mode"] == "shelves"
    assert scope["fallback"] is None


@pytest.mark.parametrize("shelf_only,my_library,expected", [
    (True, False, "shelves"),
    (False, False, "library"),
    (False, True, "my_library"),
], ids=["e-reader-shelves", "whole-library", "my-library"])
def test_the_account_setting_is_named_for_each_kind_of_account(
        world, shelf_only, my_library, expected):
    _account(world, shelf_only=shelf_only, my_library=my_library)

    scope = _scope(world, world.koreader_device)

    assert scope["account_mode"] == expected
    assert scope["choices"]["my_library"] is my_library


def test_the_web_reader_has_no_such_control(world):
    a = _account(world)
    rows = world.device_rows(a)
    browsers = [row for row in rows.values() if row["kind"] == "webreader"]
    assert browsers, "the account's web reader entry is listed"

    assert all(row.get("book_scope") is None for row in browsers)
    response = world.web(a).patch("%s/%s" % (DEVICES, browsers[0]["public_id"]),
                                  json={"book_scope": {"mode": "library"}})
    assert response.status_code == 400


@pytest.mark.parametrize("kind", ["koreader", "kobo"])
def test_the_choice_saves_and_reads_back(world, kind):
    _account(world)
    device = getattr(world, kind + "_device")

    saved = world.set_scope(world.a, device, "shelves", [world.kindle])
    assert saved.status_code == 200, saved.get_data(as_text=True)
    assert saved.get_json()["book_scope"]["mode"] == "shelves"
    assert saved.get_json()["book_scope"]["shelf_ids"] == [world.kindle.id]

    # A fresh page load (a new browser session) shows the same choice.
    reloaded = world.browser("user-a").get(DEVICES).get_json()["devices"]
    scope = {row["public_id"]: row for row in reloaded}[device.public_id]["book_scope"]
    assert scope["mode"] == "shelves"
    assert scope["shelf_ids"] == [world.kindle.id]
    assert scope["effective_mode"] == "shelves"

    assert world.set_scope(world.a, device, "library").status_code == 200
    assert _scope(world, device)["mode"] == "library"
    assert world.set_scope(world.a, device, "unset").status_code == 200
    assert _scope(world, device)["mode"] == "unset"


def test_saving_one_device_leaves_the_other_devices_alone(world):
    _account(world)

    assert world.set_scope(world.a, world.koreader_device, "shelves",
                           [world.kindle]).status_code == 200

    assert _scope(world, world.kobo_device)["mode"] == "unset"
    row = world.device_rows(world.a)[world.koreader_device.public_id]
    assert row["share_place"] is False
    assert row["label"] == "Kindle"


def test_only_the_users_own_shelves_are_offered(world):
    _account(world)

    choices = _scope(world, world.koreader_device)["choices"]

    assert {(s["id"], s["name"]) for s in choices["shelves"]} == {
        (world.to_read.id, "To Read"), (world.kindle.id, "Kindle")}


def test_another_users_shelf_cannot_be_chosen(world):
    _account(world)

    response = world.set_scope(world.a, world.koreader_device, "shelves",
                               [world.kindle, world.theirs])

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_book_scope"
    assert _scope(world, world.koreader_device)["mode"] == "unset"


@pytest.mark.parametrize("body", [
    {"mode": "everything"},
    {"mode": "shelves", "shelf_ids": []},
    {"mode": "shelves", "shelf_ids": ["1"]},
    {"mode": "shelves", "shelf_ids": [999999]},
    {"mode": "my_library"},   # My Library is off for this account
    "shelves",
], ids=["unknown-mode", "no-shelves", "string-id", "missing-shelf",
        "my-library-off", "not-an-object"])
def test_a_choice_the_page_could_not_offer_is_refused_and_changes_nothing(world, body):
    a = _account(world)
    assert world.set_scope(a, world.koreader_device, "shelves", [world.kindle]).status_code == 200

    response = world.web(a).patch(
        "%s/%s" % (DEVICES, world.koreader_device.public_id), json={"book_scope": body})

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_book_scope"
    scope = _scope(world, world.koreader_device)
    assert (scope["mode"], scope["shelf_ids"]) == ("shelves", [world.kindle.id])


def test_another_users_device_cannot_be_changed(world):
    _account(world)

    response = world.web(world.b).patch(
        "%s/%s" % (DEVICES, world.koreader_device.public_id),
        json={"book_scope": {"mode": "library"}})

    assert response.status_code == 404
    assert _scope(world, world.koreader_device)["mode"] == "unset"


def test_my_library_can_be_chosen_when_my_library_is_on(world):
    _account(world, shelf_only=True, my_library=True)

    response = world.set_scope(world.a, world.koreader_device, "my_library")

    assert response.status_code == 200, response.get_data(as_text=True)
    assert _scope(world, world.koreader_device)["mode"] == "my_library"


# -- translations ---------------------------------------------------------------

def _catalog(locale):
    from cps.api import i18n as i18n_mod
    i18n_mod._catalog_cache.clear()
    i18n_mod._available_locales = None
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/i18n/%s.json" % locale):
        result = inspect.unwrap(i18n_mod.i18n_catalog)(locale)
        return json.loads(result.get_data(as_text=True))["catalog"]


@pytest.mark.parametrize("locale", ["fr", "nl"])
def test_the_control_ships_translated(locale):
    catalog = _catalog(locale)

    missing = [msgid for msgid in CONTROL_STRINGS
               if not catalog.get(msgid) or catalog[msgid] == msgid]

    assert missing == []
