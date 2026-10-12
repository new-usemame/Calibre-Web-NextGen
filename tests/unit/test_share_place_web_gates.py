# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for "Share my place" PR 4: the web reader's gates.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 15 (with
sections 2, 3, 8, 12 and 14c). Read as a spec:

* The web reader is the account's one ``webreader`` device ("Browser"). Its
  own place is its ``Bookmark`` plus its journal row.
* Gates on, Browser unchecked: a save writes its own Bookmark and journal
  (place AND status), latest-wins through the arbiter, never an upsert. It
  writes no ReadBook, kosync row, Kobo bookmark or custom read column.
  Hardcover still hears about it (section 8). Opening the reader is the same:
  the "reading" status goes to its journal only.
* Gates on, Browser checked: the journal as above, then the shared carriers
  latest-wins (a deliberate backward move propagates); 99% or more finishes
  the account status; a manual finish is never undone by a lower save.
* Resume: unchecked is own-only (no offer). Checked compares the shared report
  with its own through the arbiter: a newer exact shared report opens
  automatically, a newer percentage-only one is an offer, and a shared report
  the Browser made itself is not newer.
* CFI -> span: a checked save's EPUB CFI becomes the Kobo span on the shared
  bookmark; delivery to a Kobo follows 3b (exact only with download proof,
  otherwise the Kobo's own held Location); any failure is percentage only.
* The rollout switch stays default OFF in PR 4 (coordinator, 2026-10-12): unset
  or ``0`` is today's path byte for byte; ``1`` and ``user:<ids>`` enforce.
* The devices list always includes the account's Browser and says whether
  the gates are enforced for the account.

Everything goes through real routes: the SPA ``/api/v1/books/<id>/bookmark``
(POST and GET) with real session login, the classic ``/ajax/bookmark`` and
``/read`` routes, the real kosync blueprint, the real Kobo state handlers and
the real devices API. The app database is a real SQLite file because the
resume read opens its own read-only connection to it.
"""

import inspect
import os
import sqlite3
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool

from cps import constants, db, ub
from cps.progress_syncing.models import KOSyncProgress
from cps.services import device_registry
from tests.unit.kobo_state_world import READ_COLUMN_ID, read_column_class  # noqa: F401
from tests.unit.share_place_span_world import (  # noqa: F401  (fixture)
    BOOK, OTHER_DIGEST, _now, after_now, location_of, span_location, span_rows, sw,
)

pytestmark = pytest.mark.unit

SWITCH = "CWNG_SHARE_PLACE_GATES"
FINISHED = ub.ReadBook.STATUS_FINISHED
IN_PROGRESS = ub.ReadBook.STATUS_IN_PROGRESS
BAD_CFI = "epubcfi(/6/9998!/4/2/1:0)"  # a well-formed CFI addressing nothing


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    """Every test here runs with the gates on for everyone unless it says
    otherwise (spec section 12)."""
    monkeypatch.setenv(SWITCH, "1")


# -- the world ----------------------------------------------------------------

@pytest.fixture
def file_backed(monkeypatch, tmp_path):
    """``LibraryWorld`` on a real app.db file (one shared connection, as its
    in-memory database has), so ``read_resume_position`` can read it."""
    from tests.unit import koreader_library_world as library_world
    real = library_world.create_engine
    path = tmp_path / "app.db"
    monkeypatch.setattr(library_world, "create_engine", lambda _url, **_kw: real(
        "sqlite:///" + str(path), poolclass=StaticPool,
        connect_args={"check_same_thread": False}))


@pytest.fixture
def rows():
    found = span_rows()
    return {"kobo-own": found[5], "early": found[100], "mid": found[200],
            "late": found[300]}


@pytest.fixture
def w(read_column_class, file_backed, sw, monkeypatch, tmp_path):  # noqa: F811
    from cps import annotations, config, helper
    from cps import web as classic
    from cps.web import web as classic_blueprint

    sw.enable_web()
    sw.app.register_blueprint(classic_blueprint)
    sw.reader.role = sw.reader.role | constants.ROLE_VIEWER
    sw.session.commit()
    sw.web = sw.browser("reader")

    # The custom read column is configured (book-level, Calibre side).
    monkeypatch.setattr(config, "config_read_column", READ_COLUMN_ID, raising=False)
    # kobo_resume reads the library's metadata.db for the EPUB's path.
    book = sw.session.get(db.Books, BOOK)
    data = sw.session.query(db.Data).filter_by(book=BOOK, format="EPUB").one()
    metadata = sqlite3.connect(tmp_path / "metadata.db")
    metadata.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, path TEXT)")
    metadata.execute("CREATE TABLE data (book INTEGER, format TEXT, name TEXT)")
    metadata.execute("INSERT INTO books VALUES (?, ?)", (BOOK, book.path))
    metadata.execute("INSERT INTO data VALUES (?, 'EPUB', ?)", (BOOK, data.name))
    metadata.commit()
    metadata.close()
    monkeypatch.setattr(config, "config_calibre_dir", str(tmp_path), raising=False)

    # The devices list reads book visibility from metadata.db.
    monkeypatch.setattr(
        annotations, "_visible_book_scopes_for_owners",
        lambda owners, _session, candidates: {
            int(owner.id): frozenset(candidates.get(int(owner.id), ()))
            for owner in owners if owner is not None
        },
    )
    # Hardcover hears about the account's reading through this queue.
    sw.hardcover = []
    monkeypatch.setattr(helper, "queue_hardcover_reading_progress",
                        lambda user, book_id, percent: sw.hardcover.append(
                            (int(book_id), float(percent))))
    # Classic /read renders a template this app does not carry.
    monkeypatch.setattr(classic, "render_title_template", lambda *_a, **_k: "reader")
    monkeypatch.setattr(classic, "calibre_db", SimpleNamespace(
        get_filtered_book=lambda *_a, **_k: sw.session.get(db.Books, BOOK),
        order_authors=lambda books, *_a, **_k: books[0].authors,
    ))
    return sw


def browser(w, *, checked):
    """The account's Browser row, ticked or not through the devices API."""
    device = device_registry.upsert_webreader_device(w.session, user_id=w.reader.id)
    w.session.commit()
    if checked:
        w.share(device, True)
    w.session.expire_all()
    return w.session.get(ub.Device, device.id)


def cfi_of(w, row):
    """The EPUB CFI of a span row's word, checked to align back to the span."""
    from cps.services import kepub_alignment, koreader_xpointer
    cfi = koreader_xpointer.xpointer_to_cfi(w.epub, row["xpointer"])
    assert cfi, row
    xpointer = koreader_xpointer.cfi_to_xpointer(w.epub, cfi)
    assert kepub_alignment.xpointer_to_span(w.epub, w.kepub, xpointer) == (
        row["source"], row["span"])  # fixture ground truth
    return cfi


# -- the wire -----------------------------------------------------------------

def save(w, percent, cfi, *, route="spa"):
    if route == "spa":
        response = w.web.post(f"/api/v1/books/{BOOK}/bookmark", json={
            "format": "epub", "bookmark": cfi, "percentage": percent})
        assert response.status_code == 204, response.get_data(as_text=True)
    else:
        response = w.web.post(f"/ajax/bookmark/{BOOK}/epub",
                              data={"bookmark": cfi, "percentage": str(percent)})
        assert response.status_code == 201, response.get_data(as_text=True)
    w.session.expire_all()


def resume(w):
    response = w.web.get(f"/api/v1/books/{BOOK}/bookmark")
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def opened(w):
    """(own CFI, resume hint) the reader is given; other keys are free."""
    body = resume(w)
    return body["bookmark"], body["resume"]


def open_reader(w):
    response = w.web.get(f"/read/{BOOK}/epub")
    assert response.status_code == 200, response.get_data(as_text=True)
    w.session.expire_all()


def later():
    """Keep two receipts apart on a store with one-second resolution."""
    time.sleep(1.1)


# -- what the account and the devices see --------------------------------------

def shared_bookmark(w):
    """(percent, Location, last_modified) of the shared Kobo bookmark."""
    w.session.expire_all()
    state = (w.session.query(ub.KoboReadingState)
             .filter_by(user_id=w.reader.id, book_id=BOOK).first())
    mark = state.current_bookmark if state is not None else None
    if mark is None:
        return None
    location = ({"Source": mark.location_source, "Type": mark.location_type,
                 "Value": mark.location_value} if mark.location_value else None)
    return mark.progress_percent, location, mark.last_modified


def account_status(w):
    w.session.expire_all()
    row = w.read_row(w.reader, BOOK)
    return row.read_status if row is not None else None


def kosync_rows(w):
    w.session.expire_all()
    return [(row.document, row.percentage, row.progress, row.timestamp)
            for row in w.session.query(KOSyncProgress).filter_by(user_id=w.reader.id)]


def read_column(w):
    from cps import db as calibre
    w.session.expire_all()
    return w.session.query(calibre.cc_classes[READ_COLUMN_ID]).filter_by(book=BOOK).all()


def journal(w, device):
    w.session.expire_all()
    return (w.session.query(ub.DeviceReadingPosition)
            .filter_by(device_id=device.id, book_id=BOOK).one_or_none())


def account_snapshot(w):
    return shared_bookmark(w), account_status(w), kosync_rows(w), len(read_column(w))


def kobo_with_own_place(w, rows, *, proven):
    """A checked Kobo that last reported its own span (5%) an hour ago, fully
    synced; ``proven``: it completed a download of the current KEPUB."""
    import logging
    from cps.services.kobo_post_download_restore import record_download
    kobo = w.kobo_device("Kobo Libra", checked=True)
    if proven:
        record_download(device_id=kobo.id, book_id=BOOK, book_format="kepub",
                        log=logging.getLogger("test"))
        w.session.commit()
    own = rows["kobo-own"]
    w.put(kobo, own["source"], own["span"], 5, _now() - timedelta(hours=1))
    w.settle(kobo)
    return kobo


def own_span(rows):
    return span_location(rows["kobo-own"]["source"], rows["kobo-own"]["span"])


def row_span(rows, name):
    return span_location(rows[name]["source"], rows[name]["span"])


# == 1. Unchecked Browser: private place and status ===========================

@pytest.mark.parametrize("route", ["spa", "classic"])
def test_unchecked_save_is_the_browsers_own_and_changes_nothing_shared(w, rows, route):
    """Spec 3.2 / 15 write gate. A checked KOReader set the shared place to
    40%; the unchecked Browser then reads to the end."""
    w.koreader("koreader-1", "KOReader", checked=True)
    w.push(rows["mid"]["xpointer"], 40, client_at=_now() - timedelta(seconds=30))
    me = browser(w, checked=False)
    before = account_snapshot(w)
    assert before[0][0] == pytest.approx(40) and before[1] == IN_PROGRESS

    cfi = cfi_of(w, rows["late"])
    save(w, 99.5, cfi, route=route)

    assert account_snapshot(w) == before        # Kobo bookmark, ReadBook, kosync, read column
    assert w.pull() == (pytest.approx(40), rows["mid"]["xpointer"])
    assert opened(w) == (cfi, None)
    own = journal(w, me)
    assert (own.progress_percent, own.cfi, own.reading_status) == (
        pytest.approx(99.5), cfi, FINISHED)
    assert w.hardcover == [(BOOK, pytest.approx(99.5))]


def test_unchecked_save_of_an_unread_book_creates_no_account_state(w, rows):
    browser(w, checked=False)

    save(w, 30, cfi_of(w, rows["early"]))

    assert shared_bookmark(w) is None
    assert account_status(w) is None
    assert kosync_rows(w) == []


def test_unchecked_reader_open_is_reading_on_the_browser_only(w):
    me = browser(w, checked=False)

    open_reader(w)

    # Session counters may be kept on the account row; its status may not move.
    assert account_status(w) in (None, ub.ReadBook.STATUS_UNREAD)
    assert journal(w, me).reading_status == IN_PROGRESS


def test_checked_reader_open_is_the_accounts_reading(w):
    """Control: a checked Browser's open still sets the account status."""
    browser(w, checked=True)

    open_reader(w)

    assert account_status(w) == IN_PROGRESS


# == 2. The Browser journal is arbitrated, not upserted ======================

def test_an_older_web_report_does_not_overwrite_a_newer_journal_report(w, rows):
    """The journal row already holds a report whose clock is ahead of this
    request's receipt (inside the five-minute skew allowance). A plain upsert
    replaces it; the arbiter keeps it. Its companion, that an ordinary save
    IS journalled, is the first test of section 1."""
    me = browser(w, checked=False)
    newer = cfi_of(w, rows["mid"])
    w.session.add(ub.DeviceReadingPosition(
        device_id=me.id, book_id=BOOK, progress_percent=45.0, cfi=newer,
        client_modified_at=_now() + timedelta(minutes=2),
        server_modified_at=_now() - timedelta(seconds=1)))
    w.session.commit()

    save(w, 20, cfi_of(w, rows["early"]))

    own = journal(w, me)
    assert (own.progress_percent, own.cfi) == (pytest.approx(45.0), newer)


# == 3. Checked Browser: latest wins =========================================

@pytest.mark.parametrize("route", ["spa", "classic"])
def test_a_checked_backward_save_moves_the_shared_place_back(w, rows, route):
    w.koreader("koreader-1", "KOReader", checked=True)
    browser(w, checked=True)
    save(w, 60, cfi_of(w, rows["late"]), route=route)
    assert shared_bookmark(w)[0] == pytest.approx(60)
    later()

    save(w, 30, cfi_of(w, rows["early"]), route=route)

    assert shared_bookmark(w)[0] == pytest.approx(30)
    assert w.pull()[0] == pytest.approx(30)


def test_a_checked_save_at_99_finishes_the_account_and_the_browser(w, rows):
    me = browser(w, checked=True)

    save(w, 99.5, cfi_of(w, rows["late"]))

    assert account_status(w) == FINISHED
    assert len(read_column(w)) == 1
    assert journal(w, me).reading_status == FINISHED


def test_a_checked_save_below_99_does_not_unfinish_a_manual_finish(w, rows):
    """Control: the guard against un-finishing a manual finish stays. "Mark
    as read" leaves a bare Kobo bookmark (no percentage), so no position
    comparison can stand in for the guard."""
    w.position(w.reader, BOOK, None, status=FINISHED)
    browser(w, checked=True)

    save(w, 40, cfi_of(w, rows["mid"]))

    assert account_status(w) == FINISHED


# == 4. Resume ===============================================================

def test_checked_browser_opens_at_a_newer_exact_shared_place(w, rows):
    """Spec 2: a checked device resumes at the shared place. ``automatic``
    means the reader opens there, ahead of its own older CFI. (A Kobo span
    resolves for the web only in an EPUB that carries Kobo spans; this
    library EPUB does not, so the exact sender here is KOReader.)"""
    browser(w, checked=True)
    save(w, 20, cfi_of(w, rows["early"]))
    w.koreader("koreader-1", "KOReader", checked=True)
    w.push(rows["late"]["xpointer"], 60, client_at=after_now(2))

    served = resume(w)["resume"]

    assert served is not None
    assert served["mode"] == "automatic"
    assert served["percentage"] == pytest.approx(60)
    assert served["cfi"] == cfi_of(w, rows["late"])


def test_checked_browser_is_offered_a_newer_percentage_only_place(w, rows):
    """Control: an inexact place never overrides the Browser's exact one
    (KOReader reported from a file that is not the library EPUB)."""
    browser(w, checked=True)
    save(w, 20, cfi_of(w, rows["early"]))
    w.koreader("koreader-1", "KOReader", checked=True)
    w.push(rows["late"]["xpointer"], 60, client_at=after_now(2), document=OTHER_DIGEST)

    served = resume(w)["resume"]

    assert served is not None and served["mode"] == "offer"
    assert served["percentage"] == pytest.approx(60)


def test_checked_browser_is_not_offered_its_own_shared_report(w, rows):
    """Control: the shared place the Browser itself made, and an older report
    from another device arriving after it, are not newer than its own."""
    browser(w, checked=True)
    cfi = cfi_of(w, rows["mid"])
    save(w, 50, cfi)
    w.koreader("koreader-1", "KOReader", checked=True)
    w.push(rows["early"]["xpointer"], 20, client_at=_now() - timedelta(hours=1))

    assert opened(w) == (cfi, None)


@pytest.mark.parametrize("own_place", [True, False])
def test_unchecked_browser_is_never_offered_the_shared_place(w, rows, own_place):
    browser(w, checked=False)
    cfi = None
    if own_place:
        cfi = cfi_of(w, rows["early"])
        save(w, 20, cfi)
    w.koreader("koreader-1", "KOReader", checked=True)
    w.push(rows["late"]["xpointer"], 60, client_at=after_now(2))

    assert opened(w) == (cfi, None)


# == 5. CFI -> Kobo span =====================================================

def test_a_checked_save_stores_its_place_as_the_kobo_span(w, rows):
    browser(w, checked=True)

    save(w, 60, cfi_of(w, rows["late"]))

    percent, location, _ = shared_bookmark(w)
    assert percent == pytest.approx(60)
    assert location == row_span(rows, "late")


def test_a_proven_checked_kobo_receives_the_browsers_exact_span(w, rows):
    kobo = kobo_with_own_place(w, rows, proven=True)
    browser(w, checked=True)

    save(w, 60, cfi_of(w, rows["late"]))

    bookmark = w.get(kobo)["CurrentBookmark"]
    assert bookmark["ProgressPercent"] == pytest.approx(60)
    assert location_of(bookmark) == row_span(rows, "late")
    assert w.synced_location(kobo) == row_span(rows, "late")


def test_an_unproven_checked_kobo_keeps_its_own_location(w, rows):
    """Control (3b rule): no download proof, so its own held Location with
    the new percentage, never the Browser's span."""
    kobo = kobo_with_own_place(w, rows, proven=False)
    browser(w, checked=True)

    save(w, 60, cfi_of(w, rows["late"]))

    bookmark = w.get(kobo)["CurrentBookmark"]
    assert bookmark["ProgressPercent"] == pytest.approx(60)
    assert location_of(bookmark) == own_span(rows)


@pytest.mark.parametrize("failure", ["unaligned-cfi", "no-library-kepub"])
def test_a_place_that_does_not_align_is_percentage_only(w, rows, failure):
    """Control: nothing fabricated, and nothing stale passed off as exact. The
    Browser's previous save stored an exact span; the next one cannot be
    converted, so a proven Kobo is served its own held Location with the new
    percentage, never the previous span and never an invented one."""
    kobo = kobo_with_own_place(w, rows, proven=True)
    browser(w, checked=True)
    save(w, 40, cfi_of(w, rows["mid"]))
    later()
    cfi = BAD_CFI
    if failure == "no-library-kepub":
        cfi = cfi_of(w, rows["late"])
        os.unlink(w.kepub)

    save(w, 60, cfi)

    percent, location, _ = shared_bookmark(w)
    assert percent == pytest.approx(60)
    assert location in (None, own_span(rows), row_span(rows, "mid"))
    bookmark = w.get(kobo)["CurrentBookmark"]
    assert bookmark["ProgressPercent"] == pytest.approx(60)
    assert location_of(bookmark) == own_span(rows)


def test_a_checked_browser_place_still_reaches_koreader_exactly(w, rows):
    """Control: web -> KOReader (``web_position_for_device``) keeps working."""
    from cps.services import koreader_xpointer
    w.koreader("koreader-1", "KOReader", checked=True)
    browser(w, checked=True)
    cfi = cfi_of(w, rows["late"])

    save(w, 60, cfi)

    assert w.pull() == (pytest.approx(60), koreader_xpointer.cfi_to_xpointer(w.epub, cfi))


# == 6. The rollout switch (default off in PR 4) =============================

def _todays_web_save(w, rows, route):
    """Today's path for an unchecked Browser: furthest wins on every shared
    carrier, the journal takes the latest report."""
    w.koreader("koreader-1", "KOReader", checked=False)
    me = browser(w, checked=False)
    save(w, 60, cfi_of(w, rows["late"]), route=route)
    later()
    early = cfi_of(w, rows["early"])
    save(w, 30, early, route=route)

    assert shared_bookmark(w)[0] == pytest.approx(60)
    assert account_status(w) == IN_PROGRESS
    assert w.pull()[0] == pytest.approx(60)
    assert w.hardcover == [(BOOK, pytest.approx(60))]
    own = journal(w, me)
    assert (own.progress_percent, own.cfi) == (pytest.approx(30), early)


@pytest.mark.parametrize("value", [None, "0", "user:{other}"])
@pytest.mark.parametrize("route", ["spa", "classic"])
def test_switch_off_keeps_todays_web_save(w, rows, monkeypatch, value, route):
    """Parity with 73790181e: unset (the PR 4 default), the emergency ``0``,
    and an allow-list without this account."""
    from cps.services.share_place import gates_enforced
    if value is None:
        monkeypatch.delenv(SWITCH, raising=False)
    else:
        monkeypatch.setenv(SWITCH, value.format(other=w.reader.id + 100))
    assert gates_enforced(w.reader.id) is False

    _todays_web_save(w, rows, route)


def test_allow_list_with_this_account_gates_the_web_save(w, rows, monkeypatch):
    from cps.services.share_place import gates_enforced
    monkeypatch.setenv(SWITCH, f"user:{w.reader.id + 100},{w.reader.id}")
    assert gates_enforced(w.reader.id) is True
    browser(w, checked=False)

    save(w, 30, cfi_of(w, rows["early"]))

    assert shared_bookmark(w) is None
    assert account_status(w) is None


# == 7. The devices list =====================================================

def _devices(w):
    path = "/api/annotations/devices"
    with w.app.test_request_context(path, method="GET"):
        endpoint, args = w.app.url_map.bind("localhost").match(path, method="GET")
        result = inspect.unwrap(w.app.view_functions[endpoint])(**args)
    response, status = result if isinstance(result, tuple) else (result, result.status_code)
    assert status == 200
    w.session.expire_all()
    return response.get_json()


def test_the_devices_list_includes_the_browser_before_any_web_reading(w):
    first = [row for row in _devices(w)["devices"] if row["kind"] == "webreader"]
    second = [row for row in _devices(w)["devices"] if row["kind"] == "webreader"]

    assert len(first) == 1
    assert first[0]["share_place"] is False
    assert first[0]["place_control"] == "per_device"
    assert [row["public_id"] for row in second] == [first[0]["public_id"]]
    assert w.session.query(ub.Device).filter_by(
        user_id=w.reader.id, kind="webreader").count() == 1


@pytest.mark.parametrize("value, expected", [
    (None, False), ("0", False), ("1", True),
    ("user:{other}", False), ("user:{other},{me}", True),
])
def test_the_devices_list_says_whether_gates_are_enforced(w, monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(SWITCH, raising=False)
    else:
        monkeypatch.setenv(SWITCH, value.format(other=w.reader.id + 100, me=w.reader.id))

    assert _devices(w)["share_place_gates"] is expected


# == 15a.1 Web -> KOReader exactness binds to the shared report ==============
# KOReader's exact XPointer from the web must come from the Browser report that
# wrote the shared KOSync row. A later private Browser report at the same
# percentage (an unchecked box, or a "Read from here" save sent with
# share_with_devices=false) must never change what KOReader receives: it gets
# the shared row's percentage, or the old shared exact place.

def private_save(w, percent, cfi):
    response = w.web.post(f"/api/v1/books/{BOOK}/bookmark", json={
        "format": "epub", "bookmark": cfi, "percentage": percent,
        "share_with_devices": False})
    assert response.status_code == 204, response.get_data(as_text=True)
    w.session.expire_all()


@pytest.mark.parametrize("private", ["unchecked", "preview-gates-on", "preview-gates-off"])
def test_a_private_browser_save_never_becomes_koreaders_exact_place(
        w, rows, monkeypatch, private):
    from cps.services import koreader_xpointer
    from cps.services.koreader_position import is_xpointer
    if private == "preview-gates-off":
        monkeypatch.delenv(SWITCH, raising=False)
    w.koreader("koreader-1", "KOReader", checked=True)
    me = browser(w, checked=True)
    shared_cfi = cfi_of(w, rows["late"])
    save(w, 60, shared_cfi)
    shared_xpointer = koreader_xpointer.cfi_to_xpointer(w.epub, shared_cfi)
    assert w.pull() == (pytest.approx(60), shared_xpointer)
    later()

    private_cfi = cfi_of(w, rows["early"])
    if private == "unchecked":
        w.share(me, False)
        save(w, 60, private_cfi)
    else:
        private_save(w, 60, private_cfi)

    percent, progress = w.pull()
    assert percent == pytest.approx(60)
    assert progress != koreader_xpointer.cfi_to_xpointer(w.epub, private_cfi)
    assert progress == shared_xpointer or not is_xpointer(progress)


def test_a_later_checked_save_rebinds_koreaders_exact_place(w, rows):
    """Control: the binding follows the newest shared report, even at the
    same percentage (a fix must not pin the first CFI for good)."""
    from cps.services import koreader_xpointer
    w.koreader("koreader-1", "KOReader", checked=True)
    browser(w, checked=True)
    save(w, 60, cfi_of(w, rows["late"]))
    later()

    newer = cfi_of(w, rows["early"])
    save(w, 60, newer)

    assert w.pull() == (pytest.approx(60), koreader_xpointer.cfi_to_xpointer(w.epub, newer))


# == 15a.2 The share flag on a keepalive-shaped save (server half) ===========
# Reader.tsx's teardown keepalive is a plain POST of format/bookmark/percentage.
# The client must send its current share flag; the server keeps honouring an
# explicit false and keeps today's default (share) when the flag is omitted,
# which is what older clients send.

def test_an_explicit_no_share_save_changes_no_shared_state(w, rows):
    """Control: "Read from here" on a checked Browser stays Browser-only."""
    me = browser(w, checked=True)
    cfi = cfi_of(w, rows["early"])

    private_save(w, 30, cfi)

    assert shared_bookmark(w) is None
    assert account_status(w) is None
    assert kosync_rows(w) == []
    assert journal(w, me).cfi == cfi


def test_a_save_without_the_share_flag_shares_as_older_clients_expect(w, rows):
    """Control: an omitted flag means share (old-client default)."""
    browser(w, checked=True)

    response = w.web.post(f"/api/v1/books/{BOOK}/bookmark", json={
        "format": "epub", "bookmark": cfi_of(w, rows["early"]), "percentage": 30})
    assert response.status_code == 204
    w.session.expire_all()

    assert shared_bookmark(w)[0] == pytest.approx(30)
    assert account_status(w) == IN_PROGRESS
    assert [row[1] for row in kosync_rows(w)] == [pytest.approx(30)]
