# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2710: the PDF reader's Save button could not download the book.

pdf.js saves by building a blob: URL and clicking a link to it. Firefox checks
that download against frame-src, which falls back to child-src and then to
default-src. Our default-src had no blob:, so the download was refused with
"blocked the loading of a resource (frame-src) at blob:...". The reader pages
must allow blob: frames; every other page keeps the narrow policy.
"""
from types import SimpleNamespace

import pytest


def _csp(monkeypatch, endpoint, path="/", trusted_hosts=""):
    from cps import web

    monkeypatch.setattr(web, "request", SimpleNamespace(endpoint=endpoint, path=path))
    monkeypatch.setattr(web, "config", SimpleNamespace(
        config_trustedhosts=trusted_hosts,
        config_use_google_drive=False,
        config_use_goodreads=False,
    ))
    resp = web.add_security_headers(SimpleNamespace(headers={}))
    directives = {}
    for part in resp.headers["Content-Security-Policy"].split(";"):
        tokens = part.split()
        if tokens:
            directives[tokens[0]] = tokens[1:]
    return directives


def _effective_frame_src(directives):
    # The CSP fallback chain a browser walks for frames and Firefox downloads.
    for name in ("frame-src", "child-src", "default-src"):
        if name in directives:
            return directives[name]
    return []


@pytest.mark.unit
@pytest.mark.parametrize("endpoint, path", [
    ("web.read_book", "/read/7/pdf"),   # readpdf.html, also embedded by the SPA reader
    ("spa.spa_shell", "/app/read/7"),
])
def test_reader_pages_allow_blob_frames_for_pdf_save(monkeypatch, endpoint, path):
    frame_src = _effective_frame_src(_csp(monkeypatch, endpoint, path))
    assert "blob:" in frame_src
    # The SPA embeds /read/<id>/pdf in an iframe, so same-origin frames must stay allowed.
    assert "'self'" in frame_src


@pytest.mark.unit
def test_reader_frame_src_keeps_trusted_hosts(monkeypatch):
    frame_src = _effective_frame_src(
        _csp(monkeypatch, "web.read_book", "/read/7/pdf", trusted_hosts="books.example.org"))
    assert "books.example.org" in frame_src


@pytest.mark.unit
def test_other_pages_do_not_allow_blob_frames(monkeypatch):
    frame_src = _effective_frame_src(_csp(monkeypatch, "web.index", "/"))
    assert "blob:" not in frame_src
