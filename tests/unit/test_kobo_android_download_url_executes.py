# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Android download-URL branch, EXECUTED rather than pinned to source text.

`tests/unit/test_kobo_android_app_compat.py` asserts that the string
``kobo.redirect_download_book`` appears in the source of
``get_download_url_for_book``, and its failure message states a behaviour:

    "Android-device branch must route to the redirect_download_book endpoint,
     not the direct download_book endpoint. The Kobo Android app refuses direct
     /download/<id>/<format> URLs."

That assertion is green whenever the identifier is spelled anywhere in the
function — including in a branch that never runs, behind a condition that never
matches, or assigned to a variable nothing uses. It cannot fail for the reason
it names.

It does not need to be a source pin. The branch reads ``request.headers``, and
nothing else about it needs a device: a Flask request context with and without
``x-kobo-deviceos: Android`` produces two distinguishable URLs, which is the
whole claim. Measured 2026-08-19:

    no header                  -> http://host:8083/kobo/TOK/download/7/kepub
    x-kobo-deviceos: Android   -> http://host:8083/kobo/TOK/redirect_download/7/kepub

This file is the executing counterpart. The source pins are left in place; they
are cheap, and one of them guards the redirect view's existence, which this does
not. What changes is that the routing claim is now checked by running it.

The remaining half of the advertised claim — that the Kobo Android app *refuses*
a direct URL — is a statement about someone else's client and stays UNVERIFIED
here. That is a limit of any test in this repository, and it is the reason to be
precise about what the green does mean: the server emits a redirect URL to an
Android device and a direct URL to everything else.
"""
from __future__ import annotations

import types
from unittest import mock

import pytest
from flask import Flask

pytestmark = pytest.mark.unit


HOST = "example.local:8083"


def _url_for_device(device_os: str | None) -> str:
    """Call the real helper inside a request context shaped like a Kobo's."""
    import cps.kobo as kobo

    app = Flask(__name__)
    # The unproxied branch is the one that builds the path by hand, so it is the
    # branch where a wrong endpoint_path would actually reach a device.
    app.wsgi_app = types.SimpleNamespace(is_proxied=False)

    headers = {"Host": HOST}
    if device_os is not None:
        headers["x-kobo-deviceos"] = device_os

    with mock.patch.object(kobo.config, "config_external_port", 8083, create=True), \
            mock.patch.object(kobo, "get_auth_token", lambda: "TOK", create=True):
        with app.test_request_context("/", headers=headers):
            return kobo.get_download_url_for_book(7, "KEPUB")


def test_an_android_device_is_given_the_redirect_url():
    url = _url_for_device("Android")
    assert "/redirect_download/" in url, url
    assert "/download/" not in url, (
        "an Android device was handed a direct download URL: " + url)


def test_every_other_device_is_given_the_direct_url():
    url = _url_for_device(None)
    assert "/download/" in url, url
    assert "/redirect_download/" not in url, (
        "a non-Android device was handed the Android redirect URL: " + url)


def test_the_two_urls_actually_differ():
    """Vacuity guard.

    Both assertions above would also hold if the helper returned the same string
    in both cases and that string happened to satisfy each substring check. Pin
    that the header changes the answer.
    """
    assert _url_for_device("Android") != _url_for_device(None)


def test_the_header_match_is_exact_and_not_a_substring():
    """`x-kobo-deviceos` is compared with ==, so near-misses take the other path.

    Recorded as behaviour rather than as a claim about what Kobo sends: if this
    ever needs to be case-insensitive or prefix-matched, this test is where that
    decision becomes visible instead of silently changing which devices get
    which URL.
    """
    for value in ("android", "Android ", "AndroidTV"):
        url = _url_for_device(value)
        assert "/redirect_download/" not in url, (
            "%r was treated as Android; the comparison is exact today" % value)


@pytest.mark.parametrize("book_format, expected", [("KEPUB", "kepub"), ("Epub", "epub")])
def test_the_format_is_lowercased_in_the_url(book_format, expected):
    import cps.kobo as kobo

    app = Flask(__name__)
    app.wsgi_app = types.SimpleNamespace(is_proxied=False)
    with mock.patch.object(kobo.config, "config_external_port", 8083, create=True), \
            mock.patch.object(kobo, "get_auth_token", lambda: "TOK", create=True):
        with app.test_request_context("/", headers={"Host": HOST}):
            assert kobo.get_download_url_for_book(7, book_format).endswith("/" + expected)
