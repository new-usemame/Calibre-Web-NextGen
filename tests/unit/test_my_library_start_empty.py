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
from tests.unit.api_basic_auth_world import build_api_app

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


def test_start_empty_refuses_dormant_rows_from_an_interrupted_seed(
        app_session, calibre_session, monkeypatch):
    """A failed first enable can commit rows without setting the seed fence.

    Empty start must not silently keep those books or erase a selection.
    """
    user = _fresh_user(app_session, "interrupted-seed")
    monkeypatch.setattr(ub, "session", app_session)
    user_library.prepare_user_library_seed(
        user, app_session=app_session, cdb=_cdb(calibre_session))
    assert not user.user_library_seeded
    assert _membership(app_session, user) == [1, 2, 3]

    with pytest.raises(user_library.UserLibraryError, match="already has"):
        user_library.set_library_mode(
            user, constants.LIBRARY_MODE_PERSONAL,
            app_session=app_session, start_empty=True)

    app_session.rollback()
    assert user.library_mode() == constants.LIBRARY_MODE_MONOLIBRARY
    assert not user.user_library_seeded
    assert _membership(app_session, user) == [1, 2, 3]


# ── Admin API ───────────────────────────────────────────────────────────────

def _post_update(app_session, monkeypatch, target, body):
    from cps import config

    administrator = ub.User(
        name="start-empty-admin", email="start-empty-admin@example.invalid",
        password="", role=constants.ROLE_ADMIN, default_language="all",
    )
    app_session.add(administrator)
    app_session.commit()
    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(config, "config_anonbrowse", 0, raising=False)
    monkeypatch.setattr(config, "config_allow_reverse_proxy_header_login", False,
                        raising=False)
    app = build_api_app(app_session)
    client = app.test_client()
    with client.session_transaction() as cookie:
        cookie.update(_user_id=str(administrator.id), _random="test",
                      _id="test", _fresh=True)
    token = client.get("/api/v1/auth/csrf").json["csrf_token"]
    response = client.post(
        "/api/v1/admin/users/%d" % target.id, json=body,
        headers={"X-CSRFToken": token})
    return response.status_code, response.get_json()


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
        "email": "changed@example.invalid",
        "roles": {"viewer": True},
    })

    assert status == 400
    assert payload["error"]["code"] == "library_mode_rejected"
    app_session.expire_all()
    assert target.library_mode() == constants.LIBRARY_MODE_MONOLIBRARY
    assert target.user_library_seeded is False
    assert target.email == "api-managed@example.invalid"
    assert not target.role_viewer()


@pytest.mark.parametrize("state", ["kobo", "seeded", "dormant"])
def test_admin_api_rejected_empty_start_rolls_back_other_edits(
        app_session, monkeypatch, state):
    """Refusals must preserve profile fields and every existing book row."""
    target = _fresh_user(app_session, "api-existing")
    target.user_library_seeded = state == "seeded"
    if state == "kobo":
        app_session.add(ub.KoboSyncedBooks(user_id=target.id, book_id=1))
    else:
        app_session.add(ub.UserLibraryBook(user_id=target.id, book_id=2))
    app_session.commit()
    before = _membership(app_session, target)

    status, payload = _post_update(app_session, monkeypatch, target, {
        "library_mode": constants.LIBRARY_MODE_PERSONAL, "start_empty": True,
        "email": "changed@example.invalid", "roles": {"viewer": True},
    })

    assert status == 400
    assert payload["error"]["code"] == "library_mode_rejected"
    app_session.expire_all()
    assert target.library_mode() == constants.LIBRARY_MODE_MONOLIBRARY
    assert target.user_library_seeded == (state == "seeded")
    assert target.email == "api-existing@example.invalid"
    assert not target.role_viewer()
    assert _membership(app_session, target) == before
    if state == "kobo":
        assert app_session.query(ub.KoboSyncedBooks).filter_by(
            user_id=target.id, book_id=1).count() == 1


@pytest.mark.parametrize("seeded", [False, True])
def test_classic_empty_start_is_saved_or_stale_form_is_refused(
        app_session, monkeypatch, seeded):
    """A stale empty-start form must reject all edits, preserving selection."""
    from flask import get_flashed_messages
    from cps import admin

    target = _fresh_user(app_session, "classic-empty")
    target.user_library_seeded = seeded
    if seeded:
        app_session.add(ub.UserLibraryBook(user_id=target.id, book_id=2))
    administrator = ub.User(name="classic-admin", email="admin@example.invalid",
                            password="", role=constants.ROLE_ADMIN)
    app_session.add(administrator)
    app_session.commit()
    original_email = target.email
    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(admin, "get_sidebar_config", lambda: ([], None))
    monkeypatch.setattr(admin, "get_custom_column_visibility_options", lambda _u: [])
    monkeypatch.setattr(admin, "save_cc_visibility", lambda *_a: None)
    monkeypatch.setattr(admin, "_", lambda value, **kw: value % kw if kw else value)
    app = build_api_app(app_session)
    form = {"email": "changed@example.invalid", "kindle_mail": "",
            "browse_global_role": "on", "library_mode": "personal_library",
            "library_start_empty": "on"}
    with app.test_request_context("/admin/user/%d" % target.id,
                                  method="POST", data=form):
        admin._handle_edit_user(form, target, [], [], False)
        messages = get_flashed_messages(with_categories=True)

    app_session.expire_all()
    assert target.user_library_seeded is True
    if seeded:
        assert target.library_mode() == constants.LIBRARY_MODE_MONOLIBRARY
        assert target.email == original_email
        assert _membership(app_session, target) == [2]
        assert any(category == "error" and "first time" in text
                   for category, text in messages), messages
    else:
        assert target.library_mode() == constants.LIBRARY_MODE_PERSONAL
        assert target.email == "changed@example.invalid"
        assert _membership(app_session, target) == []
        assert any(category == "success" for category, _ in messages), messages
