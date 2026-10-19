# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A small library and app.db for the metadata clean-up (value map) API tests.

One Flask app for the module (``app``), an in-memory calibre library with
three books and their tags (``library``), an in-memory app.db holding the map
and change-set tables (``app_db``), and ``call`` to run a real
``cps/api/value_maps.py`` route as a signed-in admin, a plain user or nobody.
File enforcement and the CWA settings read are stubbed; everything else is
the real code.
"""
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest
from flask_babel import Babel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import db, ub
import cps.api.value_maps as routes

ADMIN = SimpleNamespace(is_authenticated=True, is_anonymous=False, id=1, role_admin=lambda: True)
READER = SimpleNamespace(is_authenticated=True, is_anonymous=False, id=2, role_admin=lambda: False)
NOBODY = SimpleNamespace(is_authenticated=False, is_anonymous=True, id=None, role_admin=lambda: False)


@pytest.fixture(scope="module")
def app():
    app = flask.Flask(__name__)
    Babel(app)
    return app


@pytest.fixture
def library():
    """Book 1: "Fiction / Thrillers" + "General"; book 2: "thriller"; book 3: "Dragons"."""
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS calibre")
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.execute(db.Books.__table__.insert(), [
        {"id": i, "title": f"Book {i}", "sort": f"Book {i}", "path": f"b{i}", "author_sort": ""}
        for i in (1, 2, 3)
    ])
    session.execute(db.Tags.__table__.insert(), [
        {"id": 1, "name": "Fiction / Thrillers"}, {"id": 2, "name": "thriller"},
        {"id": 3, "name": "General"}, {"id": 4, "name": "Dragons"},
    ])
    session.execute(db.books_tags_link.insert(), [
        {"book": 1, "tag": 1}, {"book": 1, "tag": 3}, {"book": 2, "tag": 2}, {"book": 3, "tag": 4},
    ])
    session.commit()
    return session


@pytest.fixture
def app_db():
    engine = create_engine("sqlite://")
    ub.Base.metadata.create_all(engine, tables=[
        ub.MetadataValueRule.__table__, ub.MetadataChangeSet.__table__, ub.MetadataChange.__table__])
    return sessionmaker(bind=engine)()


@pytest.fixture
def call(app, library, app_db):
    """``call(route, *args, user=ADMIN, method="GET", json=None)`` -> (status, body)."""
    queued = []

    def run(route, *args, user=ADMIN, method="GET", json=None):
        with (patch.object(routes.calibre_db, "session", library),
              patch.object(routes.ub, "session", app_db),
              patch.object(routes, "current_user", user),
              patch.object(routes, "metadata_db_write_lock"),
              patch.object(routes, "_files_rewritten", lambda: True),
              patch.object(routes.helper, "mark_book_modified"),
              patch.object(routes.helper, "log_metadata_change",
                           lambda book, changed: queued.append((book.id, changed)))):
            with app.test_request_context(method=method, json=json):
                response = route.__wrapped__(*args)
        if isinstance(response, tuple):
            response, status = response
        else:
            status = response.status_code
        return status, response.get_json()

    run.queued = queued
    return run
