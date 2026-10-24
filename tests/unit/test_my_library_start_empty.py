# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""An administrator can start a user's My Library empty (#2352).

The default first switch seeds the library with everything the account can
see, which is mandatory for an account with an e-reader. These tests cover the
explicit alternative and the cases where it must be refused.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import constants, db, ub, user_library

pytestmark = pytest.mark.unit


def _book(book_id, title):
    now = datetime.now(timezone.utc)
    book = db.Books(title, title, "Author", now, now, "1.0", now,
                    "book-%d" % book_id, 1, [], [])
    book.id = book_id
    return book


@pytest.fixture
def app_session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def calibre_session():
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([_book(1, "One"), _book(2, "Two"), _book(3, "Three")])
    session.commit()
    yield session
    session.close()


def _cdb(calibre_session):
    instance = object.__new__(db.CalibreDB)
    instance.session = calibre_session
    instance.config = SimpleNamespace(config_restricted_column=0)
    return instance


def _fresh_user(session, name, *, browse_global=True):
    user = ub.User(name=name, email="%s@example.invalid" % name, password="",
                   default_language="all",
                   role=constants.ROLE_BROWSE_GLOBAL if browse_global else 0)
    session.add(user)
    session.commit()
    return user


def _membership(session, user):
    return [row.book_id for row in session.query(ub.UserLibraryBook)
            .filter_by(user_id=user.id).order_by(ub.UserLibraryBook.book_id)]


def test_default_switch_still_seeds_everything(
        app_session, calibre_session, monkeypatch):
    user = _fresh_user(app_session, "default-seed")
    monkeypatch.setattr(db.ub, "session", app_session)

    user_library.set_library_mode(
        user, constants.LIBRARY_MODE_PERSONAL,
        app_session=app_session, cdb=_cdb(calibre_session),
    )

    assert _membership(app_session, user) == [1, 2, 3]


def test_start_empty_enables_personal_mode_with_no_rows(
        app_session, calibre_session, monkeypatch):
    user = _fresh_user(app_session, "empty-start")
    monkeypatch.setattr(db.ub, "session", app_session)

    mode = user_library.set_library_mode(
        user, constants.LIBRARY_MODE_PERSONAL,
        app_session=app_session, cdb=_cdb(calibre_session),
        start_empty=True,
    )

    assert mode == constants.LIBRARY_MODE_PERSONAL
    assert user.user_library_seeded is True
    assert _membership(app_session, user) == []


def test_empty_start_is_remembered_not_reseeded_on_later_toggle(
        app_session, calibre_session, monkeypatch):
    user = _fresh_user(app_session, "empty-then-toggle")
    cdb = _cdb(calibre_session)
    monkeypatch.setattr(db.ub, "session", app_session)
    user_library.set_library_mode(
        user, constants.LIBRARY_MODE_PERSONAL,
        app_session=app_session, cdb=cdb, start_empty=True,
    )
    app_session.add(ub.UserLibraryBook(user_id=user.id, book_id=2))
    app_session.commit()

    user_library.set_library_mode(
        user, constants.LIBRARY_MODE_MONOLIBRARY, app_session=app_session)
    user_library.set_library_mode(
        user, constants.LIBRARY_MODE_PERSONAL,
        app_session=app_session, cdb=cdb)

    assert _membership(app_session, user) == [2]


def test_start_empty_refused_without_global_browse(
        app_session, calibre_session):
    user = _fresh_user(app_session, "managed", browse_global=False)

    with pytest.raises(user_library.UserLibraryError, match="global-browse"):
        user_library.set_library_mode(
            user, constants.LIBRARY_MODE_PERSONAL,
            app_session=app_session, cdb=_cdb(calibre_session),
            start_empty=True,
        )

    app_session.rollback()
    assert user.user_library_seeded is False
    assert user.library_mode() == constants.LIBRARY_MODE_MONOLIBRARY
    assert _membership(app_session, user) == []


def test_start_empty_refused_once_account_has_a_selection(
        app_session, calibre_session, monkeypatch):
    user = _fresh_user(app_session, "already-seeded")
    cdb = _cdb(calibre_session)
    monkeypatch.setattr(db.ub, "session", app_session)
    user_library.set_library_mode(
        user, constants.LIBRARY_MODE_PERSONAL,
        app_session=app_session, cdb=cdb)
    assert _membership(app_session, user) == [1, 2, 3]

    with pytest.raises(user_library.UserLibraryError, match="first time"):
        user_library.set_library_mode(
            user, constants.LIBRARY_MODE_PERSONAL,
            app_session=app_session, cdb=cdb, start_empty=True,
        )

    assert _membership(app_session, user) == [1, 2, 3]


def test_start_empty_refused_for_an_account_that_syncs_a_kobo(
        app_session, calibre_session):
    user = _fresh_user(app_session, "kobo-reader")
    app_session.add(ub.KoboSyncedBooks(user_id=user.id, book_id=1))
    app_session.commit()

    with pytest.raises(user_library.UserLibraryError, match="Kobo"):
        user_library.set_library_mode(
            user, constants.LIBRARY_MODE_PERSONAL,
            app_session=app_session, cdb=_cdb(calibre_session),
            start_empty=True,
        )

    app_session.rollback()
    assert user.user_library_seeded is False
    assert user.library_mode() == constants.LIBRARY_MODE_MONOLIBRARY


def test_start_empty_is_meaningless_when_switching_to_monolibrary(
        app_session, calibre_session):
    user = _fresh_user(app_session, "wrong-direction")

    with pytest.raises(user_library.UserLibraryError, match="switching to"):
        user_library.set_library_mode(
            user, constants.LIBRARY_MODE_MONOLIBRARY,
            app_session=app_session, cdb=_cdb(calibre_session),
            start_empty=True,
        )


# ── Admin API ───────────────────────────────────────────────────────────────

def _post_update(app_session, monkeypatch, target, body):
    from flask import Flask
    from cps.api import admin as api_admin

    administrator = ub.User(
        name="start-empty-admin", email="start-empty-admin@example.invalid",
        password="", role=constants.ROLE_ADMIN, default_language="all",
    )
    app_session.add(administrator)
    app_session.commit()
    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(api_admin, "current_user", administrator)
    app = Flask(__name__)
    with app.test_request_context(
            "/api/v1/admin/users/%d" % target.id, method="POST", json=body):
        result = api_admin.admin_update_user.__wrapped__(target.id)
    if isinstance(result, tuple):
        response, status = result
        return status, response.get_json()
    return result.status_code, result.get_json()


def test_admin_api_start_empty_creates_an_empty_personal_library(
        app_session, monkeypatch):
    target = _fresh_user(app_session, "api-empty")

    status, payload = _post_update(app_session, monkeypatch, target, {
        "library_mode": constants.LIBRARY_MODE_PERSONAL,
        "start_empty": True,
    })

    assert status == 200
    assert payload["library_mode"] == "personal_library"
    assert payload["my_library_seeded"] is True
    assert _membership(app_session, target) == []


def test_admin_api_start_empty_needs_a_library_mode(
        app_session, monkeypatch):
    target = _fresh_user(app_session, "api-no-mode")

    status, payload = _post_update(
        app_session, monkeypatch, target, {"start_empty": True})

    assert status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert target.library_mode() == constants.LIBRARY_MODE_MONOLIBRARY


def test_admin_api_start_empty_must_be_a_boolean(app_session, monkeypatch):
    target = _fresh_user(app_session, "api-not-bool")

    status, payload = _post_update(app_session, monkeypatch, target, {
        "library_mode": constants.LIBRARY_MODE_PERSONAL,
        "start_empty": "yes",
    })

    assert status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert _membership(app_session, target) == []


def test_admin_api_start_empty_refused_without_global_browse(
        app_session, monkeypatch):
    target = _fresh_user(app_session, "api-managed", browse_global=False)

    status, payload = _post_update(app_session, monkeypatch, target, {
        "library_mode": constants.LIBRARY_MODE_PERSONAL,
        "start_empty": True,
    })

    assert status == 400
    assert payload["error"]["code"] == "library_mode_rejected"
    app_session.expire_all()
    assert target.library_mode() == constants.LIBRARY_MODE_MONOLIBRARY
    assert target.user_library_seeded is False
