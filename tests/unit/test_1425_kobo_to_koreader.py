# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#1425 gap 1 — reading on a Kobo must reach KOReader.

Split out of #324, where @IceSentry reported that a Kobo and a KOReader-based
device both sync with the server, the book page shows whichever synced last,
and yet neither device ever sees the other's position.

The Kobo half is a missing producer, not a broken write path. KOReader pulls
from ``KOSyncProgress``; the Kobo PUT handler wrote ``KoboBookmark`` only, so
there was no row for the device to fetch. #1366 fixed the identical shape for
the web reader and left the helper behind for this case.

What crosses is the percentage, not the position. A Kobo reports a ``KoboSpan``
addressing the kepub that device holds, which KOReader's engine cannot resolve,
so the row is percentage-only and is withheld from any client that has not
advertised ``?position_kinds=percentage`` — an already-installed plugin guards
only on ``body.progress == nil`` and in Lua ``"" ~= nil``, so a sentinel would
otherwise reach ``GotoXPointer``.

Pinned here:
  * a Kobo sync creates the row KOReader fetches, served as a percentage;
  * an already-installed plugin still sees nothing (unchanged behaviour);
  * furthest-wins, so a stale Kobo cannot drag KOReader backwards;
  * a Kobo that has genuinely moved ahead replaces KOReader's stale locator;
  * the call site exists in the PUT handler and is guarded, because the bug
    was a call that was never made;
  * the write is best-effort — a carrier failure cannot fail the device's sync.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps.progress_syncing.models import AppBase, KOSyncProgress

BOOK_ID = 42
USER_ID = 1


def _kosync_module():
    import sys
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    return sys.modules["cps.progress_syncing.protocols.kosync"]


@pytest.fixture
def protocol(monkeypatch):
    """A real kosync HTTP surface over a real in-memory progress table."""
    module = _kosync_module()
    engine = create_engine("sqlite:///:memory:")
    AppBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    monkeypatch.setattr(module, "ub", MagicMock(session=session))
    monkeypatch.setattr(module, "is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(module, "authenticate_user", lambda: SimpleNamespace(id=USER_ID))
    monkeypatch.setattr(module, "update_book_read_status", lambda *_a: None)
    monkeypatch.setattr(module, "push_reading_state_to_hardcover", lambda *_a: None)
    monkeypatch.setattr(module, "get_book_checksums",
                        lambda book_id: ["digest-a"] if book_id else [])
    monkeypatch.setattr(module, "enrich_response_with_book_info",
                        lambda response, document: (response, BOOK_ID, "EPUB", "Fixture", "koreader"))

    app = Flask(__name__)
    app.register_blueprint(module.kosync)
    yield module, app.test_client(), session
    session.close()


def _kobo_syncs(module, percent):
    """What a Kobo state PUT now does to the shared carrier."""
    module.record_percentage_only_progress(USER_ID, BOOK_ID, percent, device="Kobo")
    module.ub.session.commit()


def _koreader_pushes(client, percent, device="Crosspoint"):
    return client.put("/kosync/syncs/progress", json={
        "document": "digest-a",
        "progress": "/body/DocFragment[12]/body/div/p[3].0",
        "percentage": percent,
        "device": device,
        "device_id": device,
    })


def _pull(client, advertises_percentage):
    """GET as a new plugin (advertises) or an already-installed one (does not)."""
    url = "/kosync/syncs/progress/digest-a"
    if advertises_percentage:
        url += "?position_kinds=locator,percentage"
    return client.get(url)


# ── the fix ──────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_kobo_sync_reaches_koreader(protocol):
    """The reported symptom: read on the Kobo, KOReader can now fetch it.

    Fails before the fix because the Kobo PUT handler never wrote a
    ``KOSyncProgress`` row, so the pull returned an empty body and the
    KOReader device carried on from where it last was.
    """
    module, client, session = protocol

    _kobo_syncs(module, 63.5)

    body = _pull(client, advertises_percentage=True).get_json()
    assert body["percentage"] == pytest.approx(0.635), "KOReader must see the Kobo's position"
    assert body["position_kind"] == "percentage"
    assert body["progress"] is None, "a KoboSpan is not a position KOReader can seek to"
    assert body["device"] == "Kobo"


@pytest.mark.unit
def test_kobo_row_is_keyed_on_book_id(protocol):
    """One row per book, on the key ``update_progress`` converges to (#633).

    Keyed on a checksum, the Kobo's row and the device's row would be two
    records and furthest-wins would never compare them.
    """
    module, client, session = protocol

    _kobo_syncs(module, 63.5)

    stored = session.query(KOSyncProgress).one()
    assert stored.document == str(BOOK_ID)
    assert stored.progress == module.PERCENTAGE_ONLY_LOCATOR


@pytest.mark.unit
def test_already_installed_plugin_is_served_nothing(protocol):
    """Unchanged behaviour for a client that has not advertised support.

    Its only guard is ``body.progress == nil``; in Lua ``"" ~= nil``, so a
    sentinel would flow into ``GotoXPointer`` and be stored as the document's
    ``last_xpointer``.
    """
    module, client, session = protocol

    _kobo_syncs(module, 63.5)

    body = _pull(client, advertises_percentage=False).get_json()
    assert not body.get("progress"), "an old plugin must not receive the sentinel"
    assert not body.get("percentage"), "nor the percentage that goes with it"


@pytest.mark.unit
def test_stale_kobo_does_not_drag_koreader_backwards(protocol):
    """Furthest-wins. A Kobo reporting an older position keeps its hands off.

    A Kobo pushes state on connect, so a device that has been asleep can report
    a position days behind where the user actually is.
    """
    module, client, session = protocol

    _koreader_pushes(client, 0.80)
    _kobo_syncs(module, 20.0)

    body = _pull(client, advertises_percentage=True).get_json()
    assert body["percentage"] == pytest.approx(0.80), "KOReader must keep its own further position"
    assert body["progress"], "and must keep the seekable locator that came with it"


@pytest.mark.unit
def test_kobo_that_moved_ahead_replaces_a_stale_locator(protocol):
    """The other direction: the Kobo is genuinely ahead, so it wins.

    The stored locator described an earlier position, so handing it back would
    send the device behind where the user is. Trading it for a percentage that
    is correct is the honest outcome.
    """
    module, client, session = protocol

    _koreader_pushes(client, 0.20)
    _kobo_syncs(module, 80.0)

    body = _pull(client, advertises_percentage=True).get_json()
    assert body["percentage"] == pytest.approx(0.80)
    assert body["position_kind"] == "percentage"
    assert body["progress"] is None
    assert session.query(KOSyncProgress).count() == 1, "one row per book, not a fork"


# ── the real PUT handler, end to end over one session ───────────────────────

@pytest.fixture
def kobo_put(monkeypatch):
    """The real ``HandleStateRequest`` PUT path over a real progress table.

    Both modules are pointed at the SAME session: ``cps.kobo`` opens the
    savepoint, ``kosync`` writes the row inside it.
    """
    import cps.kobo as kobo_module
    kosync = _kosync_module()

    engine = create_engine("sqlite:///:memory:")
    AppBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    bookmark = SimpleNamespace(progress_percent=None, content_source_progress_percent=None,
                               location_value=None, location_type=None, location_source=None,
                               last_modified=None)
    reading_state = SimpleNamespace(
        current_bookmark=bookmark,
        statistics=SimpleNamespace(spent_reading_minutes=None, remaining_time_minutes=None,
                                   last_modified=None),
        book_read_link=SimpleNamespace(read_status=0, times_started_reading=0,
                                       last_time_started_reading=None, last_modified=None),
    )
    book = SimpleNamespace(id=BOOK_ID, data=[SimpleNamespace(format="KEPUB")])

    class _SessionProxy:
        """The real session, minus ``merge`` — the reading-state graph is a stub.

        ``HandleStateRequest`` merges the ORM graph it was handed; that graph is
        faked here so the test can stay at unit scope. Everything the fix uses
        (flush, begin_nested, add, query) is the real session, and it is the
        same object ``kosync`` writes through, so the savepoint is genuine.
        """

        def merge(self, obj):
            return obj

        def __getattr__(self, name):
            return getattr(session, name)

    kobo_ub = MagicMock(session=_SessionProxy())
    kobo_ub.session_flush = lambda *a, **k: session.flush()
    kobo_ub.session_commit = lambda *a, **k: session.commit()
    kobo_ub.ReadBook = SimpleNamespace(STATUS_UNREAD=0, STATUS_IN_PROGRESS=1, STATUS_FINISHED=2)

    monkeypatch.setattr(kobo_module, "ub", kobo_ub)
    monkeypatch.setattr(kobo_module, "calibre_db",
                        SimpleNamespace(get_book_by_uuid_for_kobo=lambda *a, **k: book))
    monkeypatch.setattr(kobo_module, "get_or_create_reading_state", lambda _bid: reading_state)
    monkeypatch.setattr(kobo_module, "current_user", SimpleNamespace(id=USER_ID))
    monkeypatch.setattr(kobo_module, "push_reading_state_to_hardcover", lambda *a, **k: None)
    monkeypatch.setattr(kobo_module, "get_ub_read_status", lambda _s: 1)

    monkeypatch.setattr(kosync, "ub", MagicMock(session=session))
    monkeypatch.setattr(kosync, "get_book_checksums", lambda _bid: [])

    app = Flask(__name__)
    yield kobo_module, app, session
    session.close()


def _handler(kobo_module):
    """``HandleStateRequest`` without its ``requires_kobo_auth`` wrapper.

    The device's auth handshake is covered elsewhere and needs a real token
    row; what is under test here is what the handler does once it has run.
    """
    return kobo_module.HandleStateRequest.__wrapped__


def _state_put_payload(percent):
    """What a Kobo actually PUTs: a percentage plus a span it alone can resolve."""
    return {"ReadingStates": [{
        "LastModified": "2026-08-14T06:00:00Z",
        "CurrentBookmark": {
            "ProgressPercent": percent,
            "ContentSourceProgressPercent": percent,
            "Location": {"Value": "kobo.6.1", "Type": "KoboSpan", "Source": "kepub"},
        },
        "Statistics": None,
        "StatusInfo": None,
    }]}


@pytest.mark.unit
def test_real_kobo_state_put_writes_the_koreader_carrier(kobo_put):
    """The user-visible flow: a device PUTs its state, KOReader gains a row.

    RED before the fix — the handler wrote ``KoboBookmark`` and returned
    success, leaving ``KOSyncProgress`` empty, which is why the Crosspoint
    device in #324 never moved.
    """
    kobo_module, app, session = kobo_put

    with app.test_request_context(json=_state_put_payload(63.5), method="PUT"):
        response = _handler(kobo_module)("uuid-under-test")

    assert response.get_json()["RequestResult"] == "Success"

    row = session.query(KOSyncProgress).one()
    assert row.percentage == pytest.approx(63.5)
    assert row.device == "Kobo"
    assert row.document == str(BOOK_ID)


@pytest.mark.unit
def test_bookmark_only_put_still_records_the_devices_own_state(kobo_put):
    """The Kobo's own write is untouched by the addition."""
    kobo_module, app, session = kobo_put

    with app.test_request_context(json=_state_put_payload(63.5), method="PUT"):
        _handler(kobo_module)("uuid-under-test")

    bookmark = kobo_module.get_or_create_reading_state(BOOK_ID).current_bookmark
    assert bookmark.progress_percent == 63.5
    assert bookmark.location_value == "kobo.6.1", "the KoboSpan the device seeks to is still stored"


# ── the call site, because the bug was a call that was never made ────────────

@pytest.mark.unit
def test_put_handler_shares_progress_with_koreader():
    """``HandleStateRequest`` must actually call the producer.

    Everything above passes on the pre-fix tree if the handler never calls in
    — which is precisely what the bug was.
    """
    from cps.kobo import HandleStateRequest
    src = inspect.getsource(HandleStateRequest)
    assert "share_kobo_progress_with_koreader(" in src, (
        "the Kobo PUT handler must publish the position onto the KOSync "
        "carrier, or KOReader has nothing to fetch (#1425 gap 1)"
    )


@pytest.mark.unit
def test_share_call_is_guarded_by_a_present_progress_percent():
    """Devices legitimately PUT Statistics-only and StatusInfo-only payloads.

    Same guard ``push_reading_state_to_hardcover`` needs: unguarded, a payload
    without ``CurrentBookmark`` raises and the device retries forever.
    """
    from cps.kobo import HandleStateRequest
    src = inspect.getsource(HandleStateRequest)
    guard = 'if request_bookmark and request_bookmark.get("ProgressPercent") is not None:'
    assert guard in src
    guard_at = src.index(guard)
    call_at = src.index("share_kobo_progress_with_koreader(")
    assert call_at > guard_at, "the share call must sit inside the ProgressPercent guard"


@pytest.mark.unit
def test_carrier_failure_cannot_fail_the_devices_sync(monkeypatch):
    """Best-effort. The Kobo's own sync is the required write.

    If publishing to the KOSync carrier raises, the device must still get its
    success response — otherwise a KOReader-side problem breaks Kobo syncing,
    which is a strictly worse outcome than the two devices disagreeing.
    """
    import cps.kobo as kobo_module

    monkeypatch.setattr(kobo_module.ub, "session_flush", lambda *a, **k: None)
    monkeypatch.setattr(kobo_module.ub, "session",
                        SimpleNamespace(begin_nested=MagicMock(side_effect=RuntimeError("boom"))))

    # Must not raise.
    kobo_module.share_kobo_progress_with_koreader(USER_ID, BOOK_ID, 50.0)


@pytest.mark.unit
def test_settles_pending_writes_before_opening_the_savepoint():
    """A SAVEPOINT only contains what is flushed after it.

    Without the flush, a rollback of this best-effort write would also discard
    the Kobo's own bookmark, statistics and status updates from the same PUT.
    """
    from cps.kobo import share_kobo_progress_with_koreader
    src = inspect.getsource(share_kobo_progress_with_koreader)
    assert "session_flush()" in src
    assert src.index("session_flush()") < src.index("begin_nested()")
