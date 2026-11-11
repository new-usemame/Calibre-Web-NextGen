# SPDX-License-Identifier: GPL-3.0-or-later
"""#2841: a client can tell a private place from no place at all.

With Share my place gates on, a device that is not sharing keeps its place to
itself. Before this, any other reader asking for that book got ``{}``, the same
body as a book nobody had opened, so a client could not explain why a place
just set on another device was not arriving.

* An otherwise-empty GET carries ``places_not_shared``: how many of this
  account's other devices hold a place for the book without sharing it. A
  count only, never a position, percentage or device name.
* ``/kosync/users/auth`` sent with ``X-CWNG-Device-ID`` says whether that
  device shares its place (``share_place``), so a client can show the state
  without a book.

Both are additive: a stock plugin reads neither and still sees "no progress".
"""

import pytest

from cps import ub
from cps.services import device_registry
from tests.unit.test_share_place_kosync_gates import (
    BOOK, _get_json, _headers, _put, _register, _share,
    world,  # noqa: F401 - the shared HTTP world
)

pytestmark = pytest.mark.unit

HINT = "places_not_shared"


@pytest.fixture(autouse=True)
def enforce_share_place(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")


def test_a_private_place_is_named_by_count_to_a_reader_without_one(world):
    # The issue's repro: a header-identified device reports a place while not
    # sharing, then another reader with no device header asks.
    _put(world, 55, device_id="dev-k", name="KOReader")

    assert _get_json(world) == {HINT: 1}


def test_the_hint_counts_devices_and_carries_nothing_else(world):
    _put(world, 55, device_id="dev-k", name="KOReader")
    _put(world, 30, device_id="dev-l", name="Second Kindle")

    body = _get_json(world)

    # Exactly the count: no place, no percentage, no device name or id.
    assert body == {HINT: 2}


def test_a_private_device_asking_is_not_counted_against_itself(world):
    asker = _register(world, "asker", "Asker")
    _share(world, asker, False)
    _put(world, 55, device_id="dev-k", name="KOReader")

    assert _get_json(world, device_id="asker", name="Asker") == {HINT: 1}


def test_a_shared_place_is_served_without_the_hint(world):
    sharing = _register(world, "dev-s", "Sharing Kindle")
    _share(world, sharing, True)
    _put(world, 40, device_id="dev-s", name="Sharing Kindle")
    _put(world, 55, device_id="dev-k", name="KOReader")  # private, ahead

    body = _get_json(world)

    assert body["percentage"] == pytest.approx(0.40)
    assert HINT not in body


def test_no_place_anywhere_stays_the_bare_miss(world):
    _register(world, "dev-k", "KOReader")

    assert _get_json(world) == {}


def test_another_accounts_private_place_is_never_counted(world):
    other = world.add_user("neighbour")
    internal = device_registry.register_koreader_device_best_effort(
        user_id=other.id, device_id="their-kindle", device_name="Their Kindle",
        secret_key=world.app.secret_key,
    )
    world.session.add(ub.DeviceReadingPosition(
        device_id=internal, book_id=BOOK, progress_percent=70.0))
    world.session.commit()

    assert _get_json(world) == {}


def test_with_the_gates_off_nothing_is_private_so_nothing_is_hinted(world, monkeypatch):
    _put(world, 55, device_id="dev-k", name="KOReader")
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "0")

    assert HINT not in _get_json(world)


def _auth(world, raw_id=None, name=None):
    response = world.client.get("/kosync/users/auth", headers=_headers(raw_id, name))
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def test_auth_reports_the_identified_devices_share_state(world):
    device = _register(world, "dev-k", "KOReader")

    assert _auth(world, "dev-k", "KOReader")["share_place"] is False
    _share(world, device, True)
    assert _auth(world, "dev-k", "KOReader")["share_place"] is True


def test_auth_without_a_known_device_says_nothing_about_sharing(world):
    _register(world, "dev-k", "KOReader")

    assert "share_place" not in _auth(world)
    assert "share_place" not in _auth(world, "never-seen", "New Kindle")
    # The existing fields are unchanged.
    assert _auth(world)["authorized"] == "OK"


def _status_only(world, raw_id, name):
    """A private device that set a read status but never reported a place."""
    response = world.client.put(
        "/kosync/syncs/read_status",
        json={"book_id": BOOK, "status": "reading", "device": name, "device_id": raw_id},
        headers=_headers(raw_id, name),
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    world.session.expire_all()


def test_a_device_with_a_status_but_no_place_is_not_a_place(world):
    _status_only(world, "dev-s", "Status Only")

    assert HINT not in _get_json(world)


def test_the_asker_is_not_counted_even_when_it_has_only_a_status(world):
    _status_only(world, "asker", "Asker")
    _put(world, 55, device_id="dev-k", name="KOReader")

    assert _get_json(world, device_id="asker", name="Asker") == {HINT: 1}
