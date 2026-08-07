# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#1366 — reading in the web reader must reach KOReader.

v4.1.29 shipped the browser -> Kobo direction by mirroring a percentage into
``KoboBookmark.progress_percent``.  KOReader does not read that field.  It pulls
from ``KOSyncProgress`` over the kosync protocol, and the only writer of that
table was KOReader's own PUT — so a book read in the browser left nothing for
KOReader to fetch and the device carried on from where *it* last was.

The carrier is now written by the browser too.  What it cannot write is a
position: KOReader consumes ``KOSyncProgress.progress`` as an engine-private
crengine xpointer (or a page number when numeric), and the web reader's position
is an EPUB CFI.  So the row is stored with an explicit percentage-only sentinel
and served as ``position_kind: "percentage"``, which the plugin acts on with
``GotoPercent``.

The compatibility problem this has to solve, and what most of these tests pin:
an already-installed plugin cannot be sent one of these rows.  Its only guard is
``body.progress == nil``, and in Lua ``"" ~= nil``, so anything non-null flows
into ``GotoXPointer`` and is stored as the document's ``last_xpointer``.  A null
``progress`` would clear that hazard but makes the plugin report a sync error
where it used to say "no progress found".  So the rows are withheld entirely
from any client that has not advertised percentage support, and the client
advertises it with ``?position_kinds=`` on the GET.

Pinned here:
  * the browser's save creates the row KOReader fetches;
  * a client that has not advertised support never sees it (unchanged behaviour);
  * a client that has sees ``progress: null`` + ``position_kind: percentage``;
  * furthest-wins, so opening the browser cannot drag a device backwards;
  * a later KOReader push upgrades the shared row to a real locator instead of
    forking a second one beside it, and old clients see it again once it has.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
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


def _web_reader_saves(module, percent):
    """What the browser's bookmark save now does to the shared carrier."""
    module.record_percentage_only_progress(USER_ID, BOOK_ID, percent, device="Web reader")
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
def test_web_reader_save_reaches_koreader(protocol):
    """The reported symptom: read in the browser, KOReader can now fetch it.

    Fails before the fix because nothing but KOReader's own PUT ever wrote a
    ``KOSyncProgress`` row, so the pull returned an empty body.
    """
    module, client, session = protocol

    _web_reader_saves(module, 45.67)

    body = _pull(client, advertises_percentage=True).get_json()
    assert body["percentage"] == pytest.approx(0.4567), "KOReader must see the browser's position"
    assert body["position_kind"] == "percentage"
    assert body["progress"] is None, "a CFI is not a position KOReader can seek to"
    assert body["device"] == "Web reader"


@pytest.mark.unit
def test_row_is_keyed_on_book_id_not_a_checksum(protocol):
    """One row per book, on the key ``update_progress`` converges to (#633).

    Keyed on a checksum instead, the browser's row and the device's row would be
    two separate records and the furthest-wins comparison would never see both.
    """
    module, client, session = protocol

    _web_reader_saves(module, 45.67)

    stored = session.query(KOSyncProgress).one()
    assert stored.document == str(BOOK_ID)
    assert stored.progress == module.PERCENTAGE_ONLY_LOCATOR


# ── compatibility with plugins already installed on devices ──────────────────

@pytest.mark.unit
def test_older_plugin_never_receives_a_percentage_only_row(protocol):
    """The safety property. An unadvertised client must see exactly what it saw
    before these rows existed: nothing.

    If it received the row, its ``body.progress == nil`` guard would either miss
    a non-null sentinel and store it as ``last_xpointer``, or catch a null one
    and report a sync error where it used to say "no progress found".
    """
    module, client, session = protocol

    _web_reader_saves(module, 45.67)

    assert _pull(client, advertises_percentage=False).get_json() == {}


@pytest.mark.unit
def test_older_plugin_still_receives_a_real_locator(protocol):
    """The withholding is scoped to percentage-only rows, not to the endpoint."""
    module, client, session = protocol

    assert _koreader_pushes(client, 0.30).status_code == 200

    body = _pull(client, advertises_percentage=False).get_json()
    assert body["progress"] == "/body/DocFragment[12]/body/div/p[3].0"
    assert body["position_kind"] == "locator"


# ── conflict handling ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_browser_behind_a_device_does_not_drag_it_backwards(protocol):
    """Opening a book in the browser must not undo a device's real progress."""
    module, client, session = protocol

    assert _koreader_pushes(client, 0.80).status_code == 200
    _web_reader_saves(module, 30.0)

    stored = session.query(KOSyncProgress).one()
    assert stored.percentage == pytest.approx(80.0)
    assert stored.progress == "/body/DocFragment[12]/body/div/p[3].0", \
        "the device's locator survives a lower browser sample"


@pytest.mark.unit
def test_browser_ahead_replaces_a_stale_locator(protocol):
    """When the browser IS further, the stored locator describes an earlier
    position — serving it would send the device behind where the user is."""
    module, client, session = protocol

    assert _koreader_pushes(client, 0.30).status_code == 200
    _web_reader_saves(module, 75.0)

    stored = session.query(KOSyncProgress).one()
    assert stored.percentage == pytest.approx(75.0)
    assert stored.progress == module.PERCENTAGE_ONLY_LOCATOR
    assert _pull(client, advertises_percentage=False).get_json() == {}, \
        "and it is now percentage-only, so it is withheld again"


@pytest.mark.unit
def test_koreader_push_upgrades_the_shared_row(protocol):
    """Convergence: the device's own push restores a seekable locator in place.

    A second row here would mean the two carriers had permanently forked.
    """
    module, client, session = protocol

    _web_reader_saves(module, 45.67)
    assert _koreader_pushes(client, 0.60).status_code == 200

    stored = session.query(KOSyncProgress).one()
    assert stored.percentage == pytest.approx(60.0)
    assert stored.progress == "/body/DocFragment[12]/body/div/p[3].0"

    body = _pull(client, advertises_percentage=False).get_json()
    assert body["position_kind"] == "locator", "old plugins get positions again"


# ── the browser route is actually wired to the writer ────────────────────────

@pytest.mark.unit
def test_record_web_reader_progress_writes_the_kosync_row(monkeypatch):
    """A writer no caller reaches is not a fix. This drives the real service
    entry point the two bookmark routes call."""
    from cps.services import reading_position

    module = _kosync_module()
    engine = create_engine("sqlite:///:memory:")
    AppBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    # Both modules resolve the session off the shared ``ub`` module object.
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(module, "update_book_read_status", lambda *_a: None)

    advanced = reading_position.record_web_reader_progress(
        SimpleNamespace(id=USER_ID), BOOK_ID, 45.67)
    session.commit()

    assert advanced is True
    stored = session.query(KOSyncProgress).one()
    assert stored.document == str(BOOK_ID)
    assert stored.percentage == pytest.approx(45.67)
    assert stored.progress == module.PERCENTAGE_ONLY_LOCATOR
    assert stored.device == "Web reader"

    session.close()


@pytest.mark.unit
def test_equal_percentage_does_not_destroy_a_real_locator(protocol):
    """The browser opens the book AT the last synced position.

    Saving there without moving produces a percentage exactly equal to the
    stored one. Treating equality as a win replaces the device's real
    xpointer with the sentinel, and an already-installed plugin — which is
    served only locator rows — then gets no row at all. Strictly-further
    wins; equal does not.
    """
    module, client, session = protocol

    assert _koreader_pushes(client, 0.50).status_code == 200
    _web_reader_saves(module, 50.0)

    stored = session.query(KOSyncProgress).one()
    assert stored.progress == "/body/DocFragment[12]/body/div/p[3].0", \
        "an equal browser sample must not overwrite the device's locator"

    # The consequence the user actually feels: an installed plugin still syncs.
    assert _pull(client, advertises_percentage=False).get_json()["progress"] == \
        "/body/DocFragment[12]/body/div/p[3].0"


@pytest.mark.unit
def test_a_client_cannot_push_the_sentinel_as_its_own_locator(protocol):
    """``progress`` is client-controlled, and the sentinel's meaning is ours.

    ``is_percentage_only`` classifies a row by equality with
    ``PERCENTAGE_ONLY_LOCATOR`` alone, so a client that pushed that exact string
    through the ordinary locator path would have its row silently reclassified:
    served to capable clients as ``progress: null``, withheld from every plugin
    that does not advertise, and skipped by bulk pull -- while the client
    believed it had stored a position. Reserving the value at the one boundary
    that accepts locators is what keeps the classification unambiguous.
    """
    module, client, session = protocol

    response = client.put("/kosync/syncs/progress", json={
        "document": "digest-a",
        "progress": module.PERCENTAGE_ONLY_LOCATOR,
        "percentage": 0.5,
        "device": "Impostor",
        "device_id": "impostor-1",
    })

    assert response.status_code != 200, "the reserved sentinel must be refused"
    assert session.query(module.KOSyncProgress).count() == 0, (
        "no row may be created from a reserved-value push"
    )


@pytest.mark.unit
def test_a_real_locator_push_is_unaffected_by_the_reservation(protocol):
    """The guard rejects one exact value, not locators that merely resemble it."""
    module, client, session = protocol

    for locator in ("cwng:percentage-ish", "/body/DocFragment[2]/body/p[1].0", "42"):
        response = client.put("/kosync/syncs/progress", json={
            "document": "digest-a",
            "progress": locator,
            "percentage": 0.5,
            "device": "Crosspoint",
            "device_id": "crosspoint-1",
        })
        assert response.status_code == 200, f"{locator!r} is a legitimate locator"
