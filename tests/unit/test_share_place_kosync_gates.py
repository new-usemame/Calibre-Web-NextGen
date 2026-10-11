# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for "Share my place" PR 2: the KOSync write and serve gates.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 10 (with
section 3 criteria 2-7 and section 8). Read as a spec:

* A kosync request is *identified* when it carries ``X-CWNG-Device-ID``. A
  request without it keeps today's behaviour exactly (shared row, ReadBook,
  Kobo bookmark), with one exception from section 10's last bullet: a
  headerless PUT whose body ``device_id`` resolves to a device the server has
  already seen send the header (``place_control == "per_device"``) gets that
  device's gate. A headerless GET is never identified.
* An identified device with Share my place OFF ("unchecked") writes only its
  own ``DeviceReadingPosition`` and is served only that row; its GET with no
  own row is today's miss. Hardcover still hears about its reading.
* Devices with the box ON ("checked") share one place per book: the latest
  report wins, including a backward move; an older report (its client
  ``timestamp``) arriving later never wins. Without a client timestamp the
  server's receipt time is the report time.
* ``PUT /kosync/syncs/read_status`` from an unchecked device sets only that
  device's journal status, and the library listing serves it back to it.
* The devices JSON says which KOReader devices the checkbox can control.

Everything runs through the real kosync and annotations blueprints over HTTP
with real Basic auth (``LibraryWorld``), so these pin wire behaviour, not
function names.
"""

import inspect
import time
from types import SimpleNamespace

import pytest

from cps import ub
from cps.progress_syncing.models import BookFormatChecksum, KOSyncProgress
from cps.services import device_registry
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit

BOOK = 1
DIGEST = "0123456789abcdef0123456789abcdef"        # the file device A holds
OTHER_DIGEST = "fedcba9876543210fedcba9876543210"  # another file of the same book
XPOINTER = "/body/DocFragment[7]/body/p[3]/text().12"
ADVERTISES = {"position_kinds": "locator,percentage"}


# -- the world ----------------------------------------------------------------

SWITCH = "CWNG_SHARE_PLACE_GATES"


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    """Rollout switch (spec section 12): every test here runs with the gates
    on for everyone unless it says otherwise."""
    monkeypatch.setenv(SWITCH, "1")


@pytest.fixture
def world(monkeypatch, tmp_path):
    built = LibraryWorld(monkeypatch, tmp_path)
    from cps import annotations

    # The devices API is called by path and method, as PR 1's tests do. Flask
    # refuses blueprint registration after the first request, so it is here.
    built.app.register_blueprint(annotations.annotations_bp)
    built.reader = built.add_user("reader")
    built.add_book(BOOK, "First Light")
    built.add_book(2, "Second Book")
    for digest in (DIGEST, OTHER_DIGEST):
        built.session.add(BookFormatChecksum(book=BOOK, format="EPUB", checksum=digest))
    built.session.commit()

    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=built.reader.id))
    monkeypatch.setattr(
        annotations, "_visible_book_scopes_for_owners",
        lambda owners, _session, candidates: {
            int(owner.id): frozenset(candidates.get(int(owner.id), ()))
            for owner in owners if owner is not None
        },
    )
    built.hardcover = []
    monkeypatch.setattr(
        built.kosync, "push_reading_state_to_hardcover",
        lambda user, book, percent: built.hardcover.append(
            (user.id, getattr(book, "id", None), percent)),
    )
    yield built
    built.close()


def _devices_api(world, method, path, json=None):
    from werkzeug.exceptions import HTTPException
    app = world.app
    with app.test_request_context(path, method=method, json=json):
        endpoint, args = app.url_map.bind("localhost").match(path, method=method)
        view = inspect.unwrap(app.view_functions[endpoint])
        try:
            result = view(**args)
        except HTTPException as error:
            return error.code, None
    if isinstance(result, tuple):
        response, status = result[0], result[1]
    else:
        response, status = result, result.status_code
    return status, response.get_json()


def _devices_json(world):
    status, body = _devices_api(world, "GET", "/api/annotations/devices")
    assert status == 200
    return {row["public_id"]: row for row in body["devices"]}


def _share(world, device, on):
    """The owner ticks or unticks Share my place through the devices API."""
    status, _body = _devices_api(
        world, "PATCH", f"/api/annotations/devices/{device.public_id}",
        json={"share_place": on},
    )
    assert status == 200
    world.session.expire_all()


# -- devices ------------------------------------------------------------------

def _register(world, raw_id, name):
    """A KOReader device the account already knows (e.g. from a library sync)."""
    internal = device_registry.register_koreader_device_best_effort(
        user_id=world.reader.id, device_id=raw_id, device_name=name,
        secret_key=world.app.secret_key,
    )
    assert internal is not None
    world.session.expire_all()
    return world.session.get(ub.Device, internal)


def _device_named(world, name):
    world.session.expire_all()
    return (world.session.query(ub.Device)
            .filter_by(user_id=world.reader.id, display_name=name).one())


def _journal(world, device, book_id=BOOK):
    world.session.expire_all()
    return (world.session.query(ub.DeviceReadingPosition)
            .filter_by(device_id=device.id, book_id=book_id).first())


def _device_count(world):
    world.session.expire_all()
    return world.session.query(ub.Device).filter_by(user_id=world.reader.id).count()


# -- the wire -----------------------------------------------------------------

def _headers(raw_id=None, name=None):
    headers = LibraryWorld.basic("reader")
    headers["Accept"] = "application/vnd.koreader.v1+json"
    if raw_id is not None:
        headers["X-CWNG-Device-ID"] = raw_id
        headers["X-CWNG-Device-Name"] = name
    return headers


def _put(world, percent, *, device_id, name, header=True, document=DIGEST,
         progress=XPOINTER, client_at=None):
    """A progress push. ``header`` sends the identity header (the new plugin);
    ``header=False`` is stock KOReader / an old plugin with the same body."""
    body = {"document": document, "progress": progress,
            "percentage": percent / 100.0, "device": name, "device_id": device_id}
    if client_at is not None:
        body["timestamp"] = int(client_at)
    response = world.client.put(
        "/kosync/syncs/progress", json=body,
        headers=_headers(device_id if header else None, name),
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    world.session.expire_all()
    return response.get_json()


def _get(world, *, device_id=None, name=None, document=DIGEST, advertises=True):
    response = world.client.get(
        f"/kosync/syncs/progress/{document}",
        query_string=ADVERTISES if advertises else None,
        headers=_headers(device_id, name),
    )
    return response


def _get_json(world, **kwargs):
    response = _get(world, **kwargs)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def _shared_rows(world):
    world.session.expire_all()
    return (world.session.query(KOSyncProgress)
            .filter_by(user_id=world.reader.id).all())


def _shared_percent(world):
    rows = _shared_rows(world)
    assert len(rows) == 1, [(r.document, r.percentage) for r in rows]
    return rows[0].percentage


def _account_state(world):
    """What the account shows: ReadBook status and the Kobo bookmark."""
    world.session.expire_all()
    row = world.read_row(world.reader, BOOK)
    state = (world.session.query(ub.KoboReadingState)
             .filter_by(user_id=world.reader.id, book_id=BOOK).first())
    bookmark = state.current_bookmark if state is not None else None
    return (
        row.read_status if row is not None else None,
        bookmark.progress_percent if bookmark is not None else None,
        bookmark.last_modified if bookmark is not None else None,
    )


def _later():
    """Receipt times are the report times when no client clock is sent; keep
    two receipts apart even on a store with one-second resolution."""
    time.sleep(1.1)


def _stock_reader_sets_shared(world, percent):
    """A stock KOReader (no header, no body device_id) moves the shared place."""
    body = {"document": DIGEST, "progress": "/body/DocFragment[2]/body/p[1]/text().0",
            "percentage": percent / 100.0, "device": "Stock KOReader"}
    response = world.client.put("/kosync/syncs/progress", json=body, headers=_headers())
    assert response.status_code == 200
    world.session.expire_all()


# == 1. Unidentified requests keep today's behaviour ==========================

def test_headerless_push_from_an_always_shared_device_is_todays_shared_path(world):
    """Stock KOReader sends body device_id but never the header. Its Device
    row exists (registered from the body, Share my place off by default), but
    it was never seen with the header, so the box cannot control it."""
    _put(world, 40, device_id="stock-a", name="Stock A", header=False)
    device = _device_named(world, "Stock A")
    assert device.share_place is False

    _put(world, 60, device_id="stock-a", name="Stock A", header=False)

    assert _shared_percent(world) == pytest.approx(60.0)
    status, bookmark, _ = _account_state(world)
    assert status == ub.ReadBook.STATUS_IN_PROGRESS
    assert bookmark == pytest.approx(60.0)
    # A second stock device sees A's place.
    pulled = _get_json(world)
    assert pulled["percentage"] == pytest.approx(0.60)
    assert pulled["device_id"] == "stock-a"


# == (a) The built-in Progress sync beside cwngsync ===========================

def test_headerless_push_from_a_per_device_unchecked_device_takes_its_gate(world):
    """KOReader's built-in kosync on a cwngsync device: headerless, same body
    device_id as the header cwngsync sends. Same physical device, same gate."""
    _stock_reader_sets_shared(world, 25)
    before = _account_state(world)
    _put(world, 40, device_id="kindle-a", name="Kindle A")  # header: per_device now
    device = _device_named(world, "Kindle A")
    assert device.share_place is False

    _put(world, 60, device_id="kindle-a", name="Kindle A", header=False)

    assert _shared_percent(world) == pytest.approx(25.0)
    assert _account_state(world) == before
    assert _journal(world, device).progress_percent == pytest.approx(60.0)
    own = _get_json(world, device_id="kindle-a", name="Kindle A")
    assert own["percentage"] == pytest.approx(0.60)


def test_headerless_get_is_never_identified_even_for_a_per_device_device(world):
    _stock_reader_sets_shared(world, 25)
    _put(world, 60, device_id="kindle-a", name="Kindle A")  # private, per_device

    unidentified = _get_json(world)
    identified = _get_json(world, device_id="kindle-a", name="Kindle A")

    assert unidentified["percentage"] == pytest.approx(0.25)
    assert identified["percentage"] == pytest.approx(0.60)


# == 2. Identified unchecked device ===========================================

def test_unchecked_push_changes_nothing_shared(world):
    _stock_reader_sets_shared(world, 70)
    before = _account_state(world)
    assert before[0] == ub.ReadBook.STATUS_IN_PROGRESS

    _put(world, 90, device_id="kindle-a", name="Kindle A")

    assert _shared_percent(world) == pytest.approx(70.0)
    assert _account_state(world) == before
    device = _device_named(world, "Kindle A")
    assert _journal(world, device).progress_percent == pytest.approx(90.0)
    # Neither an unidentified reader nor a checked device sees it.
    assert _get_json(world)["percentage"] == pytest.approx(0.70)
    checked = _register(world, "kindle-b", "Kindle B")
    _share(world, checked, True)
    assert _get_json(world, device_id="kindle-b", name="Kindle B")["percentage"] == \
        pytest.approx(0.70)


def test_unchecked_push_of_an_unread_book_creates_no_account_state(world):
    _put(world, 30, device_id="kindle-a", name="Kindle A")

    assert _shared_rows(world) == []
    assert _account_state(world) == (None, None, None)


def test_unchecked_device_is_served_its_own_place_echoing_its_raw_id(world):
    _stock_reader_sets_shared(world, 70)
    _put(world, 20, device_id="kindle-a", name="Kindle A")  # deliberately behind

    own = _get_json(world, device_id="kindle-a", name="Kindle A")

    assert own["document"] == DIGEST
    assert own["progress"] == XPOINTER
    assert own["position_kind"] == "locator"
    assert own["percentage"] == pytest.approx(0.20)
    assert own["device_id"] == "kindle-a"
    assert isinstance(own["timestamp"], int)


def test_unchecked_place_is_a_locator_only_in_the_file_it_was_reported_in(world):
    _put(world, 20, device_id="kindle-a", name="Kindle A", document=DIGEST)

    other_file = _get_json(world, device_id="kindle-a", name="Kindle A",
                           document=OTHER_DIGEST)

    assert other_file["progress"] is None
    assert other_file["position_kind"] == "percentage"
    assert other_file["percentage"] == pytest.approx(0.20)
    assert other_file["device_id"] == "kindle-a"


def test_unchecked_push_still_reaches_hardcover_as_the_accounts_reading(world):
    _put(world, 80, device_id="kindle-a", name="Kindle A")

    assert world.hardcover == [(world.reader.id, BOOK, 80)]


# == 3. Unchecked device with no own row ======================================

def test_unchecked_device_with_no_own_place_gets_todays_miss(world):
    todays_miss = _get(world)  # nothing stored anywhere yet
    assert todays_miss.status_code == 200
    _register(world, "kindle-a", "Kindle A")
    _stock_reader_sets_shared(world, 70)

    response = _get(world, device_id="kindle-a", name="Kindle A")

    assert response.status_code == todays_miss.status_code
    assert response.get_json() == todays_miss.get_json()


# == 4. Identified checked device: today's path plus its journal row ==========

def test_checked_push_is_shared_and_journalled(world):
    device = _register(world, "kindle-a", "Kindle A")
    _share(world, device, True)

    _put(world, 80, device_id="kindle-a", name="Kindle A")

    assert _shared_percent(world) == pytest.approx(80.0)
    status, bookmark, _ = _account_state(world)
    assert status == ub.ReadBook.STATUS_IN_PROGRESS
    assert bookmark == pytest.approx(80.0)
    assert _journal(world, device).progress_percent == pytest.approx(80.0)
    assert _get_json(world)["percentage"] == pytest.approx(0.80)
    assert world.hardcover == [(world.reader.id, BOOK, 80)]


# == 5. Two checked devices: latest wins, stale echo loses ====================

@pytest.fixture
def two_checked(world):
    a = _register(world, "kindle-a", "Kindle A")
    b = _register(world, "kindle-b", "Kindle B")
    _share(world, a, True)
    _share(world, b, True)
    return a, b


def test_checked_backward_move_reaches_the_other_checked_device(world, two_checked):
    _put(world, 70, device_id="kindle-a", name="Kindle A")
    _later()
    _put(world, 30, device_id="kindle-b", name="Kindle B")  # went back a chapter

    served = _get_json(world, device_id="kindle-a", name="Kindle A")

    assert served["percentage"] == pytest.approx(0.30)
    assert served["device_id"] == "kindle-b"
    assert _get_json(world)["percentage"] == pytest.approx(0.30)


def test_checked_stale_echo_does_not_overwrite_a_newer_place(world, two_checked):
    now = time.time()
    _put(world, 70, device_id="kindle-a", name="Kindle A", client_at=now - 300)
    _put(world, 30, device_id="kindle-b", name="Kindle B", client_at=now - 200)
    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.30)

    # A re-sends its older report after B's newer one was received.
    _put(world, 70, device_id="kindle-a", name="Kindle A", client_at=now - 300)

    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.30)
    assert _get_json(world)["percentage"] == pytest.approx(0.30)


# == 6. Same unchecked device: an older report arriving later loses ============

def test_unchecked_older_report_arriving_later_keeps_the_newer_own_place(world):
    now = time.time()
    _put(world, 50, device_id="kindle-a", name="Kindle A", client_at=now - 60)

    _put(world, 20, device_id="kindle-a", name="Kindle A", client_at=now - 300)

    device = _device_named(world, "Kindle A")
    assert _journal(world, device).progress_percent == pytest.approx(50.0)
    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.50)


def test_unchecked_device_may_move_its_own_place_backward(world):
    _put(world, 50, device_id="kindle-a", name="Kindle A")
    _later()
    _put(world, 20, device_id="kindle-a", name="Kindle A")

    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.20)


# == 7. GET never creates a Device ============================================

def test_identified_get_from_an_unknown_device_is_todays_path_and_creates_nothing(world):
    _stock_reader_sets_shared(world, 70)
    devices_before = _device_count(world)

    served = _get_json(world, device_id="never-seen", name="New Kindle")

    assert served["percentage"] == pytest.approx(0.70)
    assert _device_count(world) == devices_before


# == 9. Read status ===========================================================

def _status(world, status, *, device_id, name):
    response = world.client.put(
        "/kosync/syncs/read_status",
        json={"book_id": BOOK, "status": status, "device": name, "device_id": device_id},
        headers=_headers(device_id, name),
    )
    world.session.expire_all()
    return response


def _library_status(world, *, device_id, name):
    response = world.client.get("/kosync/syncs/library", headers=_headers(device_id, name))
    assert response.status_code == 200
    return {book["book_id"]: book for book in response.get_json()["books"]}[BOOK]["read_status"]


def test_unchecked_status_stays_on_the_device_and_is_served_back_to_it(world):
    world.position(world.reader, BOOK, 55.0)  # the account is reading it
    device = _register(world, "kindle-a", "Kindle A")
    other = _register(world, "kindle-b", "Kindle B")
    _share(world, other, True)

    assert _status(world, "finished", device_id="kindle-a", name="Kindle A").status_code == 200

    assert world.read_row(world.reader, BOOK).read_status == ub.ReadBook.STATUS_IN_PROGRESS
    own = _journal(world, device)
    assert own is not None
    assert own.reading_status == ub.ReadBook.STATUS_FINISHED
    assert own.status_modified_at is not None
    assert _library_status(world, device_id="kindle-a", name="Kindle A") == "finished"
    assert _library_status(world, device_id="kindle-b", name="Kindle B") == "reading"


def test_checked_status_is_the_accounts_status(world):
    world.position(world.reader, BOOK, 55.0)
    device = _register(world, "kindle-a", "Kindle A")
    _share(world, device, True)

    assert _status(world, "finished", device_id="kindle-a", name="Kindle A").status_code == 200

    assert world.read_row(world.reader, BOOK).read_status == ub.ReadBook.STATUS_FINISHED


# == 10. Turning the box on ===================================================

def test_turning_share_on_serves_the_newer_shared_place_on_the_next_get(world):
    _put(world, 20, device_id="kindle-a", name="Kindle A")
    _later()
    _stock_reader_sets_shared(world, 70)
    device = _device_named(world, "Kindle A")
    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.20)

    _share(world, device, True)

    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.70)


# == 11. Devices JSON place_control ===========================================

def test_devices_json_says_which_devices_the_checkbox_controls(world):
    from cps.services.device_registry import upsert_webreader_device
    kobo = ub.Device(user_id=world.reader.id, kind="kobo", display_name="Kobo Clara",
                     active=True, created_by="auto")
    world.session.add(kobo)
    browser = upsert_webreader_device(world.session, user_id=world.reader.id)
    world.session.commit()
    _put(world, 10, device_id="kindle-a", name="Kindle A")                 # header seen
    _put(world, 10, device_id="stock-b", name="Stock B", header=False)     # never
    kindle = _device_named(world, "Kindle A")
    stock = _device_named(world, "Stock B")

    rows = _devices_json(world)

    assert rows[kobo.public_id]["place_control"] == "per_device"
    assert rows[browser.public_id]["place_control"] == "per_device"
    assert rows[kindle.public_id]["place_control"] == "per_device"
    assert rows[stock.public_id]["place_control"] == "always_shared"


def test_a_header_once_seen_makes_a_stock_registered_device_per_device(world):
    _put(world, 10, device_id="kindle-a", name="Kindle A", header=False)
    kindle = _device_named(world, "Kindle A")
    assert _devices_json(world)[kindle.public_id]["place_control"] == "always_shared"

    _put(world, 20, device_id="kindle-a", name="Kindle A")

    assert _devices_json(world)[kindle.public_id]["place_control"] == "per_device"


# == 5b. Shared-row arbitration splits by who writes (coordinator, 2026-10-11) ==
# An identified checked device's push is judged latest-wins against the shared
# row; an unidentified push keeps today's rule exactly (furthest wins, with
# same-device rewind), so a stock KOReader opening an old copy at 1% cannot pull
# every checked device back.

@pytest.fixture
def checked_a(world):
    device = _register(world, "kindle-a", "Kindle A")
    _share(world, device, True)
    return device


def test_unidentified_backward_push_does_not_displace_a_checked_place(world, checked_a):
    _put(world, 70, device_id="kindle-a", name="Kindle A")
    _later()

    _stock_reader_sets_shared(world, 30)

    assert _shared_percent(world) == pytest.approx(70.0)
    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.70)
    assert _get_json(world)["percentage"] == pytest.approx(0.70)


def test_unidentified_forward_push_still_moves_the_shared_place(world, checked_a):
    _put(world, 70, device_id="kindle-a", name="Kindle A")
    _later()

    _stock_reader_sets_shared(world, 90)

    assert _shared_percent(world) == pytest.approx(90.0)
    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.90)


def test_checked_backward_push_displaces_an_unidentified_place(world, checked_a):
    _stock_reader_sets_shared(world, 70)
    _later()

    _put(world, 30, device_id="kindle-a", name="Kindle A")

    assert _shared_percent(world) == pytest.approx(30.0)
    assert _get_json(world)["percentage"] == pytest.approx(0.30)
    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.30)


def test_checked_stale_echo_does_not_displace_a_newer_unidentified_place(world, checked_a):
    now = time.time()
    _stock_reader_sets_shared(world, 40)  # received now, no client clock

    # Further along, but reported before the unidentified place was received.
    _put(world, 60, device_id="kindle-a", name="Kindle A", client_at=now - 300)

    assert _shared_percent(world) == pytest.approx(40.0)
    assert _get_json(world)["percentage"] == pytest.approx(0.40)


# == 10b. A checked device's GET: its own row or the shared row, whichever is newer

def test_turning_share_on_keeps_an_own_place_newer_than_the_shared_one(world):
    _stock_reader_sets_shared(world, 70)
    _later()
    _put(world, 20, device_id="kindle-a", name="Kindle A")  # private, newer
    device = _device_named(world, "Kindle A")

    _share(world, device, True)

    served = _get_json(world, device_id="kindle-a", name="Kindle A")
    assert served["percentage"] == pytest.approx(0.20)
    assert served["device_id"] == "kindle-a"


# == 12. Rollout switch: gates enforce only when the server switch says so =====
# CWNG_SHARE_PLACE_GATES is read on every request: "1" is everyone,
# "user:3,107" is only those account ids, anything else (or unset) is off.

def _assert_todays_behaviour_for_an_identified_unchecked_device(world):
    _stock_reader_sets_shared(world, 70)

    _put(world, 90, device_id="kindle-a", name="Kindle A")
    assert _shared_percent(world) == pytest.approx(90.0)
    _status_now, bookmark, _ = _account_state(world)
    assert bookmark == pytest.approx(90.0)

    # A second unchecked device going back is not served its own place.
    _put(world, 10, device_id="kindle-b", name="Kindle B")
    assert _shared_percent(world) == pytest.approx(90.0)
    assert _get_json(world, device_id="kindle-b", name="Kindle B")["percentage"] == \
        pytest.approx(0.90)


def _assert_gated_for_an_identified_unchecked_device(world):
    _stock_reader_sets_shared(world, 70)

    _put(world, 90, device_id="kindle-a", name="Kindle A")

    assert _shared_percent(world) == pytest.approx(70.0)
    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.90)


def test_switch_off_by_default_keeps_todays_behaviour(world, monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    _assert_todays_behaviour_for_an_identified_unchecked_device(world)


@pytest.mark.parametrize("value", ["0", "", "yes", "true", "user:", "users:1"])
def test_switch_values_other_than_on_or_an_allow_list_are_off(world, monkeypatch, value):
    monkeypatch.setenv(SWITCH, value)
    _assert_todays_behaviour_for_an_identified_unchecked_device(world)


def test_allow_list_without_this_account_keeps_todays_behaviour(world, monkeypatch):
    monkeypatch.setenv(SWITCH, f"user:{world.reader.id + 100},{world.reader.id + 101}")
    _assert_todays_behaviour_for_an_identified_unchecked_device(world)


def test_allow_list_with_this_account_is_gated(world, monkeypatch):
    monkeypatch.setenv(SWITCH, f"user:{world.reader.id + 100},{world.reader.id}")
    _assert_gated_for_an_identified_unchecked_device(world)


def test_switch_is_read_on_every_request(world, monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    _stock_reader_sets_shared(world, 70)
    _put(world, 20, device_id="kindle-a", name="Kindle A")
    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.70)

    monkeypatch.setenv(SWITCH, "1")

    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.20)


def _auth(world):
    response = world.client.get("/kosync/users/auth", headers=_headers())
    assert response.status_code == 200
    return response.get_json()


@pytest.mark.parametrize("value, expected", [
    (None, False),
    ("0", False),
    ("1", True),
    ("user:{other}", False),
    ("user:{other},{me}", True),
])
def test_auth_reply_tells_the_plugin_whether_gates_are_enforced(
        world, monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(SWITCH, raising=False)
    else:
        monkeypatch.setenv(SWITCH, value.format(other=world.reader.id + 100,
                                                me=world.reader.id))

    body = _auth(world)

    assert body["authorized"] == "OK"
    assert body["share_place_gates"] is expected


# == 12. Client timestamp validation on PUT ===================================
# Only a finite JSON number is a client clock. Anything else (missing, null,
# a string, a bool, NaN, inf), and any value <= 0 or before 2000-01-01, is
# treated as missing: the report time is the server's receipt. Never a 400.
# A far-future clock is left to the arbiter's clamp.

Y2000 = 946684800  # 2000-01-01T00:00:00Z


def _put_raw_timestamp(world, percent, *, device_id, name, timestamp_json):
    """A push whose ``timestamp`` is written verbatim into the JSON text, so
    NaN / Infinity / strings / booleans reach the server as sent."""
    body = ('{"document": "%s", "progress": "%s", "percentage": %r, "device": "%s", '
            '"device_id": "%s", "timestamp": %s}'
            % (DIGEST, XPOINTER, percent / 100.0, name, device_id, timestamp_json))
    response = world.client.put(
        "/kosync/syncs/progress", data=body, content_type="application/json",
        headers=_headers(device_id, name),
    )
    world.session.expire_all()
    return response


def _junk_values(now):
    return [
        "null", "true", "false",
        '"%d"' % int(now + 180),   # a number in a string is not a number
        '"%d"' % int(now - 3600),
        '"soon"',
        "NaN", "Infinity", "-Infinity",
        "0", "-5", str(Y2000 - 1), "%r" % (Y2000 - 0.5),
    ]


@pytest.mark.parametrize("index", range(13))
def test_a_junk_client_clock_is_receipt_time_on_both_sides(world, two_checked, index):
    now = time.time()
    junk = _junk_values(now)[index]
    _put(world, 70, device_id="kindle-a", name="Kindle A", client_at=now - 120)

    # Read as receipt time, B's report is newer than A's and wins; read as an
    # ancient clock it would lose.
    response = _put_raw_timestamp(world, 30, device_id="kindle-b", name="Kindle B",
                                  timestamp_json=junk)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.30)

    # Read as receipt time, a later genuine clock beats it; read as a
    # near-future clock it would still hold.
    _put(world, 50, device_id="kindle-a", name="Kindle A", client_at=time.time() + 60)
    assert _get_json(world, device_id="kindle-a", name="Kindle A")["percentage"] == \
        pytest.approx(0.50)


def test_a_junk_clock_cannot_win_a_stale_echo(world, two_checked):
    now = time.time()
    _put(world, 30, device_id="kindle-b", name="Kindle B", client_at=now + 120)

    # A's echo carries its old place with a clock it sent as a string.
    response = _put_raw_timestamp(world, 70, device_id="kindle-a", name="Kindle A",
                                  timestamp_json='"%d"' % int(now + 240))

    assert response.status_code == 200
    assert _get_json(world)["percentage"] == pytest.approx(0.30)


@pytest.mark.parametrize("clock", [
    lambda now: Y2000,                 # the first instant that counts
    lambda now: float(now - 200.5),    # a float is a JSON number too
])
def test_a_finite_number_from_2000_on_is_the_client_clock(world, two_checked, clock):
    now = time.time()
    _put(world, 70, device_id="kindle-a", name="Kindle A", client_at=now - 120)

    response = _put_raw_timestamp(world, 30, device_id="kindle-b", name="Kindle B",
                                  timestamp_json=repr(clock(now)))

    assert response.status_code == 200
    # Older than A's report by its own clock, so it loses.
    assert _get_json(world)["percentage"] == pytest.approx(0.70)


def test_a_far_future_clock_is_clamped_by_the_arbiter(world, two_checked):
    now = time.time()
    response = _put_raw_timestamp(world, 70, device_id="kindle-a", name="Kindle A",
                                  timestamp_json=str(int(now + 86400 * 365)))
    assert response.status_code == 200
    _later()

    _put(world, 30, device_id="kindle-b", name="Kindle B")

    assert _get_json(world)["percentage"] == pytest.approx(0.30)


# == CWNG's stale-echo shape: stock push at T, checked echo at T-10m ===========

def test_checked_echo_ten_minutes_old_loses_to_a_stock_push(world, checked_a):
    _stock_reader_sets_shared(world, 40)  # received at T
    t = time.time()

    _put(world, 80, device_id="kindle-a", name="Kindle A", client_at=t - 600)

    assert _shared_percent(world) == pytest.approx(40.0)
    assert _get_json(world)["percentage"] == pytest.approx(0.40)
