# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The annotation DOWNLOAD direction must never hand Nickel an authoritative empty set.

Kobo's cloud has never heard of a sideloaded book. Forwarding
``GET /api/v3/content/<uuid>/annotations`` to it yields a success-shaped "you have
no annotations" answer, and Nickel acts on that by DELETING every local Bookmark
row for the book -- upstream calibre-web #2610.

Reproduced on the operator's own hardware 2026-08-15: 88 highlights were present
and correctly anchored at 11:58; one sync at 12:07 uploaded a single annotation
and deleted the other 87. For a sideloaded book the device is frequently the only
copy, so a 200-empty here is a destructive operation even though the verb is GET.

The one response that cannot be misread as "you have none" is an explicitly
non-authoritative failure. We return 503 + Retry-After for books we own, and keep
proxying for content we do NOT own (real Kobo store books, where Kobo's cloud is
genuinely authoritative).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

import cps.readingservices as rs


BOOK_UUID = "9e5251ad-d530-4e58-9121-8b8336099fdd"


@pytest.fixture
def app():
    return Flask(__name__)


@pytest.fixture
def no_proxy(monkeypatch):
    """Fail loudly if the download direction reaches Kobo for a book we own."""
    calls = []

    def _proxy():
        calls.append(True)
        raise AssertionError(
            "GET annotations was proxied to Kobo's cloud for a book we own -- "
            "its empty answer deletes the device's only copy (calibre-web #2610)"
        )

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    return calls


def _view():
    # strip the auth/config decorator; functools.wraps exposes the original
    return rs.handle_annotations.__wrapped__


def test_get_for_a_book_we_own_is_not_proxied_and_is_not_success(app, monkeypatch, no_proxy):
    monkeypatch.setattr(
        rs, "get_book_by_entitlement_id",
        lambda eid: SimpleNamespace(id=347, title="Flatland", uuid=BOOK_UUID),
    )
    with app.test_request_context(
        f"/api/v3/content/{BOOK_UUID}/annotations?limit=100", method="GET"
    ):
        resp = _view()(BOOK_UUID)

    status = resp.status_code if hasattr(resp, "status_code") else resp[1]
    assert status == 503, f"expected a non-authoritative 503, got {status}"
    assert not (200 <= status < 300), "a 2xx here tells Nickel to delete local highlights"
    assert resp.headers.get("Retry-After"), "503 must carry Retry-After so the device retries"


def test_get_for_content_we_do_not_own_still_proxies(app, monkeypatch):
    """A real Kobo store book is Kobo's data; its cloud IS authoritative there."""
    sentinel = object()
    monkeypatch.setattr(rs, "get_book_by_entitlement_id", lambda eid: None)
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)

    with app.test_request_context("/api/v3/content/not-ours/annotations", method="GET"):
        assert _view()("not-ours") is sentinel


def test_patch_upload_direction_still_proxies(app, monkeypatch):
    """Uploads are harmless and must keep working -- only the download is destructive."""
    sentinel = object()
    monkeypatch.setattr(rs, "get_book_by_entitlement_id", lambda eid: None)
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)

    with app.test_request_context(
        f"/api/v3/content/{BOOK_UUID}/annotations", method="PATCH",
        json={"updatedAnnotations": []},
    ):
        assert _view()(BOOK_UUID) is sentinel
