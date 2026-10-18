# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""POST /api/v1/books/<id>/kindle-usb: pick or create the file a browser pushes to a USB Kindle over WebUSB."""
import inspect
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest


def _ctx(path="/api/v1/books/5/kindle-usb"):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_request_context(path, method="POST")


def _user(download=True, anon=False):
    return SimpleNamespace(is_authenticated=True, is_anonymous=anon, id=1, name="alice",
                           role_download=lambda: download)


def _body(resp):
    return json.loads(resp[0].get_data() if isinstance(resp, tuple) else resp.get_data())


def _book(formats, title="Dune", authors=("Frank Herbert",)):
    return SimpleNamespace(id=5, title=title, data=[SimpleNamespace(format=f) for f in formats],
                           authors=[SimpleNamespace(name=a) for a in authors])


def _call(book, *, converter="/usr/bin/ebook-convert", convert_result=None, user=None):
    from cps.api import actions as mod
    db = MagicMock()
    db.get_filtered_book.return_value = book
    db.get_book_format.side_effect = lambda _id, fmt: SimpleNamespace(name="Dune - Frank Herbert", format=fmt)
    cfg = SimpleNamespace(config_converterpath=converter, get_book_path=lambda: "/books")
    with _ctx():
        with patch.object(mod, "current_user", user or _user()), \
             patch.object(mod, "calibre_db", db), \
             patch.object(mod, "config", cfg), \
             patch("cps.helper.config", SimpleNamespace(config_unicode_filename=False)), \
             patch.object(mod, "convert_book_format", return_value=convert_result) as convert:
            resp = inspect.unwrap(mod.prepare_kindle_usb)(5)
    return resp, convert


@pytest.mark.unit
@pytest.mark.parametrize("available, converter, expected", [
    (["EPUB", "AZW3"], True, ("AZW3", None)),          # already there: never reconvert
    (["EPUB"], True, ("AZW3", "EPUB")),                # convert
    (["EPUB", "MOBI"], True, ("AZW3", "EPUB")),        # AZW3 preferred over an existing MOBI
    (["EPUB", "MOBI"], False, ("MOBI", None)),         # no converter: use what the Kindle can read
    (["epub", "pdf"], False, ("PDF", None)),           # case-insensitive
    (["EPUB"], False, (None, None)),                   # EPUB alone is not sideloadable
    ([], True, (None, None)),
])
def test_plan(available, converter, expected):
    from cps.api.actions import kindle_usb_plan
    assert kindle_usb_plan(available, converter) == expected


@pytest.mark.unit
def test_requires_download_role():
    resp, convert = _call(_book(["AZW3"]), user=_user(download=False))
    assert resp[1] == 403
    convert.assert_not_called()


@pytest.mark.unit
def test_missing_book_is_404():
    resp, _ = _call(None)
    assert resp[1] == 404


@pytest.mark.unit
def test_existing_azw3_is_returned_without_converting():
    resp, convert = _call(_book(["EPUB", "AZW3"]))
    body = _body(resp)
    assert body == {"format": "AZW3", "converted": False, "filename": "Dune - Frank Herbert.azw3",
                    "download_url": "/download/5/azw3"}
    convert.assert_not_called()


@pytest.mark.unit
def test_epub_is_converted_inline_to_azw3():
    resp, convert = _call(_book(["EPUB"]))
    body = _body(resp)
    assert body["format"] == "AZW3" and body["converted"] is True
    convert.assert_called_once()
    args, kwargs = convert.call_args
    assert args[:4] == (5, "/books", "EPUB", "AZW3")
    assert kwargs["blocking"] is True


@pytest.mark.unit
def test_failed_conversion_falls_back_to_existing_mobi():
    resp, _ = _call(_book(["EPUB", "MOBI"]), convert_result="converter exploded")
    body = _body(resp)
    assert body["format"] == "MOBI" and body["converted"] is False


@pytest.mark.unit
def test_failed_conversion_with_no_fallback_is_502():
    resp, _ = _call(_book(["EPUB"]), convert_result="converter exploded")
    assert resp[1] == 502
    assert _body(resp)["error"]["code"] == "convert_failed"


@pytest.mark.unit
def test_nothing_sendable_is_422():
    resp, _ = _call(_book(["EPUB"]), converter=None)
    assert resp[1] == 422
    assert _body(resp)["error"]["code"] == "no_kindle_format"


@pytest.mark.unit
def test_filename_is_filesystem_safe():
    resp, _ = _call(_book(["AZW3"], title='A/B: "C"?', authors=("X|Y",)))
    name = _body(resp)["filename"]
    assert name.endswith(".azw3") and not any(c in name for c in '/:"?|')
