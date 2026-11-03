# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2724: with two or more Kobos, reading on one never moves the other.

A Kobo's book download (Nickel's file GET) carries the auth token in the URL
but no ``x-kobo-*`` headers, so the download route cannot tell which Kobo is
fetching the file. On an account with more than one Kobo that attribution is
ambiguous, and an ambiguous download never proves the Kobo holds the current
KEPUB (spec section 16 item 4). Without that proof, a checked Kobo that has
read the book before is served its OWN old Location with the shared
percentage, and Nickel, which navigates by Location, stays where it was. That
is the reporter's "I read on the Clara BW, open on the Libra 2, nothing
happens".

The download URL a Kobo receives comes from its own library/sync, which does
carry the device header. These tests take that URL from the real
``get_download_url_for_book``, fetch it the way Nickel does (no device
headers, through the production gevent server and the real
``helper.get_download_link``), and then ask the real Kobo GET /state what
that Kobo is served.
"""

from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from flask import g, send_file

from cps import kobo as kobo_module
from cps.services import kepub_alignment
from tests.unit.share_place_span_world import (  # noqa: F401  (fixture)
    _now, location_of, span_location, span_rows, sw,
)

pytestmark = pytest.mark.unit

# Captured at import, before the span world stubs it out.
REAL_DOWNLOAD_URL = kobo_module.get_download_url_for_book


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")
    monkeypatch.setattr(kobo_module.config, "config_external_port", 8083, raising=False)


@pytest.fixture
def two_kobos(sw):
    """Two checked Kobos on one account. A KOReader has shared a place at 40%;
    the "Libra" read the book before (its own span at 5%) and holds a KEPUB
    from before the current library one, as any Kobo does until it downloads
    the book again."""
    # url_for needs the real Kobo routes; register them before any request.
    sw.app.register_blueprint(kobo_module.kobo)
    rows = span_rows()
    sw.koreader_device = sw.koreader("koreader-1", "KOReader", checked=True)
    sw.clara = sw.kobo_device("Clara BW", checked=True)
    sw.checked = sw.libra = sw.kobo_device("Libra 2", checked=True)
    sw.download(sw.libra, at=sw.kepub_written() - timedelta(days=1))
    own = rows[5]
    sw.put(sw.libra, own["source"], own["span"], 5, _now() - timedelta(hours=1))
    sw.settle(sw.libra)
    sw.push(rows[300]["xpointer"], 40)
    sw.own = span_location(own["source"], own["span"])
    sw.exact = span_location(*kepub_alignment.xpointer_to_span(
        sw.epub, sw.kepub, rows[300]["xpointer"]))
    assert location_of(_bookmark(sw, sw.libra)) == sw.own  # not yet proven
    return sw


def _bookmark(sw, device):
    return sw.get(device).get("CurrentBookmark") or {}


def _url_from_sync(sw, device, *, proxied=False):
    """The download URL ``device``'s own library/sync hands it."""
    real_wsgi_app, sw.app.wsgi_app = sw.app.wsgi_app, SimpleNamespace(is_proxied=proxied)
    try:
        with sw.app.test_request_context("/kobo/tok/v1/library/sync",
                                         base_url="http://cwng.example:8083"):
            g.auth_token = "tok"
            g.annotation_origin_device_id = device.id if device is not None else None
            return REAL_DOWNLOAD_URL(sw.book.id, "KEPUB")
    finally:
        sw.app.wsgi_app = real_wsgi_app


def _download(monkeypatch, sw, url):
    """Nickel fetches ``url``: no x-kobo-* headers, so the route resolves no
    device, through gevent's pywsgi with CWNG's handler."""
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

    def application(environ, start_response):
        with sw.app.request_context(environ):
            g.annotation_origin_device_id = None  # Nickel sends no device header
            response = helper.get_download_link(book.id, "kepub", "kobo")
        return response(environ, start_response)

    parts = urlsplit(url)
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    server = WSGIServer(("127.0.0.1", 0), application, handler_class=MyWSGIHandler,
                        log=None, error_log=None)
    server.start()
    try:
        sock = gevent.socket.create_connection(("127.0.0.1", server.server_port))
        sock.sendall((f"GET {target} HTTP/1.1\r\nHost: localhost\r\n"
                      "Connection: close\r\n\r\n").encode())
        reply = http.client.HTTPResponse(sock, method="GET")
        reply.begin()
        body = reply.read()
        sock.close()
    finally:
        server.stop(timeout=5)
    sw.session.expire_all()
    assert reply.status == 200 and body


def test_a_kobo_that_downloads_its_own_sync_url_is_sent_the_shared_place(
        monkeypatch, two_kobos):
    sw = two_kobos
    _download(monkeypatch, sw, _url_from_sync(sw, sw.libra))

    bookmark = _bookmark(sw, sw.libra)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == sw.exact
    assert sw.synced_location(sw.libra) == sw.exact


def test_behind_a_reverse_proxy_the_link_names_the_kobo_too(monkeypatch, two_kobos):
    sw = two_kobos
    _download(monkeypatch, sw, _url_from_sync(sw, sw.libra, proxied=True))

    assert location_of(_bookmark(sw, sw.libra)) == sw.exact


def test_a_link_naming_a_deactivated_kobo_proves_nothing(monkeypatch, two_kobos):
    sw = two_kobos
    url = _url_from_sync(sw, sw.libra)
    sw.libra.active = False
    sw.session.commit()
    _download(monkeypatch, sw, url)
    sw.libra.active = True
    sw.session.commit()

    assert location_of(_bookmark(sw, sw.libra)) == sw.own


def test_the_download_proves_only_the_kobo_whose_sync_url_it_was(
        monkeypatch, two_kobos):
    """The Clara fetching its URL proves nothing for the Libra: the Libra
    still holds an older KEPUB, so it must not be sent a span from the new one."""
    sw = two_kobos
    _download(monkeypatch, sw, _url_from_sync(sw, sw.clara))

    assert location_of(_bookmark(sw, sw.libra)) == sw.own


def test_a_url_naming_another_accounts_device_proves_nothing(monkeypatch, two_kobos):
    from cps import ub
    sw = two_kobos
    stranger = sw.add_user("stranger")
    theirs = ub.Device(user_id=stranger.id, kind="kobo", display_name="Theirs",
                       active=True, created_by="auto")
    sw.session.add(theirs)
    sw.session.commit()
    url = _url_from_sync(sw, sw.libra).replace(sw.libra.public_id, theirs.public_id)
    _download(monkeypatch, sw, url)

    assert location_of(_bookmark(sw, sw.libra)) == sw.own
    assert sw.session.query(ub.KoboDeviceBookDownload).filter_by(
        device_id=theirs.id).count() == 0
