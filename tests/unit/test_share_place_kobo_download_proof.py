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
that holds a stale KEPUB through a real server: gevent's pywsgi with
``MyWSGIHandler`` on a local socket (production), or the Tornado fallback's
``MyWSGIContainer.handle_request`` with a fake connection. The view runs in a
request context that is popped before the server reads the body, as Flask
does. It then asks the real Kobo GET /state what that Kobo is now served.

Later rules from the same item: proof also needs the declared
``Content-Length`` (greater than zero) actually yielded and no
X-Sendfile/X-Accel-Redirect hand-off; and only gevent can prove, since
Tornado joins the whole body before it writes anything to the Kobo.

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
    sw.exact_percent = kepub_alignment.kobo_progress_percent(sw.kepub, *found)
    assert location_of(_bookmark(sw)) == sw.own  # not yet proven
    return sw


def _bookmark(sw):
    return sw.get(sw.checked).get("CurrentBookmark") or {}


def _kepub_response_with_body(sw, stream):
    """Vary streaming behavior while preserving the actual opened KEPUB."""
    response = send_file(sw.kepub, conditional=True)
    original = response.response

    class Body:
        file = original.file

        def __iter__(self):
            return iter(stream(original))

        def close(self):
            original.close()

    response.response = Body()
    return response


def _interrupted_kepub_response(sw, *, raises=False):
    def stream(body):
        yield body.file.read(1000)
        if raises:
            raise OSError("simulated stream failure")

    return _kepub_response_with_body(sw, stream)


def _install_download(monkeypatch, sw, *, broken=False, serve=None):
    """The real ``helper.get_download_link`` as a WSGI app, mounted the way
    Flask runs a view: the request context is pushed for the view and popped
    before the server reads the body."""
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
    if serve is not None:
        do_download = serve
    elif broken:
        def do_download(*_a, **_k):
            return _interrupted_kepub_response(sw, raises=True)
    else:
        def do_download(*_a, **_k):
            return send_file(sw.kepub, conditional=True)
    monkeypatch.setattr(helper, "do_download_file", do_download)

    def application(environ, start_response):
        with sw.app.request_context(environ):
            g.annotation_origin_device_id = sw.checked.id
            response = helper.get_download_link(book.id, "kepub", "kobo")
        return response(environ, start_response)
    return application


class Served:
    """What reached the Kobo's socket."""

    def __init__(self, status, headers, body, complete):
        self.status, self.headers, self.body, self.complete = status, headers, body, complete


def _download(monkeypatch, sw, method="GET", headers=None, *, broken=False, serve=None,
              application=None):
    """The Kobo downloads through CWNG's production server, gevent's pywsgi
    with ``cps.gevent_wsgi.MyWSGIHandler`` on a real local socket. The server
    is chosen here by what actually serves the request, not by a flag."""
    import http.client
    import gevent.socket
    from gevent.pywsgi import WSGIServer
    from cps.gevent_wsgi import MyWSGIHandler

    if application is None:
        application = _install_download(monkeypatch, sw, broken=broken, serve=serve)
    server = WSGIServer(("127.0.0.1", 0), application, handler_class=MyWSGIHandler,
                        log=None, error_log=None)
    server.start()
    try:
        sock = gevent.socket.create_connection(("127.0.0.1", server.server_port))
        lines = [f"{method} /kobo/download/{sw.book.id}/kepub HTTP/1.1", "Host: localhost"]
        lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
        sock.sendall(("\r\n".join(lines + ["Connection: close", "", ""])).encode())
        reply = http.client.HTTPResponse(sock, method=method)
        reply.begin()
        try:
            body, complete = reply.read(), True
        except http.client.IncompleteRead as short:
            body, complete = short.partial, False
        served = Served(reply.status, dict(reply.getheaders()), body, complete)
        sock.close()
    finally:
        server.stop(timeout=5)
    sw.session.expire_all()
    return served


class _TornadoConnection:
    """The HTTP1Connection a Tornado request writes its reply to."""

    def __init__(self, fail_headers=False):
        self.fail_headers, self.written, self.finished = fail_headers, None, False
        self.context = type("Context", (), {"remote_ip": "127.0.0.1", "protocol": "http"})()

    def write_headers(self, start_line, headers, chunk=None):
        if self.fail_headers:
            from tornado.iostream import StreamClosedError
            raise StreamClosedError()
        self.written = (start_line, headers, chunk)

    def finish(self):
        self.finished = True


def _download_under_tornado(monkeypatch, sw, *, fail_headers=False):
    """The same download through the Tornado fallback server
    (``cps.tornado_wsgi.MyWSGIContainer``), which buffers the whole body
    before it writes anything to the Kobo."""
    import asyncio
    from tornado import httputil
    from cps.tornado_wsgi import MyWSGIContainer

    application = _install_download(monkeypatch, sw)
    connection = _TornadoConnection(fail_headers=fail_headers)
    request = httputil.HTTPServerRequest(
        method="GET", uri=f"/kobo/download/{sw.book.id}/kepub", version="HTTP/1.1",
        headers=httputil.HTTPHeaders({"Host": "localhost"}), connection=connection)
    container = MyWSGIContainer(application)
    error = None
    try:
        asyncio.run(container.handle_request(request))
    except Exception as exc:  # noqa: BLE001  (the write failure is the case)
        error = exc
    sw.session.expire_all()
    return connection, error


# == Requests that do not deliver the whole current KEPUB set no proof ========

def test_a_head_download_does_not_prove_the_kobo_holds_the_current_kepub(
        monkeypatch, stale_kobo):
    sw = stale_kobo
    served = _download(monkeypatch, sw, method="HEAD")
    assert served.status == 200 and served.body == b""

    bookmark = _bookmark(sw)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == sw.own


def test_a_range_download_does_not_prove_the_kobo_holds_the_current_kepub(
        monkeypatch, stale_kobo):
    sw = stale_kobo
    served = _download(monkeypatch, sw, headers={"Range": "bytes=0-0"})
    assert served.status == 206
    assert served.complete and len(served.body) == 1  # the whole (one-byte) range

    bookmark = _bookmark(sw)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == sw.own


def test_a_download_whose_stream_fails_partway_does_not_prove_the_current_kepub(
        monkeypatch, stale_kobo):
    sw = stale_kobo
    served = _download(monkeypatch, sw, broken=True)
    assert served.status == 200
    assert len(served.body) == 1000 and not served.complete

    bookmark = _bookmark(sw)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == sw.own


# == Control: a full download that is read to the end is proof ================

def test_a_full_download_read_to_the_end_delivers_the_exact_span(monkeypatch, stale_kobo):
    sw = stale_kobo
    served = _download(monkeypatch, sw)
    assert served.status == 200 and served.complete
    with open(sw.kepub, "rb") as handle:
        assert served.body == handle.read()

    bookmark = _bookmark(sw)
    # The exact span arrives with the percentage the Kobo works out for it
    # (#2821), not the 40 KOReader counted the book by.
    assert bookmark.get("ProgressPercent") == pytest.approx(sw.exact_percent, abs=0.01)
    assert location_of(bookmark) == sw.exact
    assert sw.synced_location(sw.checked) == sw.exact


def test_a_full_download_after_on_demand_conversion_records_file_proof(monkeypatch, sw):
    """A worker publishes KEPUB after the request loaded the book's formats."""
    from pathlib import Path

    from sqlalchemy.orm import Session

    from cps import db, helper, ub
    from cps.services import kobo_place_gate
    from cps.tasks import kepub_backfill

    sw.checked = sw.kobo_device("Kobo Checked", checked=True)
    kepub_path = Path(sw.kepub)
    expected_file = kepub_path.read_bytes()
    kepub_path.unlink()
    book_id = sw.book.id
    format_row = sw.session.query(db.Data).filter_by(book=book_id, format="KEPUB").one()
    filename = format_row.name
    sw.session.delete(format_row)
    sw.session.commit()

    application = _install_download(monkeypatch, sw)
    monkeypatch.setattr(helper.config, "config_kepubifypath", "/test/kepubify", raising=False)
    monkeypatch.setattr(helper.config, "config_kobo_prefer_kepub", True, raising=False)
    monkeypatch.setattr(kepub_backfill, "is_kepub_backfill_pending", lambda: False)

    def convert(book_id, _path, source, destination, _user_id, *, blocking, timeout):
        assert (source, destination, blocking) == ("EPUB", "KEPUB", True)
        kepub_path.write_bytes(expected_file)
        with Session(sw.engine) as worker:
            worker.add(db.Data(name=filename, book_format=destination, book=book_id,
                               uncompressed_size=len(expected_file)))
            worker.commit()

    monkeypatch.setattr(helper, "convert_book_format", convert)
    with Session(sw.engine) as request_session:
        # Keep the request's identity map separate from the app-data commits.
        book = request_session.query(db.Books).filter_by(id=book_id).one()
        assert [data.format for data in book.data] == ["EPUB"]
        monkeypatch.setattr(helper.calibre_db, "get_book", lambda _id: book)
        monkeypatch.setattr(helper.calibre_db, "get_filtered_book", lambda *_a, **_kw: book)
        monkeypatch.setattr(helper.calibre_db, "get_book_format", lambda bid, fmt:
                            request_session.query(db.Data).filter_by(book=bid, format=fmt).one_or_none())
        served = _download(monkeypatch, sw, application=application)

    assert served.status == 200 and served.complete and served.body == expected_file
    download = sw.session.query(ub.KoboDeviceBookDownload).filter_by(
        device_id=sw.checked.id, book_id=book_id).one()
    assert download.completed_at is not None
    assert download.kepub_file_identity == kobo_place_gate.completed_kepub_identity(
        sw.checked.id, sw.book)


def test_a_full_transfer_of_a_different_kepub_earns_no_proof(monkeypatch, stale_kobo, tmp_path):
    """An unchanged library path cannot prove which file the response opened."""
    from pathlib import Path

    from cps import ub

    from cps.services.kobo_post_download_restore import RESTORE_PENDING

    sw = stale_kobo
    previous_at = sw.session.query(ub.KoboDeviceBookDownload).filter_by(
        device_id=sw.checked.id, book_id=sw.book.id).one().downloaded_at
    original = Path(sw.kepub).read_bytes()
    different = tmp_path / "different.kepub"
    replacement = original[:10] + bytes([original[10] ^ 0x01]) + original[11:]
    different.write_bytes(replacement)

    served = _download(monkeypatch, sw, serve=lambda *_a, **_kw: send_file(different, conditional=True))

    assert served.status == 200 and served.complete and served.body == replacement
    download = sw.session.query(ub.KoboDeviceBookDownload).filter_by(
        device_id=sw.checked.id, book_id=sw.book.id).one()
    assert download.downloaded_at > previous_at
    assert download.restore_state == RESTORE_PENDING
    assert download.completed_at is None
    assert location_of(_bookmark(sw)) == sw.own


def test_local_metadata_rewrite_from_the_verified_kepub_preserves_proof(
        monkeypatch, stale_kobo, tmp_path):
    """The default Kobo download can rewrite metadata and still prove its source."""
    import io
    import zipfile

    from cps import helper

    sw = stale_kobo
    original_download = helper.do_download_file
    application = _install_download(monkeypatch, sw)
    monkeypatch.setattr(helper, "do_download_file", original_download)
    monkeypatch.setattr(helper.current_user, "locale", "en", raising=False)
    monkeypatch.setattr(helper.config, "config_kepubifypath", "/test/kepubify", raising=False)
    monkeypatch.setattr(helper.config, "config_embed_metadata", True, raising=False)
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(tmp_path))

    served = _download(monkeypatch, sw, application=application)

    assert served.status == 200 and served.complete
    with zipfile.ZipFile(sw.kepub) as original, zipfile.ZipFile(io.BytesIO(served.body)) as delivered:
        chapters = [name for name in original.namelist() if name.endswith((".html", ".xhtml"))]
        assert chapters
        assert all(delivered.read(chapter) == original.read(chapter) for chapter in chapters)
        package = next(name for name in original.namelist() if name.endswith(".opf"))
        assert delivered.read(package) != original.read(package)
    assert location_of(_bookmark(sw)) == sw.exact


def test_drive_delivery_does_not_claim_a_local_kepub(monkeypatch, stale_kobo, tmp_path):
    """A complete Drive download can differ from a copy still on disk."""
    from pathlib import Path

    from cps import helper, ub
    from cps.services import user_cover

    from cps.services.kobo_post_download_restore import RESTORE_PENDING

    sw = stale_kobo
    previous_at = sw.session.query(ub.KoboDeviceBookDownload).filter_by(
        device_id=sw.checked.id, book_id=sw.book.id).one().downloaded_at
    original_download = helper.do_download_file
    application = _install_download(monkeypatch, sw)
    monkeypatch.setattr(helper, "do_download_file", original_download)
    monkeypatch.setattr(helper.config, "config_use_google_drive", True)
    monkeypatch.setattr(helper.config, "config_embed_metadata", False, raising=False)
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(tmp_path))
    monkeypatch.setattr(user_cover, "override_for_user", lambda *_: object())
    monkeypatch.setattr(user_cover, "materialize_delivery_copy", lambda *_a, **_k: None)
    monkeypatch.setattr(helper.gd, "getFileFromEbooksFolder", lambda *_: object())
    original = Path(sw.kepub).read_bytes()
    remote = original[:10] + bytes([original[10] ^ 0x01]) + original[11:]
    monkeypatch.setattr(
        helper.gd, "downloadFile", lambda _path, _name, dest: Path(dest).write_bytes(remote)
    )

    served = _download(monkeypatch, sw, application=application)

    assert served.status == 200 and served.complete and served.body == remote
    download = sw.session.query(ub.KoboDeviceBookDownload).filter_by(
        device_id=sw.checked.id, book_id=sw.book.id).one()
    assert download.downloaded_at > previous_at
    assert download.restore_state == RESTORE_PENDING
    assert download.completed_at is None
    assert location_of(_bookmark(sw)) == sw.own


# == Byte count: proof needs the declared length actually yielded ============

def test_an_x_sendfile_download_does_not_prove_the_current_kepub(monkeypatch, stale_kobo):
    """With X-Sendfile the app yields no bytes; the front server sends the
    file later, or not at all. Nothing CWNG saw proves the Kobo got it."""
    sw = stale_kobo
    monkeypatch.setitem(sw.app.config, "USE_X_SENDFILE", True)
    served = _download(monkeypatch, sw)
    assert served.status == 200
    assert served.headers.get("X-Sendfile") and served.body == b""

    bookmark = _bookmark(sw)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == sw.own


class _ShortDrive:
    """A Google Drive file whose second ranged chunk fails: the real
    ``do_gdrive_download`` logs it and ends the stream normally."""

    def __init__(self, path):
        with open(path, "rb") as handle:
            self.data = handle.read()
        assert len(self.data) > 64 * 1024  # at least two chunks
        self.metadata = {"fileSize": str(len(self.data)), "downloadUrl": "https://drive.test/f"}
        self.auth = self

    def Get_Http_Object(self):
        return self

    def request(self, _url, headers):
        first, last = (int(n) for n in headers["Range"][len("bytes="):].split("-"))
        if first == 0:
            return type("R", (), {"status": 206})(), self.data[first:last + 1]
        return type("R", (), {"status": 500})(), b""


def test_a_google_drive_stream_that_ends_short_does_not_prove_the_current_kepub(
        monkeypatch, stale_kobo):
    from cps import gdriveutils
    sw = stale_kobo
    # Drive chunks are 1 MiB; the fixture KEPUB is smaller, so cut them to
    # 64 KiB to give the real generator a second chunk to fail on.
    whole_chunks = gdriveutils.partial
    monkeypatch.setattr(gdriveutils, "partial",
                        lambda total, _limit: whole_chunks(total, 64 * 1024))
    drive = _ShortDrive(sw.kepub)

    def serve(_book, _format, _client, _data, headers, **_k):
        return gdriveutils.do_gdrive_download(drive, headers)

    served = _download(monkeypatch, sw, serve=serve)
    assert served.status == 200
    assert 0 < len(served.body) < len(drive.data)

    bookmark = _bookmark(sw)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == sw.own


def test_a_stream_shorter_than_its_content_length_does_not_prove_the_current_kepub(
        monkeypatch, stale_kobo):
    """The byte-count clause on its own: Content-Length is declared and the
    body ends normally, early."""
    sw = stale_kobo
    served = _download(
        monkeypatch, sw, serve=lambda *_a, **_k: _interrupted_kepub_response(sw)
    )
    assert served.status == 200
    assert len(served.body) == 1000 and not served.complete

    bookmark = _bookmark(sw)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == sw.own


# == Server and size: only gevent proves, and never an empty KEPUB ============

@pytest.mark.parametrize("write", ["written", "write-fails"])
def test_a_download_under_the_tornado_fallback_does_not_prove_the_current_kepub(
        monkeypatch, stale_kobo, write):
    """Tornado joins the whole body before it writes the reply, so reading
    the body to the end says nothing about what reached the Kobo."""
    sw = stale_kobo
    connection, error = _download_under_tornado(
        monkeypatch, sw, fail_headers=(write == "write-fails"))
    if write == "written":
        assert error is None and connection.finished
        start_line, _headers, chunk = connection.written
        with open(sw.kepub, "rb") as handle:
            assert start_line.code == 200 and chunk == handle.read()
    else:
        assert error is not None and connection.written is None

    bookmark = _bookmark(sw)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == sw.own


def test_a_zero_byte_kepub_served_in_full_does_not_prove_the_current_kepub(
        monkeypatch, stale_kobo):
    sw = stale_kobo
    open(sw.kepub, "wb").close()

    served = _download(monkeypatch, sw)
    assert served.status == 200 and served.complete and served.body == b""
    assert served.headers.get("Content-Length") == "0"

    # An empty KEPUB has no spans, so the Location served cannot show the
    # proof either way; ask the proof itself, as the span gate does.
    from cps.services.kobo_place_gate import completed_kepub_identity
    assert completed_kepub_identity(sw.checked.id, sw.book) is None
    bookmark = _bookmark(sw)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == sw.own
