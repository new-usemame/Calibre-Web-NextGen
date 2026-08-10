# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The create/edit routes must resolve device ids the way ``data.json`` does.

``_data_json_row`` translates the internal ``origin_device_id`` /
``assigned_device_id`` integers to public ids through a ``device_public_ids``
map, and defaults that map to ``{}``. ``data.json`` passes it; the create and
edit routes originally did not, so a row that HAD been attributed answered
``origin_device_id: null`` on the very response the reader inserts
optimistically — the one highlight the user just watched itself be created
rendered as "Unknown device" until a refetch.

Caught by comparing the wire against the database rather than trusting either:
the POST response said ``null`` while the stored row said ``1``. The write
landed and the report said it did not.

Routed through a bare Flask app with ``current_user`` patched — the house
pattern (see ``test_cover_preview_endpoints.py``) — so this pins the route's
own serialisation without books, kepubs, or the login machinery.
"""

from __future__ import annotations

from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def session():
    from cps import ub
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
    ub.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    s = Session()
    original = ub.session
    ub.session = s
    try:
        yield s
    finally:
        ub.session = original


@pytest.fixture
def attributed_row(session):
    """A user owning one device, and an annotation attributed to it."""
    from cps import ub
    device = ub.Device(
        public_id="pub-device-1", user_id=1, kind="webreader",
        display_name="Web reader", model="CWNG web reader", platform="epub.js",
        active=True, created_by="auto",
    )
    session.add(device)
    session.flush()
    row = ub.Annotation(
        user_id=1, book_id=223, annotation_id="cwn-web-probe",
        source="webreader", highlighted_text="probe",
        origin_device_id=device.id, assigned_device_id=device.id,
        routing_revision=1,
    )
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def app():
    from cps.annotations import annotations_bp
    a = flask.Flask(__name__)
    a.config["TESTING"] = True
    a.register_blueprint(annotations_bp)
    return a


def _call_route(app, monkeypatch, view_name, row, **view_kwargs):
    """Invoke a route function directly with auth + book resolution stubbed."""
    from cps import annotations as ann
    monkeypatch.setattr(ann, "current_user", SimpleNamespace(id=1), raising=False)
    monkeypatch.setattr(ann, "_resolve_book_or_404", lambda book_id: SimpleNamespace(id=book_id, uuid="u"))
    monkeypatch.setattr(ann, "_fanout_to_sync_targets", lambda *a, **k: None)
    monkeypatch.setattr(ann, "create_annotation", lambda *a, **k: row)
    monkeypatch.setattr(ann, "edit_annotation", lambda *a, **k: row)
    view = app.view_functions[f"annotations.{view_name}"]
    # __wrapped__ skips @user_login_required without faking a session.
    fn = getattr(view, "__wrapped__", view)
    with app.test_request_context(json={}):
        return fn(**view_kwargs)


def test_create_response_resolves_origin_device_to_its_public_id(app, monkeypatch, attributed_row):
    """The create response must name the device, not answer null for it."""
    response, status = _call_route(app, monkeypatch, "annotations_create", attributed_row, book_id=223)
    body = response.get_json()
    assert status == 201
    assert body["origin_device_id"] == "pub-device-1", (
        "create answered a null/internal origin — the reader would render "
        "'Unknown device' for a row that IS attributed"
    )
    assert body["assigned_device_id"] == "pub-device-1"


def test_edit_response_resolves_both_device_fields(app, monkeypatch, attributed_row):
    """Edit patched assigned_device_id back in by hand and left origin null."""
    response, status = _call_route(
        app, monkeypatch, "annotations_edit", attributed_row,
        book_id=223, annotation_id="cwn-web-probe",
    )
    body = response.get_json()
    assert status == 200
    assert body["origin_device_id"] == "pub-device-1"
    assert body["assigned_device_id"] == "pub-device-1"
    assert body["routing_revision"] == 1


def test_unresolvable_device_falls_back_to_null_rather_than_dropping_the_row(
    app, monkeypatch, session, attributed_row,
):
    """A dangling origin renders unlabelled; it must never hide the annotation.

    Soft-deleting a device deliberately leaves origin_device_id pointing at it.
    If resolution were ever expressed as a filter instead of a lookup, the
    user's highlight would vanish from their own reader.
    """
    attributed_row.origin_device_id = 9999  # no such device
    session.commit()
    response, status = _call_route(app, monkeypatch, "annotations_create", attributed_row, book_id=223)
    body = response.get_json()
    assert status == 201
    assert body["origin_device_id"] is None
    assert body["highlighted_text"] == "probe"
