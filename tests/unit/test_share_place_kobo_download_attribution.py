# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact-file proof only for a download we can attribute without guessing.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 16 item 4
(a blocker on the PR 5 flip). Nickel's file GET carries no ``x-kobo-*``
headers, so ``record_download`` attributes it heuristically: the armed restore
row, else the most recently seen active Kobo. On an account with two Kobos,
Kobo A's download could credit Kobo B with the current ``kepub_file_identity``
and B would then be served exact spans for a file it does not hold.

Rule: record proof (the identity, and ``confirm_download`` success) only when
attribution is unambiguous: a device header was present, an armed row named
the device, or the account has exactly one active Kobo. Otherwise store ``""``
(modern, unproven) so the receiver takes the guarded inexact path. Annotation
restore is unchanged.

Each case drives the real ``helper.get_download_link`` through gevent's
pywsgi with ``MyWSGIHandler`` on a local socket, as
``test_share_place_kobo_download_proof.py`` does, then asks the real Kobo
GET /state what each Kobo is served. A Kobo with proof gets the exact span of
the shared place; one without gets its own Location with the new percentage.
"""

from datetime import datetime, timedelta, timezone

import pytest
from flask import g, send_file

from cps import ub
from cps.services import kepub_alignment
from tests.unit.share_place_span_world import (  # noqa: F401  (fixture)
    _now, location_of, span_location, span_rows, sw,
)

pytestmark = pytest.mark.unit

SWITCH = "CWNG_SHARE_PLACE_GATES"


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    monkeypatch.setenv(SWITCH, "1")


@pytest.fixture
def rows():
    found = span_rows()
    return {"a-own": found[5], "b-own": found[9], "late": found[300]}


def _stale(sw, device, own):
    """``device`` holds a KEPUB from before the library one was written and
    last reported its own span at 5%."""
    sw.download(device, at=sw.kepub_written() - timedelta(days=1))
    sw.put(device, own["source"], own["span"], 5, _now() - timedelta(hours=1))
    sw.settle(device)
    return span_location(own["source"], own["span"])


def _household(sw, rows, *, kobos):
    """A checked KOReader shares a place at 40%; ``kobos`` checked Kobos each
    hold a stale KEPUB. Kobo B (when present) was seen most recently, so a
    header-less download is attributed to it by the heuristic."""
    sw.koreader_device = sw.koreader("koreader-1", "KOReader", checked=True)
    sw.a = sw.kobo_device("Kobo A", checked=True)
    sw.a_own = _stale(sw, sw.a, rows["a-own"])
    sw.b = None
    if kobos == 2:
        sw.b = sw.kobo_device("Kobo B", checked=True)
        sw.b_own = _stale(sw, sw.b, rows["b-own"])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    sw.session.query(ub.Device).filter_by(id=sw.a.id).update(
        {"last_seen_at": now - timedelta(hours=2)})
    if sw.b is not None:
        sw.session.query(ub.Device).filter_by(id=sw.b.id).update(
            {"last_seen_at": now - timedelta(minutes=1)})
    sw.session.commit()
    sw.push(rows["late"]["xpointer"], 40)
    found = kepub_alignment.xpointer_to_span(sw.epub, sw.kepub, rows["late"]["xpointer"])
    assert found == (rows["late"]["source"], rows["late"]["span"])
    sw.exact = span_location(*found)
    assert location_of(_bookmark(sw, sw.a)) == sw.a_own  # not yet proven
    if sw.b is not None:
        assert location_of(_bookmark(sw, sw.b)) == sw.b_own
    return sw


@pytest.fixture
def two_kobos(sw, rows):
    return _household(sw, rows, kobos=2)


@pytest.fixture
def one_kobo(sw, rows):
    return _household(sw, rows, kobos=1)


def _bookmark(sw, device):
    return sw.get(device).get("CurrentBookmark") or {}


def _download(monkeypatch, sw, *, header_device=None):
    """Kobo A downloads the current KEPUB in full through the production
    server. ``header_device`` is the device the request's identity headers
    resolved to; None is Nickel's real header-less file GET."""
    import http.client
    import gevent.socket
    from gevent.pywsgi import WSGIServer
    from cps import helper
    from cps.gevent_wsgi import MyWSGIHandler

    book = sw.book
    fake_user = type("User", (), {"id": sw.reader.id, "name": "reader",
                                  "is_authenticated": True, "is_anonymous": False,
                                  "role_admin": lambda self: False})()
    monkeypatch.setattr(helper, "current_user", fake_user)
    monkeypatch.setattr(helper.calibre_db, "get_filtered_book", lambda *_a, **_k: book)
    monkeypatch.setattr(helper.calibre_db, "get_book_format",
                        lambda *_a: next(d for d in book.data if d.format == "KEPUB"))
    monkeypatch.setattr(helper.calibre_db, "get_book", lambda *_a: book)
    monkeypatch.setattr(helper.ub, "update_download", lambda *_a: None)
    monkeypatch.setattr(helper, "get_valid_filename", lambda name, **_k: name)
    monkeypatch.setattr(helper, "CWA_DB", lambda: type(
        "C", (), {"log_activity": lambda *_a, **_k: None})())
    monkeypatch.setattr(helper, "do_download_file",
                        lambda *_a, **_k: send_file(sw.kepub, conditional=True))
    origin = None if header_device is None else header_device.id

    def application(environ, start_response):
        with sw.app.request_context(environ):
            g.annotation_origin_device_id = origin
            response = helper.get_download_link(book.id, "kepub", "kobo")
        return response(environ, start_response)

    server = WSGIServer(("127.0.0.1", 0), application, handler_class=MyWSGIHandler,
                        log=None, error_log=None)
    server.start()
    try:
        sock = gevent.socket.create_connection(("127.0.0.1", server.server_port))
        sock.sendall((f"GET /kobo/download/{book.id}/kepub HTTP/1.1\r\n"
                      "Host: localhost\r\nConnection: close\r\n\r\n").encode())
        reply = http.client.HTTPResponse(sock, method="GET")
        reply.begin()
        body = reply.read()
        sock.close()
    finally:
        server.stop(timeout=5)
    with open(sw.kepub, "rb") as handle:
        assert reply.status == 200 and body == handle.read()
    sw.session.expire_all()


def _ledger(sw, device):
    return sw.session.query(ub.KoboDeviceBookDownload).filter_by(
        device_id=device.id, book_id=sw.book.id).one()


def _served(sw, device):
    bookmark = _bookmark(sw, device)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    return location_of(bookmark)


# == Two Kobos, heuristic attribution: no proof ===============================

def test_two_kobos_a_headerless_download_proves_nothing(monkeypatch, two_kobos):
    """A downloads; the heuristic credits B (seen most recently). B must not be
    served an exact span for a file it does not hold, and A, which did hold
    the download, is not credited either: the guess is unproven."""
    sw = two_kobos
    _download(monkeypatch, sw)

    assert _ledger(sw, sw.b).kepub_file_identity == ""
    assert _served(sw, sw.b) == sw.b_own
    assert _served(sw, sw.a) == sw.a_own
    assert sw.synced_location(sw.b) != sw.exact


def test_two_kobos_a_headerless_download_still_stages_the_annotation_restore(
        monkeypatch, two_kobos):
    """Control: annotation restore is unchanged by the proof rule. The
    heuristic still arms the post-download restore on the attributed Kobo."""
    sw = two_kobos
    _download(monkeypatch, sw)

    row = _ledger(sw, sw.b)
    assert row.restore_state == "pending"
    assert row.downloaded_at is not None
    assert row.book_format == "kepub"


# == Unambiguous attribution: proof is recorded ===============================

def test_a_single_active_kobo_headerless_download_is_proof(monkeypatch, one_kobo):
    sw = one_kobo
    _download(monkeypatch, sw)

    assert _ledger(sw, sw.a).kepub_file_identity
    assert _served(sw, sw.a) == sw.exact


def test_two_kobos_a_download_with_device_headers_is_proof_for_that_kobo(
        monkeypatch, two_kobos):
    sw = two_kobos
    _download(monkeypatch, sw, header_device=sw.a)

    assert _served(sw, sw.a) == sw.exact
    assert _served(sw, sw.b) == sw.b_own


def test_two_kobos_an_armed_row_sends_proof_to_the_armed_kobo_only(monkeypatch, two_kobos):
    """A sync re-sent the book to A (its restore row is armed), so a
    header-less download is A's even though B was seen more recently."""
    from cps.services.kobo_post_download_restore import arm_pending_restore
    import logging
    sw = two_kobos
    _ledger(sw, sw.a).restore_state = "served"  # A's earlier restore was answered
    sw.session.commit()
    assert arm_pending_restore(device_id=sw.a.id, book_ids=[sw.book.id],
                               log=logging.getLogger("test")) == 1
    sw.session.commit()
    assert _ledger(sw, sw.a).restore_state == "armed"

    _download(monkeypatch, sw)

    assert _served(sw, sw.a) == sw.exact
    assert _served(sw, sw.b) == sw.b_own
    assert _ledger(sw, sw.b).kepub_file_identity in (None, "")


# == Section 17a.2: who counts as a candidate, and which arms name a device ===
# Candidates are the account's Kobos seen in the last 30 days, active or not.
# An armed row names the device only when it was armed in the last 15 minutes.
# Restore attribution is unchanged.

def _age(sw, device, **delta):
    sw.session.query(ub.Device).filter_by(id=device.id).update(
        {"last_seen_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(**delta)})
    sw.session.commit()


def test_a_thirty_day_old_arm_does_not_name_the_downloader(monkeypatch, two_kobos):
    sw = two_kobos
    stale = _ledger(sw, sw.a)
    stale.restore_state = "armed"
    stale.downloaded_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    sw.session.commit()

    _download(monkeypatch, sw)

    row = _ledger(sw, sw.a)
    assert row.restore_state == "pending", "restore still follows the armed row"
    assert not row.kepub_file_identity
    assert _served(sw, sw.a) == sw.a_own
    assert _served(sw, sw.b) == sw.b_own


def test_an_inactive_but_recently_seen_second_kobo_still_makes_it_ambiguous(
        monkeypatch, two_kobos):
    sw = two_kobos
    sw.session.query(ub.Device).filter_by(id=sw.b.id).update({"active": False})
    sw.session.commit()

    _download(monkeypatch, sw)

    row = _ledger(sw, sw.a)
    assert row.restore_state == "pending", "restore goes to the one active Kobo, as today"
    assert row.kepub_file_identity == ""
    assert _served(sw, sw.a) == sw.a_own


def test_a_kobo_not_seen_for_over_thirty_days_is_not_a_candidate(monkeypatch, two_kobos):
    """B is registered and active but silent for 31 days; A is the one Kobo
    that could have downloaded, so its download is proof."""
    sw = two_kobos
    _age(sw, sw.b, days=31)

    _download(monkeypatch, sw)

    assert _ledger(sw, sw.a).kepub_file_identity
    assert _served(sw, sw.a) == sw.exact
    assert _served(sw, sw.b) == sw.b_own
