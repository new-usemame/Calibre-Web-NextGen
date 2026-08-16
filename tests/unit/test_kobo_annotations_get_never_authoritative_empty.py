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
        rs, "resolve_entitlement_ownership",
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
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda eid: None)
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)

    with app.test_request_context("/api/v3/content/not-ours/annotations", method="GET"):
        assert _view()("not-ours") is sentinel


def test_patch_upload_direction_still_proxies(app, monkeypatch):
    """Uploads are harmless and must keep working -- only the download is destructive."""
    sentinel = object()
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda eid: None)
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)

    with app.test_request_context(
        f"/api/v3/content/{BOOK_UUID}/annotations", method="PATCH",
        json={"updatedAnnotations": []},
    ):
        assert _view()(BOOK_UUID) is sentinel


def test_patch_non_object_body_is_validated_locally_and_still_proxies(
    app, monkeypatch, caplog,
):
    """Malformed local-capture input must be explicit, not an AttributeError
    swallowed by the outer handler; upload pass-through remains unchanged."""
    from cps.services import annotation_sync

    sentinel = object()
    dispatched = []
    monkeypatch.setattr(
        rs, "get_book_by_entitlement_id",
        lambda _eid: SimpleNamespace(id=347, title="Flatland", identifiers=[]),
    )
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(id=7, name="test-user", is_authenticated=True),
    )
    monkeypatch.setattr(
        annotation_sync, "dispatch_annotation_sync",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )

    with app.test_request_context(
        f"/api/v3/content/{BOOK_UUID}/annotations", method="PATCH", json=["not", "an", "object"],
    ), caplog.at_level("WARNING"):
        assert _view()(BOOK_UUID) is sentinel

    assert dispatched == []
    assert "PATCH body is not a JSON object" in caplog.text


def test_patch_non_list_annotation_batch_is_rejected_without_breaking_proxy(
    app, monkeypatch, caplog,
):
    from cps.services import annotation_sync

    sentinel = object()
    monkeypatch.setattr(
        rs, "get_book_by_entitlement_id",
        lambda _eid: SimpleNamespace(id=347, title="Flatland", identifiers=[]),
    )
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(id=7, name="test-user", is_authenticated=True),
    )
    monkeypatch.setattr(
        annotation_sync, "dispatch_annotation_sync",
        lambda *_args, **_kwargs: pytest.fail("non-list batch reached dispatcher"),
    )

    with app.test_request_context(
        f"/api/v3/content/{BOOK_UUID}/annotations", method="PATCH",
        json={"updatedAnnotations": {"id": "not-a-list"}},
    ), caplog.at_level("WARNING"):
        assert _view()(BOOK_UUID) is sentinel

    assert "expected a list" in caplog.text


# --- fail-closed: uncertainty must never fall through to the destructive path ---

def test_ownership_lookup_error_fails_closed(app, monkeypatch, no_proxy):
    """A DB hiccup must not be read as 'not our book'.

    `get_book_by_entitlement_id` swallows every exception and returns None, which
    is indistinguishable from a genuinely foreign entitlement. If the guard used
    that, a transient metadata-DB failure would re-open the exact path that
    deleted 87 highlights. Uncertainty fails closed.
    """
    def _boom(_uuid):
        raise RuntimeError("metadata db unavailable")

    monkeypatch.setattr(rs.calibre_db, "get_book_by_uuid", _boom)
    with app.test_request_context(f"/api/v3/content/{BOOK_UUID}/annotations", method="GET"):
        resp = _view()(BOOK_UUID)
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After")


def test_guard_runs_even_when_kobo_sync_is_disabled(app, monkeypatch, no_proxy):
    """Switching Kobo sync off must not re-open the destructive download.

    The decorator proxies everything when sync is disabled, so before this the
    guard never ran in that state -- an admin toggling the setting could hand the
    device an answer that deletes its highlights.
    """
    monkeypatch.setattr(rs.config, "config_kobo_sync", False, raising=False)
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda eid: SimpleNamespace(id=347, title="Flatland", uuid=BOOK_UUID),
    )
    decorated = rs.requires_reading_services_auth_and_config(
        lambda *_a, **_k: pytest.fail("handler should not be reached"))
    with app.test_request_context(f"/api/v3/content/{BOOK_UUID}/annotations", method="GET"):
        resp = decorated()
    assert resp.status_code == 503


def test_guard_runs_even_without_an_authenticated_session(app, monkeypatch, no_proxy):
    """A lost or expired Kobo session must not re-open the destructive download."""
    monkeypatch.setattr(rs.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(rs, "current_user", SimpleNamespace(is_authenticated=False, id=None))
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda eid: SimpleNamespace(id=347, title="Flatland", uuid=BOOK_UUID),
    )
    decorated = rs.requires_reading_services_auth_and_config(
        lambda *_a, **_k: pytest.fail("handler should not be reached"))
    with app.test_request_context(f"/api/v3/content/{BOOK_UUID}/annotations", method="GET"):
        resp = decorated()
    assert resp.status_code == 503


def test_foreign_content_still_proxies_when_sync_disabled(app, monkeypatch):
    """The guard must not become a blanket block: a real Kobo store book still proxies."""
    sentinel = object()
    monkeypatch.setattr(rs.config, "config_kobo_sync", False, raising=False)
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda eid: None)
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)
    decorated = rs.requires_reading_services_auth_and_config(lambda *_a, **_k: sentinel)
    with app.test_request_context("/api/v3/content/not-ours/annotations", method="GET"):
        assert decorated() is sentinel
