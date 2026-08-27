# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #1874 — annotation writers must not acknowledge rolled-back writes.

Every test drives the real caller boundary rather than merely asserting that a
helper raises: Flask routing for the web reader and bulk assignment APIs, and
the KOReader PUT protocol for portable upserts and named deletes.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cps import calibre_db, ub


pytestmark = pytest.mark.unit

annotations = importlib.import_module("cps.annotations")
koreader_annotations = importlib.import_module(
    "cps.progress_syncing.protocols.koreader_annotations"
)
kosync = importlib.import_module("cps.progress_syncing.protocols.kosync")


@pytest.fixture
def database(tmp_path, monkeypatch):
    from cps import constants
    from cps.services import annotation_backup

    annotation_backup.reset_for_tests()
    monkeypatch.setattr(annotation_backup, "WORKER_AUTOSTART", False)
    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))

    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    session.execute(text("PRAGMA foreign_keys=ON"))
    user = ub.User(name="reader", email="reader@example.invalid", role=0, password="x")
    session.add(user)
    session.commit()
    monkeypatch.setattr(ub, "session", session)

    yield session, user

    session.close()
    annotation_backup.reset_for_tests()


def _failed_commit(session):
    def commit():
        # Match ub.session_commit's failure contract, including its rollback.
        session.rollback()
        return False

    return commit


@pytest.fixture
def web_routes(database, monkeypatch):
    session, user = database
    book = SimpleNamespace(
        id=7,
        uuid="b3d1b38b-74fd-43b7-a796-996e5a6a8b04",
        title="Commit Guard",
        data=[],
    )
    monkeypatch.setattr(annotations, "current_user", user, raising=False)
    monkeypatch.setattr(annotations, "_resolve_book_or_404", lambda _book_id: book)

    app = Flask(__name__)
    # The auth decorator is outside this defect. Register the wrapped route
    # bodies so requests still traverse Flask routing, request parsing, and
    # response serialization without fabricating a login-manager stack.
    app.add_url_rule(
        "/annotations/<int:book_id>",
        "annotations_create_1874",
        annotations.annotations_create.__wrapped__,
        methods=["POST"],
    )
    app.add_url_rule(
        "/annotations/<int:book_id>/<annotation_id>",
        "annotations_delete_1874",
        annotations.annotations_delete.__wrapped__,
        methods=["DELETE"],
    )
    app.add_url_rule(
        "/api/annotations/assignments/bulk",
        "annotation_assignments_bulk_1874",
        annotations.annotation_assignments_bulk.__wrapped__,
        methods=["POST"],
    )
    return app.test_client(), session, user, book


@pytest.mark.parametrize(
    "payload",
    [
        {
            "position_type": "unanchored",
            "note_text": "A standalone note",
        },
        {
            "cfi_range": "epubcfi(/6/4!/4/2,/1:0,/1:9)",
            "highlighted_text": "A CFI highlight",
        },
        {
            "start_kobospan": "kobo.1.1",
            "end_kobospan": "kobo.1.1",
            "highlighted_text": "A KoboSpan highlight",
        },
    ],
    ids=["unanchored", "cfi", "kobospan"],
)
def test_web_create_route_reports_each_commit_failure(
    web_routes, monkeypatch, payload,
):
    client, session, _user, _book = web_routes
    fanned_out = []
    monkeypatch.setattr(ub, "session_commit", _failed_commit(session))
    monkeypatch.setattr(
        annotations, "_fanout_to_sync_targets",
        lambda *args: fanned_out.append(args),
    )

    response = client.post("/annotations/7", json=payload)

    assert response.status_code == 500
    assert response.get_json() == {"error": "database_error"}
    assert fanned_out == []
    assert session.query(ub.Annotation).count() == 0


def test_bulk_reassignment_route_reports_commit_failure(web_routes, monkeypatch):
    client, session, user, _book = web_routes
    target = ub.Device(
        user_id=user.id,
        kind="kobo",
        display_name="Target",
        active=True,
        created_by="auto",
    )
    row = ub.Annotation(
        user_id=user.id,
        book_id=7,
        annotation_id="reassign-me",
        source="webreader",
    )
    session.add_all([target, row])
    session.commit()
    monkeypatch.setattr(ub, "session_commit", _failed_commit(session))

    response = client.post("/api/annotations/assignments/bulk", json={
        "assigned_device_id": target.public_id,
        "items": [{
            "book_id": 7,
            "annotation_id": row.annotation_id,
            "expected_routing_revision": 1,
        }],
    })

    assert response.status_code == 200
    assert response.get_json() == {"results": [{
        "annotation_id": "reassign-me",
        "ok": False,
        "error_code": "database_error",
    }]}
    session.refresh(row)
    assert row.assigned_device_id is None


def test_web_delete_route_reports_commit_failure_before_fanout(
    web_routes, monkeypatch,
):
    client, session, user, _book = web_routes
    row = ub.Annotation(
        user_id=user.id,
        book_id=7,
        annotation_id="delete-me",
        source="webreader",
        hidden=False,
    )
    session.add(row)
    session.commit()
    monkeypatch.setattr(ub, "session_commit", _failed_commit(session))

    from cps.services import annotation_sync
    fanned_out = []
    monkeypatch.setattr(
        annotation_sync,
        "dispatch_annotation_deletes",
        lambda *args, **kwargs: fanned_out.append((args, kwargs)),
    )

    response = client.delete("/annotations/7/delete-me")

    assert response.status_code == 500
    assert response.get_json() == {"error": "database_error"}
    assert fanned_out == []
    session.refresh(row)
    assert row.hidden is False


@pytest.fixture
def koreader_route(database, monkeypatch):
    session, user = database
    book = SimpleNamespace(
        id=7,
        uuid="b3d1b38b-74fd-43b7-a796-996e5a6a8b04",
        title="Commit Guard",
    )
    monkeypatch.setattr(koreader_annotations, "_require_kosync_enabled", lambda: None)
    monkeypatch.setattr(koreader_annotations, "authenticate_user", lambda: user)
    monkeypatch.setattr(
        koreader_annotations,
        "get_book_by_checksum",
        lambda document: (book.id, "EPUB", book.title, "book.epub", "koreader"),
    )
    monkeypatch.setattr(calibre_db, "get_book", lambda _book_id: book)

    app = Flask(__name__)
    app.register_blueprint(kosync.kosync)
    return app.test_client(), session, user, book


def test_koreader_route_reports_apply_portable_commit_failure(
    koreader_route, monkeypatch,
):
    client, session, _user, _book = koreader_route
    monkeypatch.setattr(ub, "session_commit", _failed_commit(session))

    from cps.services import annotation_sync
    fanned_out = []
    monkeypatch.setattr(
        annotation_sync,
        "dispatch_existing_annotation_sync",
        lambda *args, **kwargs: fanned_out.append((args, kwargs)),
    )

    response = client.put("/kosync/syncs/annotations", json={
        "document": "digest-1874",
        "annotations": [{
            "annotation_id": "portable-create",
            "highlighted_text": "Must land before acknowledgement",
        }],
    })

    assert response.status_code == 503
    assert response.get_json() == {"error": 2000, "message": "Database error"}
    assert fanned_out == []
    assert session.query(ub.Annotation).count() == 0


def test_koreader_route_reports_named_delete_commit_failure_before_fanout(
    koreader_route, monkeypatch,
):
    client, session, user, _book = koreader_route
    row = ub.Annotation(
        user_id=user.id,
        book_id=7,
        annotation_id="portable-delete",
        source="koreader",
        hidden=False,
    )
    session.add(row)
    session.commit()
    monkeypatch.setattr(ub, "session_commit", _failed_commit(session))

    from cps.services import annotation_sync
    fanned_out = []
    monkeypatch.setattr(
        annotation_sync,
        "dispatch_annotation_deletes",
        lambda *args, **kwargs: fanned_out.append((args, kwargs)),
    )

    response = client.put("/kosync/syncs/annotations", json={
        "document": "digest-1874",
        "annotations": [],
        "deleted": ["portable-delete"],
    })

    assert response.status_code == 503
    assert response.get_json() == {"error": 2000, "message": "Database error"}
    assert fanned_out == []
    session.refresh(row)
    assert row.hidden is False
