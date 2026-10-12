# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for "Share my place" PR 3a: the KOReader -> Kobo bridge.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 11 (3a,
last bullet: "Checked KOReader -> Kobo bridge"), with section 3 criteria 2, 3
and 5 and section 10 (identity is the ``X-CWNG-Device-ID`` header).

These need a checked, *identified* KOReader writer, which PR 2 (KOSync gates)
provides, so they also stay red until PR 2 lands. The unidentified bridge
(forward-only percentage, unchanged) is pinned in
``test_share_place_kobo_gates.py``.

* A checked identified KOReader push is latest-wins against the Kobo
  bookmark: a deliberate backward move reaches it (as a percentage; the exact
  span is PR 3b), and the reading-state clock moves so a checked Kobo's next
  sync carries it. An older report arriving later does not move it.
* An unchecked identified KOReader push never touches the Kobo bookmark.

Runs through the real kosync blueprint over HTTP with real Basic auth
(``LibraryWorld``); the toggle goes through the real devices API route.
"""

import inspect
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cps import ub
from cps.progress_syncing.models import BookFormatChecksum
from cps.services import device_registry
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit

BOOK = 1
DIGEST = "0123456789abcdef0123456789abcdef"
XPOINTER = "/body/DocFragment[7]/body/p[3]/text().12"


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    """Spec section 12: enforce the gates for every account in these tests."""
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")


@pytest.fixture
def world(monkeypatch, tmp_path):
    built = LibraryWorld(monkeypatch, tmp_path)
    from cps import annotations

    built.app.register_blueprint(annotations.annotations_bp)
    built.reader = built.add_user("reader")
    built.add_book(BOOK, "First Light")
    built.session.add(BookFormatChecksum(book=BOOK, format="EPUB", checksum=DIGEST))
    built.session.commit()
    monkeypatch.setattr(annotations, "current_user", SimpleNamespace(id=built.reader.id))
    monkeypatch.setattr(built.kosync, "push_reading_state_to_hardcover",
                        lambda *_a: None)
    # A checked Kobo on the account holds the shared bookmark at 80%,
    # reported an hour ago.
    built.kobo_at = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(
        tzinfo=None, microsecond=0)
    built.position(built.reader, BOOK, 80.0, at=built.kobo_at)
    state = (built.session.query(ub.KoboReadingState)
             .filter_by(user_id=built.reader.id, book_id=BOOK).one())
    state.current_bookmark.location_value = "kobo.80.1"
    state.current_bookmark.location_type = "KoboSpan"
    state.current_bookmark.location_source = "OEBPS/chap.xhtml"
    built.session.commit()
    # Editing the location re-stamps the bookmark's clocks to now; put the
    # bookmark (its clock and its receipt) and the reading state back at the
    # hour-old report they stand for.
    bookmark = ub.KoboBookmark.__table__
    built.session.execute(bookmark.update()
                          .where(bookmark.c.id == state.current_bookmark.id)
                          .values(last_modified=built.kobo_at,
                                  server_modified_at=built.kobo_at))
    state.last_modified = built.kobo_at
    state.priority_timestamp = built.kobo_at
    built.session.commit()
    built.session.add(ub.Device(
        user_id=built.reader.id, kind="kobo", display_name="Kobo Checked",
        active=True, created_by="auto", share_place=True,
        share_place_enabled_at=datetime.now(timezone.utc)))
    built.session.commit()
    yield built
    built.close()


def _koreader(world, raw_id, name, *, checked):
    internal = device_registry.register_koreader_device_best_effort(
        user_id=world.reader.id, device_id=raw_id, device_name=name,
        secret_key=world.app.secret_key,
    )
    assert internal is not None
    device = world.session.get(ub.Device, internal)
    if checked:
        path = f"/api/annotations/devices/{device.public_id}"
        app = world.app
        with app.test_request_context(path, method="PATCH", json={"share_place": True}):
            endpoint, args = app.url_map.bind("localhost").match(path, method="PATCH")
            result = inspect.unwrap(app.view_functions[endpoint])(**args)
        status = result[1] if isinstance(result, tuple) else result.status_code
        assert status == 200
    world.session.expire_all()
    return device


def _push(world, percent, *, raw_id, name, client_at):
    """An identified push (the CWNG plugin sends the identity header)."""
    headers = LibraryWorld.basic("reader")
    headers["Accept"] = "application/vnd.koreader.v1+json"
    headers["X-CWNG-Device-ID"] = raw_id
    headers["X-CWNG-Device-Name"] = name
    response = world.client.put("/kosync/syncs/progress", json={
        "document": DIGEST, "progress": XPOINTER, "percentage": percent / 100.0,
        "device": name, "device_id": raw_id, "timestamp": int(client_at),
    }, headers=headers)
    assert response.status_code == 200, response.get_data(as_text=True)
    world.session.expire_all()


def _kobo(world):
    world.session.expire_all()
    state = (world.session.query(ub.KoboReadingState)
             .filter_by(user_id=world.reader.id, book_id=BOOK).one())
    mark = state.current_bookmark

    def naive(value):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    return SimpleNamespace(
        percent=mark.progress_percent, location=mark.location_value,
        bookmark_at=naive(mark.last_modified), state_at=naive(state.last_modified))


def test_checked_koreader_backward_move_reaches_the_kobo_bookmark(world):
    _koreader(world, "kindle-a", "Kindle A", checked=True)

    _push(world, 30, raw_id="kindle-a", name="Kindle A",
          client_at=time.time() - 60)

    kobo = _kobo(world)
    assert kobo.percent == pytest.approx(30.0)
    # The reading-state clock moved, so a checked Kobo's next sync carries it.
    assert kobo.bookmark_at > world.kobo_at
    assert kobo.state_at > world.kobo_at


def test_checked_koreader_older_report_does_not_move_the_kobo_bookmark(world):
    _koreader(world, "kindle-a", "Kindle A", checked=True)

    _push(world, 30, raw_id="kindle-a", name="Kindle A",
          client_at=world.kobo_at.replace(tzinfo=timezone.utc).timestamp() - 600)

    kobo = _kobo(world)
    assert kobo.percent == pytest.approx(80.0)
    assert kobo.location == "kobo.80.1"


def test_unchecked_koreader_push_never_touches_the_kobo_bookmark(world):
    _koreader(world, "kindle-a", "Kindle A", checked=False)
    before = _kobo(world)

    _push(world, 95, raw_id="kindle-a", name="Kindle A",
          client_at=time.time() - 60)

    after = _kobo(world)
    assert (after.percent, after.location, after.bookmark_at, after.state_at) == (
        before.percent, before.location, before.bookmark_at, before.state_at)
