# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Contain every spelling of Kobo's destructive checkforchanges trigger."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from flask import Flask, jsonify, request

import cps.readingservices as rs


OWNED = "9e5251ad-d530-4e58-9121-8b8336099fdd"
FOREIGN = "kobo-store-content"


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.secret_key = "checkforchanges-containment-test"
    app.register_blueprint(rs.readingservices_api_v3)

    from cps.services import device_registry
    monkeypatch.setattr(
        device_registry, "register_kobo_device_best_effort", lambda **_kwargs: None,
    )
    return app


def _entries(*content_ids):
    return [
        {"ContentId": content_id, "etag": f"etag-{content_id}"}
        for content_id in content_ids
    ]


@pytest.mark.parametrize(
    ("sync_enabled", "authenticated"),
    [(True, False), (False, True)],
    ids=["expired-session", "sync-disabled"],
)
def test_owned_content_is_contained_before_auth_and_config_bypass(
    app, monkeypatch, sync_enabled, authenticated,
):
    monkeypatch.setattr(rs.config, "config_kobo_sync", sync_enabled, raising=False)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(is_authenticated=authenticated, id=7),
    )
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership", lambda _content_id: SimpleNamespace(id=347),
    )
    monkeypatch.setattr(
        rs,
        "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail(
            "owned checkforchanges reached the untouched auth/config proxy"
        ),
    )

    response = app.test_client().post(
        "/api/v3/content/checkforchanges", json=_entries(OWNED),
    )

    assert response.status_code == 200
    assert response.get_json() == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/v3/content/checkforchanges/",
        "/api/v3/Content/CheckForChanges",
        "/api/v3/content//checkforchanges",
        "/api/v3/content/checkforchanges%2F",
    ],
    ids=["trailing-slash", "case-variant", "double-slash", "encoded-trailing-slash"],
)
def test_equivalent_checkforchanges_spelling_cannot_fall_through_catch_all(
    app, monkeypatch, path,
):
    monkeypatch.setattr(rs.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(is_authenticated=True, id=7),
    )
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership", lambda _content_id: SimpleNamespace(id=347),
    )
    monkeypatch.setattr(
        rs,
        "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail(
            "owned checkforchanges spelling reached the catch-all proxy"
        ),
    )

    response = app.test_client().post(path, json=_entries(OWNED))

    assert response.status_code == 200
    assert response.get_json() == []


@pytest.mark.parametrize(
    ("sync_enabled", "authenticated"),
    [(True, False), (False, True)],
    ids=["expired-session", "sync-disabled"],
)
def test_foreign_content_still_proxies_unchanged_through_auth_and_config_windows(
    app, monkeypatch, sync_enabled, authenticated,
):
    outbound = []
    monkeypatch.setattr(rs.config, "config_kobo_sync", sync_enabled, raising=False)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(is_authenticated=authenticated, id=7),
    )
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: None)

    def _proxy(data=None):
        outbound.extend(request.get_json() if data is None else json.loads(data))
        return jsonify([FOREIGN])

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    entries = _entries(FOREIGN)

    response = app.test_client().post(
        "/api/v3/content/checkforchanges", json=entries,
    )

    assert outbound == entries
    assert response.status_code == 200
    assert response.get_json() == [FOREIGN]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v3/content/checkforchanges/",
        "/api/v3/Content/CheckForChanges",
        "/api/v3/content//checkforchanges",
        "/api/v3/content/checkforchanges%2F",
    ],
    ids=["trailing-slash", "case-variant", "double-slash", "encoded-trailing-slash"],
)
def test_foreign_content_still_proxies_unchanged_from_equivalent_spelling(
    app, monkeypatch, path,
):
    outbound = []
    monkeypatch.setattr(rs.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(
        rs, "current_user", SimpleNamespace(is_authenticated=True, id=7),
    )
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: None)

    def _proxy(data=None):
        outbound.extend(request.get_json() if data is None else json.loads(data))
        return jsonify([FOREIGN])

    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    entries = _entries(FOREIGN)

    response = app.test_client().post(path, json=entries)

    assert outbound == entries
    assert response.status_code == 200
    assert response.get_json() == [FOREIGN]
