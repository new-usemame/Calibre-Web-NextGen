# SPDX-License-Identifier: GPL-3.0-or-later
"""Protocol seams beyond the externally committed PR 2 acceptance tests."""

import time

import pytest

from cps import ub
from tests.unit.test_share_place_kosync_gates import (
    BOOK, OTHER_DIGEST, _account_state, _device_named, _get, _headers,
    _journal, _put, _register, _share, _status, _stock_reader_sets_shared,
    world,  # noqa: F401 - the HTTP world is shared, acceptance code stays intact
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def enforce_share_place(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")


@pytest.mark.parametrize("private", [False, True])
def test_gated_commit_failure_returns_sync_error_and_recovers_session(world, monkeypatch, private):
    """A failed shared/private save rolls back and allows the next HTTP push."""
    device = _register(world, "kindle-a", "Kindle A")
    _share(world, device, not private)
    _put(world, 30, device_id="kindle-a", name="Kindle A")
    before = _account_state(world)
    commit = world.session.commit
    failed = False

    def fail_once():
        nonlocal failed
        if not failed:
            failed = True
            # The real UNIQUE violation puts the session into failed-flush
            # state; raising a mocked exception would not test recovery.
            world.session.add(ub.User(name="reader"))
        commit()

    with monkeypatch.context() as patch:
        patch.setattr(world.session, "commit", fail_once)
        response = world.client.put("/kosync/syncs/progress", json={
            "document": OTHER_DIGEST, "progress": "12", "percentage": .60,
            "device": "Kindle A", "device_id": "kindle-a",
        }, headers=_headers("kindle-a", "Kindle A"))

    assert failed, "the gated save never reached the failing commit"
    assert response.status_code == 400
    assert response.get_json() == {
        "error": world.kosync.ERROR_INTERNAL, "message": "Failed to save sync progress",
    }
    # Queries must work without a test-side rollback, and the failed report
    # must not replace either the private journal or the account carrier.
    assert world.session.query(ub.User).filter_by(name="reader").count() == 1
    assert _journal(world, device).progress_percent == 30
    assert _account_state(world) == before
    _put(world, 45, device_id="kindle-a", name="Kindle A")
    assert _get(world, device_id="kindle-a", name="Kindle A").get_json()["percentage"] == pytest.approx(.45)


@pytest.mark.parametrize("fast_by", [180, 86400])
def test_checked_fast_clock_does_not_poison_a_stock_push(world, fast_by):
    device = _register(world, "kindle-a", "Kindle A")
    _share(world, device, True)
    _put(world, 30, device_id="kindle-a", name="Kindle A", client_at=time.time() + fast_by)
    before = time.time()
    _put(world, 70, device_id="stock", name="Stock reader", header=False)
    after = time.time()
    served = _get(world).get_json()
    assert served["percentage"] == pytest.approx(.70)
    assert int(before) <= served["timestamp"] <= int(after)
    # A later checked report must judge the stock write at its receipt clock.
    _put(world, 20, device_id="kindle-a", name="Kindle A", client_at=time.time() + 60)
    assert _get(world).get_json()["percentage"] == pytest.approx(.20)


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("offset", [-300, 180, 86400])
def test_progress_timestamps_remain_receipt_time(world, private, offset):
    device = _register(world, "kindle-a", "Kindle A")
    _share(world, device, not private)
    before = time.time()
    pushed = _put(world, 30, device_id="kindle-a", name="Kindle A", client_at=before + offset)
    after = time.time()
    pulled = _get(world, device_id="kindle-a", name="Kindle A").get_json()
    # The plugin compares this wire field with last_page_turn_timestamp.
    for reply in (pushed, pulled):
        assert int(before) <= reply["timestamp"] <= int(after)


def test_dormant_library_and_status_follow_the_account(world, monkeypatch):
    world.position(world.reader, BOOK, 80)
    _put(world, 20, device_id="kindle-a", name="Kindle A")
    assert _listing(world)["books"][0]["progress"] == pytest.approx(.20)
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "0")
    shared = _listing(world)["books"][0]
    assert shared["progress"] == pytest.approx(.80)
    assert shared["read_status"] == "reading"
    response = _status(world, "finished", device_id="kindle-a", name="Kindle A")
    assert response.status_code == 200
    assert _account_state(world)[0] == ub.ReadBook.STATUS_FINISHED
    assert _journal(world, _device_named(world, "Kindle A")).reading_status == ub.ReadBook.STATUS_IN_PROGRESS


def _listing(world, raw="kindle-a", **query):
    response = world.client.get("/kosync/syncs/library", query_string=query,
                                headers=_headers(raw, "Kindle A"))
    assert response.status_code == 200
    return response.get_json()


def test_private_library_has_own_progress_and_no_account_fallback(world):
    world.position(world.reader, BOOK, 80)
    world.position(world.reader, 2, 100)
    _put(world, 20, device_id="kindle-a", name="Kindle A")
    body = _listing(world)
    books = {row["book_id"]: row for row in body["books"]}
    assert books[BOOK]["progress"] == pytest.approx(.20)
    assert books[BOOK]["read_status"] == "reading"
    assert books[2]["progress"] is None
    assert books[2]["read_status"] == "unread"
    assert "last_read" not in books[2]
    # A change on the private device invalidates that device's manifest.
    _put(world, 35, device_id="kindle-a", name="Kindle A")
    refreshed = _listing(world, if_revision=body["revision"])
    assert not refreshed.get("unchanged")
    assert refreshed["revision"] != body["revision"]
    assert refreshed["books"][0]["progress"] == pytest.approx(.35)


def test_private_percentage_without_capability_is_withheld_without_exact_bridge(world, monkeypatch):
    _stock_reader_sets_shared(world, 80)
    _put(world, 20, device_id="kindle-a", name="Kindle A")
    monkeypatch.setattr(world.kosync, "_exact_xpointer",
                        lambda *args: pytest.fail("private row reached shared bridge"))
    # The other file cannot use the private locator. An old client gets the
    # same hint as today's percentage-only shared rows, never the account row.
    response = _get(world, device_id="kindle-a", name="Kindle A",
                    document=OTHER_DIGEST, advertises=False)
    assert response.status_code == 200
    assert response.get_json() == {"position_kinds_available": ["percentage"]}


@pytest.mark.parametrize("status,percent", [("finished", 100), ("reading", 35)])
def test_private_status_fans_out_without_touching_custom_column(world, monkeypatch, status, percent):
    from cps import config, helper
    from cps.services import koreader_library
    _put(world, 35, device_id="kindle-a", name="Kindle A")
    world.hardcover.clear()
    before = _account_state(world)
    monkeypatch.setattr(config, "config_read_column", 7)
    monkeypatch.setattr(helper, "edit_book_read_status",
                        lambda *a, **k: pytest.fail("private status reached account/custom column"))
    monkeypatch.setattr(koreader_library, "_clear_custom_read_column",
                        lambda *a: pytest.fail("private reading cleared custom column"))
    assert _status(world, status, device_id="kindle-a", name="Kindle A").status_code == 200
    assert _account_state(world) == before
    assert world.hardcover == [(world.reader.id, BOOK, percent)]


def test_checked_latest_place_mirrors_backward_into_kobo_carrier(world):
    device = _register(world, "kindle-a", "Kindle A")
    _share(world, device, True)
    now = time.time()
    _put(world, 85, device_id="kindle-a", name="Kindle A", client_at=now-60)
    _put(world, 20, device_id="kindle-a", name="Kindle A", client_at=now-30)
    assert _account_state(world)[:2] == (ub.ReadBook.STATUS_IN_PROGRESS, 20)
    _put(world, 90, device_id="kindle-a", name="Kindle A", client_at=now-50)
    assert _account_state(world)[:2] == (ub.ReadBook.STATUS_IN_PROGRESS, 20)


def test_header_identity_overrides_conflicting_body_identity(world):
    response = world.client.put("/kosync/syncs/progress", json={
        "document": OTHER_DIGEST, "progress": "12", "percentage": .3,
        "device": "Body name", "device_id": "other-body-id",
    }, headers=_headers("kindle-a", "Kindle A"))
    assert response.status_code == 200
    device = _device_named(world, "Kindle A")
    assert _journal(world, device).progress_percent == 30
    assert _account_state(world) == (None, None, None)


def test_future_clock_is_fixed_at_receipt_not_rejudged_later(world):
    _put(world, 35, device_id="kindle-a", name="Kindle A", client_at=time.time()+3600)
    device = _device_named(world, "Kindle A")
    row = _journal(world, device)
    assert row.client_modified_at is None
    assert row.server_modified_at is not None


def test_delayed_progress_keeps_a_newer_explicit_private_status(world):
    now = time.time()
    _put(world, 20, device_id="kindle-a", name="Kindle A", client_at=now-120)
    device = _device_named(world, "Kindle A")
    assert _status(world, "finished", device_id="kindle-a", name="Kindle A").status_code == 200
    status_at = _journal(world, device).status_modified_at
    _put(world, 30, device_id="kindle-a", name="Kindle A", client_at=now-60)
    own = _journal(world, device)
    assert own.progress_percent == 30
    assert own.reading_status == ub.ReadBook.STATUS_FINISHED
    assert own.status_modified_at == status_at


def test_status_only_row_does_not_block_first_older_progress(world):
    assert _status(world, "finished", device_id="kindle-a", name="Kindle A").status_code == 200
    device = _device_named(world, "Kindle A")
    _put(world, 20, device_id="kindle-a", name="Kindle A", client_at=time.time()-60)
    own = _journal(world, device)
    assert own.progress_percent == 20
    assert own.reading_status == ub.ReadBook.STATUS_FINISHED


def test_private_library_walk_is_cached_only_for_that_device(world, monkeypatch):
    from cps.services import koreader_library
    world.position(world.reader, 2, 80)
    _put(world, 20, device_id="kindle-a", name="Kindle A")
    checked = _register(world, "kindle-b", "Kindle B")
    _share(world, checked, True)
    builds = []
    build = koreader_library.build_manifest

    def count_builds(*args, **kwargs):
        builds.append(1)
        return build(*args, **kwargs)

    monkeypatch.setattr(koreader_library, "build_manifest", count_builds)
    first = _listing(world, limit=1)
    cursor = first["next_cursor"]
    # Try the other device while the private walk is still cached; finishing
    # the private walk first would evict it and hide the unsafe-key failure.
    other = _listing(world, raw="kindle-b", cursor=cursor, limit=1)["books"][0]
    assert other["progress"] == pytest.approx(.80)
    assert other["read_status"] == "reading"
    own_last = _listing(world, cursor=cursor, limit=1)["books"][0]
    assert own_last["book_id"] == 2
    assert own_last["progress"] is None
    assert own_last["read_status"] == "unread"
    assert len(builds) == 2  # One manifest for each walk; private page two reuses it.
