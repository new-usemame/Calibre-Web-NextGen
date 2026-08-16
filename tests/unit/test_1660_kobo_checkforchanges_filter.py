# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Kobo annotation containment at the checkforchanges trigger boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify

import cps.readingservices as rs


OWNED = "9e5251ad-d530-4e58-9121-8b8336099fdd"
FOREIGN = "kobo-store-content"


@pytest.fixture
def app():
    return Flask(__name__)


def _request_entries(*content_ids):
    return [{"ContentId": content_id, "etag": f"etag-{content_id}"}
            for content_id in content_ids]


def _view(function):
    # Strip the auth/config decorator; functools.wraps exposes the original.
    return function.__wrapped__


def test_owned_id_is_stripped_from_request():
    entries = _request_entries(OWNED)
    assert rs._filter_check_for_changes_entries(entries, {OWNED}) == []


def test_not_owned_id_survives_request_filter():
    entries = _request_entries(FOREIGN)
    assert rs._filter_check_for_changes_entries(entries, set()) == entries


def test_mixed_request_keeps_only_not_owned_id():
    entries = _request_entries(OWNED, FOREIGN)
    assert rs._filter_check_for_changes_entries(entries, {OWNED}) == [entries[1]]


def test_unparseable_request_body_is_not_recognized():
    assert rs._parse_check_for_changes_request(b"not-json") is None
    assert rs._parse_check_for_changes_request(json.dumps({"ContentId": OWNED})) is None
    assert rs._parse_check_for_changes_request(json.dumps([{"etag": "missing-id"}])) is None


def test_unrecognized_response_shape_is_not_recognized():
    assert rs._check_for_changes_response_content_ids({"ContentId": OWNED}) is None
    assert rs._check_for_changes_response_content_ids([{"unexpected": OWNED}]) is None


def test_response_filter_handles_bare_ids_and_content_id_objects():
    entries = [OWNED, FOREIGN, {"ContentId": OWNED}, {"ContentId": FOREIGN}]
    assert rs._check_for_changes_response_content_ids(entries) == [
        OWNED, FOREIGN, OWNED, FOREIGN,
    ]
    assert rs._filter_check_for_changes_entries(entries, {OWNED}) == [
        FOREIGN, {"ContentId": FOREIGN},
    ]


def test_ownership_unknown_is_treated_as_owned():
    assert rs._check_for_changes_ownership_is_filtered(rs.OWNERSHIP_UNKNOWN) is True
    assert rs._check_for_changes_ownership_is_filtered(None) is False


def test_all_owned_batch_short_circuits_without_outbound_call(app, monkeypatch):
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda _content_id: SimpleNamespace(id=347),
    )
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("all-owned batch must not contact Kobo"),
    )
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=_request_entries(OWNED),
    ):
        response = _view(rs.handle_check_for_changes)()

    assert response.status_code == 200
    assert response.get_json() == []


def test_unparseable_request_short_circuits_without_outbound_call(app, monkeypatch):
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("unparseable batch must not contact Kobo"),
    )
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST",
        data=b"not-json", content_type="application/json",
    ):
        response = _view(rs.handle_check_for_changes)()

    assert response.status_code == 200
    assert response.get_json() == []


def test_mixed_handler_filters_outbound_request_and_defensive_response(app, monkeypatch):
    outbound = []

    def _ownership(content_id):
        return SimpleNamespace(id=347) if content_id == OWNED else None

    def _proxy(*, data):
        outbound.extend(json.loads(data))
        # Include an owned id Kobo was not asked about to exercise defense in depth.
        return jsonify([FOREIGN, {"ContentId": OWNED}])

    monkeypatch.setattr(rs, "resolve_entitlement_ownership", _ownership)
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    entries = _request_entries(OWNED, FOREIGN)
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=entries,
    ):
        response = _view(rs.handle_check_for_changes)()

    assert outbound == [entries[1]]
    assert response.status_code == 200
    assert response.get_json() == [FOREIGN]


def test_unrecognized_upstream_response_becomes_safe_empty_array(app, monkeypatch):
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services", lambda **_kwargs: jsonify({"unknown": "shape"}),
    )
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=_request_entries(FOREIGN),
    ):
        response = _view(rs.handle_check_for_changes)()

    assert response.status_code == 200
    assert response.get_json() == []


def test_annotation_get_proxies_even_for_owned_content(app, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda _content_id: pytest.fail("annotation GET must not expose ownership"),
    )
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations?limit=100", method="GET",
    ):
        assert _view(rs.handle_annotations)(OWNED) is sentinel


def test_unauthenticated_annotation_get_does_not_resolve_ownership(app, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(rs.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(is_authenticated=False, id=None),
    )
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership",
        lambda _content_id: pytest.fail("pre-auth annotation GET must not expose ownership"),
    )
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)
    decorated = rs.requires_reading_services_auth_and_config(
        lambda: pytest.fail("unauthenticated request must not reach handler")
    )
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations?limit=100", method="GET",
    ):
        assert decorated() is sentinel


def test_patch_upload_direction_still_proxies(app, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", lambda: sentinel)
    with app.test_request_context(
        f"/api/v3/content/{OWNED}/annotations", method="PATCH",
        json={"updatedAnnotations": []},
    ):
        assert _view(rs.handle_annotations)(OWNED) is sentinel


def test_patch_non_object_body_is_rejected_locally_and_still_proxies(
    app, monkeypatch, caplog,
):
    from cps.services import annotation_sync

    sentinel = object()
    dispatched = []
    monkeypatch.setattr(
        rs, "get_book_by_entitlement_id",
        lambda _content_id: SimpleNamespace(id=347, title="Flatland", identifiers=[]),
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
        f"/api/v3/content/{OWNED}/annotations", method="PATCH",
        json=["not", "an", "object"],
    ), caplog.at_level("WARNING"):
        assert _view(rs.handle_annotations)(OWNED) is sentinel

    assert dispatched == []
    assert "PATCH body is not a JSON object" in caplog.text


def test_patch_non_list_annotation_batch_is_rejected_without_breaking_proxy(
    app, monkeypatch, caplog,
):
    from cps.services import annotation_sync

    sentinel = object()
    monkeypatch.setattr(
        rs, "get_book_by_entitlement_id",
        lambda _content_id: SimpleNamespace(id=347, title="Flatland", identifiers=[]),
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
        f"/api/v3/content/{OWNED}/annotations", method="PATCH",
        json={"updatedAnnotations": {"id": "not-a-list"}},
    ), caplog.at_level("WARNING"):
        assert _view(rs.handle_annotations)(OWNED) is sentinel

    assert "expected a list" in caplog.text
