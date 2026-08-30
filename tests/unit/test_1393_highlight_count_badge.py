# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for fork issue #1393's book-detail annotation count."""

from __future__ import annotations

import datetime
import inspect
import json
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


BOOK_ID = 42


def _book():
    return SimpleNamespace(
        id=BOOK_ID,
        title="Dune",
        series_index="1.0",
        has_cover=0,
        authors=[SimpleNamespace(id=7, name="Frank Herbert")],
        series=[],
        ratings=[],
        data=[],
        comments=[],
        tags=[],
        languages=[],
        publishers=[],
        identifiers=[],
        pubdate=datetime.datetime(1965, 8, 1),
    )


def _user(user_id):
    return SimpleNamespace(
        id=user_id,
        is_authenticated=True,
        is_anonymous=False,
        has_own_library=False,
        role_browse_global=lambda: False,
    )


def _annotation(session, *, user_id, book_id=BOOK_ID, annotation_id, hidden=False):
    from cps import ub

    session.add(ub.Annotation(
        user_id=user_id,
        book_id=book_id,
        annotation_id=annotation_id,
        highlighted_text=annotation_id,
        hidden=hidden,
    ))


@pytest.fixture
def app_db(monkeypatch, tmp_path):
    from cps import constants, ub
    from cps.services import annotation_backup

    annotation_backup.reset_for_tests()
    monkeypatch.setattr(annotation_backup, "WORKER_AUTOSTART", False)
    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    monkeypatch.setattr(ub, "session", session)
    yield session
    session.close()
    engine.dispose()
    annotation_backup.reset_for_tests()


def _detail_payload(user):
    from cps.api import books as books_mod

    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_request_context(f"/api/v1/books/{BOOK_ID}"):
        with patch.object(
            books_mod.calibre_db, "get_book_read_archived",
            return_value=(_book(), None, False),
        ), \
        patch.object(books_mod.config, "config_read_column", 0, create=True), \
        patch.object(books_mod.calibre_db, "get_cc_columns", return_value=[]), \
        patch.object(books_mod, "current_user", user), \
        patch.object(books_mod, "get_convert_options", return_value=([], [])), \
        patch("cps.api.books.get_locale", return_value="en"), \
        patch("cps.api.books.isoLanguages.get_language_name", return_value="English"):
            response = inspect.unwrap(books_mod.book_detail)(BOOK_ID)

    assert response.status_code == 200
    return json.loads(response.get_data(as_text=True))


@pytest.mark.unit
def test_book_detail_counts_only_the_current_users_visible_annotations(app_db):
    _annotation(app_db, user_id=7, annotation_id="mine-1")
    _annotation(app_db, user_id=7, annotation_id="mine-2")
    _annotation(app_db, user_id=7, annotation_id="soft-deleted", hidden=True)
    _annotation(app_db, user_id=7, book_id=99, annotation_id="other-book")
    _annotation(app_db, user_id=8, annotation_id="other-user")
    app_db.commit()

    assert _detail_payload(_user(7))["annotation_count"] == 2
    assert _detail_payload(_user(9))["annotation_count"] == 0


@pytest.mark.unit
def test_anonymous_book_detail_returns_zero_without_querying_annotations(app_db):
    from cps.api import books as books_mod

    anonymous = SimpleNamespace(
        id=None,
        is_authenticated=False,
        is_anonymous=True,
        has_own_library=False,
        role_browse_global=lambda: False,
    )
    query = app_db.query

    def reject_annotation_query(entity, *entities, **kwargs):
        from cps import ub

        assert entity is not ub.Annotation, "anonymous detail queried annotations"
        return query(entity, *entities, **kwargs)

    with patch.object(books_mod.ub.session, "query", side_effect=reject_annotation_query):
        assert _detail_payload(anonymous)["annotation_count"] == 0
