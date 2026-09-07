# SPDX-License-Identifier: GPL-3.0-or-later
"""Kobo login -> cookie-authenticated PATCH -> Highlights device label (#1853).

The exchanges are synthetic: the regression condition is a Reading Services
upload that does not repeat the hardware header from the store API login.
Auth, device registration, annotation persistence and UI serialization are real.
"""

from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def reader(tmp_path, monkeypatch):
    from cps import annotations, constants, kobo, kobo_auth, readingservices, ub
    from cps.cw_login import LoginManager
    from cps.usermanagement import load_user
    from cps.services import annotation_backup, annotation_sync

    engine = create_engine(f"sqlite:///{tmp_path / 'reader.sqlite'}")
    ub.Base.metadata.create_all(engine)
    database = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", database)
    monkeypatch.setattr(ub, "session_commit", lambda: database.commit() or True)
    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(annotation_backup, "WORKER_AUTOSTART", False)
    annotation_backup.reset_for_tests()
    monkeypatch.setattr(annotation_sync, "_HANDLERS", {})
    monkeypatch.setattr(annotation_sync, "_background_enqueue", lambda: None)
    monkeypatch.setattr(kobo_auth, "limiter", SimpleNamespace(check=lambda: None, current_limits=[]))
    monkeypatch.setattr(kobo.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_two_way_annotation_sync", False, raising=False)
    monkeypatch.setattr(kobo.config, "config_allow_reverse_proxy_header_login", False, raising=False)
    book = SimpleNamespace(id=42, uuid="00000000-0000-4000-8000-000000000042", title="Test book")
    monkeypatch.setattr(readingservices, "resolve_entitlement_ownership", lambda _id: book)
    monkeypatch.setattr(readingservices, "get_book_by_entitlement_id", lambda _id: book)
    monkeypatch.setattr(readingservices, "proxy_to_kobo_reading_services", lambda **_kw: ("", 200))
    monkeypatch.setattr(readingservices, "_begin_exchange_capture", lambda *_a, **_kw: None)
    monkeypatch.setattr(annotations, "_resolve_book_or_404", lambda _id: book)
    monkeypatch.setattr(annotations, "_resolve_annotation_anchor", lambda *_a: (None, "unresolved"))

    user = ub.User(name="reader", email="reader@example.invalid", password="unused", role=0)
    database.add(user)
    database.flush()
    token = ub.RemoteAuthToken()
    token.user_id = user.id
    token.auth_token = "reader-token"
    token.token_type = 1
    database.add(token)
    database.commit()
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-session-secret")
    manager = LoginManager(app)
    manager.user_loader(load_user)
    app.register_blueprint(kobo.kobo)
    app.register_blueprint(readingservices.readingservices_api_v3)
    app.register_blueprint(annotations.annotations_bp)
    yield SimpleNamespace(app=app, db=database, book=book, user=user)
    database.close()
    engine.dispose()
    annotation_backup.reset_for_tests()


HEADERS = {"x-kobo-deviceid": "a" * 64, "x-kobo-devicemodel": "Kobo Clara"}


@pytest.mark.parametrize("patch_headers", [HEADERS, {}], ids=["repeated-header", "session-only"])
def test_new_kobo_highlight_names_the_device_from_its_login(reader, patch_headers):
    """Losing identity between login and PATCH must fail on the new stored row."""
    from cps import ub

    client = reader.app.test_client()
    response = client.post("/kobo/reader-token/v1/auth/device", headers=HEADERS, json={})
    assert response.status_code == 200
    device = reader.db.query(ub.Device).one()
    assert device.display_name == "Kobo Clara"
    assert reader.db.query(ub.Annotation).count() == 0

    response = client.patch(
        f"/api/v3/content/{reader.book.uuid}/annotations",
        headers=patch_headers,
        json={"updatedAnnotations": [{"id": "new-highlight", "type": "highlight",
                                      "highlightedText": "A newly highlighted passage.",
                                      "clientLastModifiedUtc": "2026-09-07T18:00:00Z",
                                      "location": {"span": {"chapterFilename": "chapter.xhtml",
                                                            "startPath": "span#kobo.1.1",
                                                            "endPath": "span#kobo.1.1",
                                                            "startChar": 0, "endChar": 10}}}]},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    reader.db.expire_all()
    row = reader.db.query(ub.Annotation).one()
    assert row.origin_device_id == device.id, (
        f"new Kobo highlight stored origin_device_id={row.origin_device_id!r}; "
        f"registered Kobo Clara has id={device.id}"
    )
    response = client.get("/annotations/42/data.json")
    assert response.status_code == 200
    body = response.get_json()
    assert body["annotations"][0]["origin_device_id"] == device.public_id
    assert body["devices"][device.public_id]["label"] == "Kobo Clara"


@pytest.mark.parametrize("condition", ["separate-session", "retired", "other-user", "invalid-header"])
def test_session_attribution_requires_a_current_device_owned_by_this_user(reader, condition):
    """An existing Devices row is insufficient without valid session provenance."""
    from cps import ub

    client = reader.app.test_client()
    assert client.post("/kobo/reader-token/v1/auth/device", headers=HEADERS, json={}).status_code == 200
    device = reader.db.query(ub.Device).one()
    if condition == "separate-session":
        client = reader.app.test_client()
        assert client.post("/kobo/reader-token/v1/auth/device", json={}).status_code == 200
    elif condition == "retired":
        device.active = False
        reader.db.commit()
    elif condition == "other-user":
        other = ub.User(name="other", email="other@example.invalid", password="unused", role=0)
        reader.db.add(other)
        reader.db.flush()
        token = ub.RemoteAuthToken()
        token.user_id, token.auth_token, token.token_type = other.id, "other-token", 1
        reader.db.add(token)
        reader.db.commit()
        assert client.post("/kobo/other-token/v1/auth/device", json={}).status_code == 200
    else:
        # Explicit but invalid hardware identity must invalidate the old binding.
        assert client.post("/kobo/reader-token/v1/auth/device",
                           headers={"x-kobo-deviceid": "invalid"}, json={}).status_code == 200

    response = client.patch(
        f"/api/v3/content/{reader.book.uuid}/annotations",
        json={"updatedAnnotations": [{"id": "unattributed-highlight", "type": "highlight",
                                      "highlightedText": "Keep this highlight.",
                                      "clientLastModifiedUtc": "2026-09-07T18:00:00Z",
                                      "location": {"span": {"chapterFilename": "chapter.xhtml",
                                                            "startPath": "span#kobo.1.1",
                                                            "endPath": "span#kobo.1.1",
                                                            "startChar": 0, "endChar": 10}}}]},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    reader.db.expire_all()
    row = reader.db.query(ub.Annotation).one()
    assert row.origin_device_id is None
    assert row.highlighted_text == "Keep this highlight."
