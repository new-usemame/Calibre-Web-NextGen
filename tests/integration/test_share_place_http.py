# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise device settings through HTTP dispatch, authentication and CSRF.

These tests run locally without Docker. The acceptance suite calls unwrapped
views; this seam verifies their middleware and failure responses as well.
"""

import pytest
from flask import Flask, jsonify
from flask_wtf.csrf import CSRFProtect, generate_csrf
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit


@pytest.fixture
def http_api(tmp_path, monkeypatch):
    from cps import annotations, ub, usermanagement
    from cps.cw_login import LoginManager

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    ub.Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all([
        ub.User(id=7, name="owner", email="owner@example.invalid", role=0),
        ub.User(id=8, name="other", email="other@example.invalid", role=0),
        ub.Device(public_id="owned", user_id=7, kind="kobo", display_name="Reader"),
        ub.Device(public_id="foreign", user_id=8, kind="kobo", display_name="Other"),
    ])
    db.commit()
    monkeypatch.setattr(ub, "session", db)
    monkeypatch.setattr(ub, "session_commit", db.commit)
    monkeypatch.setattr(usermanagement.config, "config_allow_reverse_proxy_header_login",
                        False, raising=False)

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="local-test-secret")
    manager = LoginManager(app)
    manager.user_loader(lambda user_id, _random, _session_key: db.get(ub.User, int(user_id)))
    CSRFProtect(app)
    app.register_blueprint(annotations.annotations_bp)

    @app.get("/test-csrf")
    def csrf_token():
        return jsonify(token=generate_csrf())

    client = app.test_client()
    token = client.get("/test-csrf").get_json()["token"]

    def read(public_id="owned"):
        with sessionmaker(bind=engine)() as other:
            row = other.query(ub.Device).filter_by(public_id=public_id).one()
            return row.display_name, row.share_place, row.share_place_enabled_at

    try:
        yield client, {"X-CSRFToken": token}, read
    finally:
        db.close()
        engine.dispose()


def _login(client):
    with client.session_transaction() as session:
        session["_user_id"] = "7"
        session["_fresh"] = True
        session["_random"] = "test-nonce"
        session["_id"] = "test-session"


def test_settings_patch_requires_login_and_csrf_then_commits(http_api):
    client, headers, read = http_api
    path = "/api/annotations/devices/owned"
    payload = {"label": "Bedside Kobo", "share_place": True}

    assert client.patch(path, json=payload, headers=headers).status_code == 401
    _login(client)
    assert client.patch(path, json=payload).status_code == 400
    assert read() == ("Reader", False, None)

    response = client.patch(path, json=payload, headers=headers)
    assert response.status_code == 200
    assert response.get_json()["share_place"] is True
    label, enabled, enabled_at = read()
    assert (label, enabled) == ("Bedside Kobo", True)
    assert enabled_at is not None


@pytest.mark.parametrize("public_id", ["foreign", "missing"])
def test_ownership_is_checked_before_invalid_sharing_value(http_api, public_id):
    client, headers, read = http_api
    _login(client)
    response = client.patch(f"/api/annotations/devices/{public_id}",
                            json={"share_place": "true"}, headers=headers)
    assert response.status_code == 404
    assert read("foreign") == ("Other", False, None)


def test_invalid_label_cannot_partially_enable_sharing(http_api):
    client, headers, read = http_api
    _login(client)
    response = client.patch("/api/annotations/devices/owned",
                            json={"label": " bad", "share_place": True}, headers=headers)
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_label"
    assert read() == ("Reader", False, None)


def test_failed_commit_returns_json_error_and_rolls_back(http_api, monkeypatch):
    from cps import ub
    client, headers, read = http_api
    _login(client)
    monkeypatch.setattr(ub, "session_commit", lambda: False)
    response = client.patch("/api/annotations/devices/owned",
                            json={"label": "Changed", "share_place": True}, headers=headers)
    assert response.status_code == 500
    assert response.get_json() == {"error": "database_error"}
    assert read() == ("Reader", False, None)
    ub.session.expire_all()
    assert ub.session.query(ub.Device).filter_by(public_id="owned").one().share_place is False
