# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2958: with the Kobo store proxy off, checkforchanges is never forwarded.

With ``config_kobo_proxy`` off, the device's bearer token is one CWNG issued
on ``/oauth/token``. readingservices.kobo.com rejects it with 401 (measured
by the reporter: every forwarded call after the first token refresh, 116 of
116 over 14 days, and 3 of 3 on a Clara BW on v4.1.70), and the 401 is passed
back to the device as a sync error. ``[]`` is the answer measured safe on a
device: Nickel then sends no annotations GET and keeps its local highlights.
"""

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


def _entries(*content_ids):
    return [{"ContentId": c, "etag": f"etag-{c}"} for c in content_ids]


def _ownership(content_id):
    return SimpleNamespace(id=347) if content_id == OWNED else None


def _post(app, entries):
    with app.test_request_context(
        "/api/v3/content/checkforchanges", method="POST", json=entries,
    ):
        return rs.handle_check_for_changes.__wrapped__()


@pytest.mark.parametrize("ids", [(FOREIGN,), (OWNED, FOREIGN), (FOREIGN, FOREIGN)])
def test_proxy_off_answers_locally_and_never_contacts_kobo(app, monkeypatch, ids):
    monkeypatch.setattr(rs.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", _ownership)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kw: pytest.fail("proxy off: a CWNG token can never succeed at Kobo"),
    )
    response = _post(app, _entries(*ids))
    assert response.status_code == 200
    assert response.get_json() == []


def test_proxy_off_unknown_route_spelling_is_also_answered_locally(app, monkeypatch):
    monkeypatch.setattr(rs.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", _ownership)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kw: pytest.fail("proxy off: must not forward"),
    )
    with app.test_request_context(
        "/api/v3/content/CheckForChanges", method="POST", json=_entries(FOREIGN),
    ):
        response = rs.handle_unknown_reading_service_request.__wrapped__(
            "content/CheckForChanges",
        )
    assert response.status_code == 200
    assert response.get_json() == []


def test_proxy_on_still_forwards_foreign_ids_and_filters_owned(app, monkeypatch):
    outbound = []

    def _proxy(*, data):
        outbound.extend(json.loads(data))
        return jsonify([FOREIGN])

    monkeypatch.setattr(rs.config, "config_kobo_proxy", True, raising=False)
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", _ownership)
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services", _proxy)
    response = _post(app, _entries(OWNED, FOREIGN))
    assert outbound == _entries(FOREIGN)
    assert response.get_json() == [FOREIGN]
