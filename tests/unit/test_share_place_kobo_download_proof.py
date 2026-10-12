# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Only a finished download proves a Kobo holds the current KEPUB.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 14c,
item 2 ("completion point"). Proof (``completed_at`` plus
``kepub_file_identity`` on ``kobo_device_book_download``) is set only when a
non-HEAD GET with no Range returns 200 and its body has streamed to the end
without error. A failed stream leaves the attempt unproven.

Review 2 (OBSERVED) found proof was set when the response was built, before
the body was sent: a HEAD, a ``Range: bytes=0-0`` 206 and a 200 whose stream
dies after its first chunk each made a Kobo still holding an older KEPUB
"hold" the current one, so it was sent a span it cannot resolve.

Each case drives the real ``helper.get_download_link`` for a checked Kobo
that holds a stale KEPUB, consumes the body the way a WSGI server does (after
the view has returned, outside the request context, then ``close()``), and
then asks the real Kobo GET /state what that Kobo is now served. Every case
drains the body, even HEAD and 206, so the method and status clauses of the
rule are each tested on their own rather than through "nothing was read".

The post-download annotation restore (case 5 of the brief) is already
covered through the same route and is not duplicated here:
``tests/unit/test_kobo_redownload_harmless.py::
test_download_route_records_a_pending_restore_for_the_device`` (the route
stages ``pending`` without the body being read) and
``::test_first_get_after_download_is_answered_from_local_rows`` (pending is
served and settled).
"""

from datetime import timedelta

import pytest
from flask import Response, g, send_file

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
    return {"kobo-own": found[5], "late": found[300]}


@pytest.fixture
def stale_kobo(sw, rows):
    """A checked KOReader has shared a place at 40%; a checked Kobo that holds
    a KEPUB from before the library one was written last reported its own
    span at 5%. Until it finishes downloading the current KEPUB, it is
    served its own Location with the new percentage."""
    sw.koreader_device = sw.koreader("koreader-1", "KOReader", checked=True)
    sw.checked = sw.kobo_device("Kobo Checked", checked=True)
    sw.download(sw.checked, at=sw.kepub_written() - timedelta(days=1))
    own = rows["kobo-own"]
    sw.put(sw.checked, own["source"], own["span"], 5, _now() - timedelta(hours=1))
    sw.settle(sw.checked)
    sw.push(rows["late"]["xpointer"], 40)
    sw.own = span_location(own["source"], own["span"])
    found = kepub_alignment.xpointer_to_span(sw.epub, sw.kepub, rows["late"]["xpointer"])
    assert found == (rows["late"]["source"], rows["late"]["span"])
    sw.exact = span_location(*found)
    assert location_of(_bookmark(sw)) == sw.own  # not yet proven
    return sw


def _bookmark(sw):
    return sw.get(sw.checked).get("CurrentBookmark") or {}


def _download(monkeypatch, sw, method="GET", headers=None, broken=False):
    """The Kobo's download request through the real route; returns the
    response the view handed to the server, body not yet read."""
    from cps import helper
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
    if broken:
        def do_download(*_a, **_k):
            def body():
                yield b"first chunk"
                raise OSError("simulated stream failure")
            return Response(body(), status=200)
    else:
        def do_download(*_a, **_k):
            return send_file(sw.kepub, conditional=True)
    monkeypatch.setattr(helper, "do_download_file", do_download)
    with sw.app.test_request_context(f"/kobo/download/{book.id}/kepub",
                                     method=method, headers=headers or {}):
        g.annotation_origin_device_id = sw.checked.id
        response = helper.get_download_link(book.id, "kepub", "kobo")
    sw.session.expire_all()
    return response


def _drain(response):
    """Send the body as a WSGI server would: iterate it to the end, then
    close it whether or not the iteration failed. Returns the bytes sent and
    the error that ended the stream, if any."""
    sent, error = [], None
    try:
        for chunk in response.response:
            sent.append(chunk)
    except OSError as exc:
        error = exc
    finally:
        response.close()
    return b"".join(sent), error


# == Requests that do not deliver the whole current KEPUB set no proof ========

def test_a_head_download_does_not_prove_the_kobo_holds_the_current_kepub(
        monkeypatch, stale_kobo):
    sw = stale_kobo
    response = _download(monkeypatch, sw, method="HEAD")
    assert response.status_code == 200
    _drain(response)

    bookmark = _bookmark(sw)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == sw.own


def test_a_range_download_does_not_prove_the_kobo_holds_the_current_kepub(
        monkeypatch, stale_kobo):
    sw = stale_kobo
    response = _download(monkeypatch, sw, headers={"Range": "bytes=0-0"})
    assert response.status_code == 206
    sent, error = _drain(response)
    assert error is None and len(sent) == 1  # the whole (one-byte) range was sent

    bookmark = _bookmark(sw)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == sw.own


def test_a_download_whose_stream_fails_partway_does_not_prove_the_current_kepub(
        monkeypatch, stale_kobo):
    sw = stale_kobo
    response = _download(monkeypatch, sw, broken=True)
    assert response.status_code == 200
    sent, error = _drain(response)
    assert sent == b"first chunk"
    assert isinstance(error, OSError)

    bookmark = _bookmark(sw)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == sw.own


# == Control: a full download that is read to the end is proof ================

def test_a_full_download_read_to_the_end_delivers_the_exact_span(monkeypatch, stale_kobo):
    sw = stale_kobo
    response = _download(monkeypatch, sw)
    assert response.status_code == 200
    sent, error = _drain(response)
    assert error is None
    with open(sw.kepub, "rb") as handle:
        assert sent == handle.read()

    bookmark = _bookmark(sw)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == sw.exact
    assert sw.synced_location(sw.checked) == sw.exact
