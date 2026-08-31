# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Server-side My Library invariants for user-initiated shelf additions."""

from datetime import datetime, timezone
import inspect

import pytest
from flask import Flask
from sqlalchemy import create_engine, event, true
from sqlalchemy.orm import sessionmaker

from cps import constants, db, ub


pytestmark = pytest.mark.unit

MANAGED_REFUSAL = (
    "This book is not in your library. Ask an administrator to add it to "
    "My Library before adding it to a shelf."
)
MANAGED_OWNER_REFUSAL = (
    "This book is not in the shelf owner's library. The shelf owner cannot "
    "browse the global library; an administrator must add it to their My "
    "Library first."
)
GENERIC_OWNER_REFUSAL = (
    "This book cannot be added to this shelf. Ask an administrator for help."
)
INVALID_OWNER_REFUSAL = (
    "This shelf has an invalid owner. Ask an administrator to repair it "
    "before adding books."
)


def _book(book_id, title):
    now = datetime.now(timezone.utc)
    book = db.Books(
        title, title, "Author", now, db.Books.DEFAULT_PUBDATE,
        "1.0", now, "book-%d" % book_id, 0, [], [],
    )
    book.id = book_id
    book.uuid = "book-uuid-%d" % book_id
    return book


@pytest.fixture
def shelf_server(monkeypatch):
    """One attached SQLite connection for app.db and metadata.db models."""
    from cps import shelf as shelf_module, user_library
    from cps.api import shelves as shelves_api

    engine = create_engine("sqlite://")
    event.listen(
        engine,
        "connect",
        lambda connection, _record: connection.execute(
            "ATTACH DATABASE ':memory:' AS calibre"
        ),
    )
    ub.Base.metadata.create_all(engine)
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    user = ub.User(
        name="shelf-reader",
        email="shelf-reader@example.invalid",
        password="",
        role=constants.ROLE_BROWSE_GLOBAL,
        has_own_library=True,
        user_library_seeded=True,
        default_language="all",
    )
    session.add(user)
    session.flush()
    shelf = ub.Shelf(id=9, name="Read later", is_public=0, user_id=user.id)
    session.add_all([shelf, _book(1, "Already mine"), _book(2, "Global only")])
    session.add(ub.UserLibraryBook(user_id=user.id, book_id=1))
    session.commit()

    def common_filters(**kwargs):
        if kwargs.get("allow_show_global"):
            return true()
        return db.Books.id.in_(
            session.query(ub.UserLibraryBook.book_id).filter(
                ub.UserLibraryBook.user_id == int(user.id)
            )
        )

    cdb = type("TestCalibreDB", (), {
        "session": session,
        "common_filters": staticmethod(common_filters),
    })()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(shelf_module, "calibre_db", cdb)
    monkeypatch.setattr(user_library, "calibre_db", cdb)
    monkeypatch.setattr(shelf_module, "current_user", user)
    monkeypatch.setattr(shelves_api, "current_user", user)
    monkeypatch.setattr(shelf_module, "_log_shelf_activity", lambda *_args: None)
    monkeypatch.setattr(shelf_module, "queue_hardcover_sync", lambda *_args: None)
    monkeypatch.setattr(shelf_module, "_", lambda text, **values: text % values if values else text)

    app = Flask(__name__)
    app.secret_key = "shelf-membership-test"
    app.add_url_rule("/", endpoint="web.index", view_func=lambda: "index")
    app.add_url_rule(
        "/api/v1/shelves/<int:shelf_id>/books/<int:book_id>",
        endpoint="api_shelf_add",
        view_func=inspect.unwrap(shelves_api.add_book_to_shelf_api),
        methods=["POST"],
    )
    app.add_url_rule(
        "/shelf/add/<int:shelf_id>/<int:book_id>",
        endpoint="classic_shelf_add",
        view_func=inspect.unwrap(shelf_module.add_to_shelf),
        methods=["POST"],
    )

    yield app, session, user, shelf

    session.close()
    engine.dispose()


def _membership_count(session, user_id, book_id):
    return session.query(ub.UserLibraryBook).filter_by(
        user_id=user_id, book_id=book_id,
    ).count()


def _shelf_count(session, shelf_id, book_id):
    return session.query(ub.BookShelf).filter_by(
        shelf=shelf_id, book_id=book_id,
    ).count()


def test_owner_add_to_own_shelf_grants_membership(shelf_server):
    """An owner gesture keeps the documented membership-first invariant."""
    app, session, user, shelf = shelf_server

    response = app.test_client().post("/api/v1/shelves/9/books/2")

    assert response.status_code == 200, response.get_json()
    assert response.get_json() == {
        "book_id": 2,
        "on_shelf": True,
        "shelf_id": 9,
    }
    assert _membership_count(session, user.id, 2) == 1
    assert _shelf_count(session, shelf.id, 2) == 1


def test_non_owner_add_grants_membership_to_shelf_owner_only(shelf_server):
    """Curating another user's public shelf must not grow the curator's set."""
    app, session, actor, shelf = shelf_server
    owner = ub.User(
        name="shelf-owner",
        email="shelf-owner@example.invalid",
        password="",
        role=constants.ROLE_BROWSE_GLOBAL,
        has_own_library=True,
        user_library_seeded=True,
        default_language="all",
    )
    session.add(owner)
    session.flush()
    shelf.user_id = owner.id
    shelf.is_public = 1
    actor.role |= constants.ROLE_EDIT_SHELFS
    session.commit()

    response = app.test_client().post("/api/v1/shelves/9/books/2")

    assert response.status_code == 200, response.get_json()
    assert _membership_count(session, actor.id, 2) == 0
    assert _membership_count(session, owner.id, 2) == 1
    assert _shelf_count(session, shelf.id, 2) == 1


def test_non_owner_non_admin_refusal_discloses_no_owner_facts(shelf_server, monkeypatch):
    from cps import shelf as shelf_module
    from cps.api import shelves as shelves_api

    app, session, actor, shelf = shelf_server
    owner = ub.User(
        name="managed-owner",
        email="managed-owner@example.invalid",
        password="",
        role=0,
        has_own_library=True,
        user_library_seeded=True,
        default_language="all",
    )
    session.add(owner)
    session.flush()
    shelf.user_id = owner.id
    shelf.is_public = 1
    actor.role |= constants.ROLE_EDIT_SHELFS
    session.commit()
    monkeypatch.setattr(shelf_module, "current_user", actor)
    monkeypatch.setattr(shelves_api, "current_user", actor)

    response = app.test_client().post("/api/v1/shelves/9/books/2")

    assert response.status_code == 403, response.get_json()
    assert response.get_json() == {
        "error": {
            "code": "library_membership_rejected",
            "message": GENERIC_OWNER_REFUSAL,
        }
    }
    response_message = response.get_json()["error"]["message"].lower()
    assert "owner" not in response_message
    assert "library" not in response_message
    assert "global" not in response_message
    assert _membership_count(session, actor.id, 2) == 0
    assert _membership_count(session, owner.id, 2) == 0
    assert _shelf_count(session, shelf.id, 2) == 0


def test_non_owner_admin_keeps_actionable_managed_owner_refusal(shelf_server, monkeypatch):
    from cps import shelf as shelf_module, user_library
    from cps.api import shelves as shelves_api

    app, session, actor, shelf = shelf_server
    owner = ub.User(
        name="managed-owner",
        email="managed-owner@example.invalid",
        password="",
        role=0,
        has_own_library=True,
        user_library_seeded=True,
        default_language="all",
    )
    session.add(owner)
    session.flush()
    shelf.user_id = owner.id
    shelf.is_public = 1
    actor.role |= constants.ROLE_ADMIN | constants.ROLE_EDIT_SHELFS
    session.commit()
    monkeypatch.setattr(shelf_module, "current_user", actor)
    monkeypatch.setattr(shelves_api, "current_user", actor)

    with pytest.raises(user_library.UserLibraryError) as refusal:
        shelf_module.prepare_user_shelf_add(shelf, 2)
    assert str(refusal.value) == MANAGED_OWNER_REFUSAL
    assert getattr(refusal.value, "reason", None) == "owner_managed_membership"

    response = app.test_client().post("/api/v1/shelves/9/books/2")

    assert response.status_code == 403, response.get_json()
    assert response.get_json() == {
        "error": {
            "code": "library_membership_rejected",
            "message": MANAGED_OWNER_REFUSAL,
        }
    }
    assert _membership_count(session, actor.id, 2) == 0
    assert _membership_count(session, owner.id, 2) == 0
    assert _shelf_count(session, shelf.id, 2) == 0


def test_smart_shelf_add_gesture_grants_no_membership(shelf_server):
    from cps import shelf as shelf_module

    _app, session, actor, _shelf = shelf_server
    smart_shelf = ub.MagicShelf(
        name="Recent books",
        user_id=actor.id,
        rules={"match": "all", "rules": []},
    )
    session.add(smart_shelf)
    session.commit()

    shelf_module.prepare_user_shelf_add(smart_shelf, 2)

    assert _membership_count(session, actor.id, 2) == 0


def test_ownerless_shelf_add_does_not_grant_membership(shelf_server):
    app, session, actor, shelf = shelf_server
    shelf.user_id = None
    shelf.is_public = 1
    actor.role |= constants.ROLE_EDIT_SHELFS
    session.commit()

    response = app.test_client().post("/api/v1/shelves/9/books/2")

    assert response.status_code == 200, response.get_json()
    assert _membership_count(session, actor.id, 2) == 0
    assert _shelf_count(session, shelf.id, 2) == 1


def test_owner_on_own_shelf_managed_refusal_is_unchanged(shelf_server):
    from cps import shelf as shelf_module, user_library

    app, session, user, shelf = shelf_server
    user.role &= ~constants.ROLE_BROWSE_GLOBAL
    session.commit()

    with pytest.raises(user_library.UserLibraryError) as refusal:
        shelf_module.prepare_user_shelf_add(shelf, 2)
    assert str(refusal.value) == MANAGED_REFUSAL
    assert getattr(refusal.value, "reason", None) == "self_managed_membership"

    response = app.test_client().post("/api/v1/shelves/9/books/2")

    assert response.status_code == 403, response.get_json()
    assert response.get_json() == {
        "error": {
            "code": "library_membership_rejected",
            "message": MANAGED_REFUSAL,
        }
    }
    assert _membership_count(session, user.id, 2) == 0
    assert _shelf_count(session, shelf.id, 2) == 0


def test_numeric_dangling_owner_is_refused_without_shelf_insert(shelf_server):
    app, session, actor, shelf = shelf_server
    shelf.user_id = 9999
    shelf.is_public = 1
    actor.role |= constants.ROLE_EDIT_SHELFS
    session.commit()

    response = app.test_client().post("/api/v1/shelves/9/books/2")

    assert response.status_code == 403, response.get_json()
    assert response.get_json() == {
        "error": {
            "code": "invalid_shelf_owner",
            "message": INVALID_OWNER_REFUSAL,
        }
    }
    assert _membership_count(session, actor.id, 2) == 0
    assert _shelf_count(session, shelf.id, 2) == 0


def test_api_refuses_unresolved_owner_raised_by_shared_add_core(shelf_server, monkeypatch):
    from cps.api import shelves as shelves_api

    app, session, actor, shelf = shelf_server
    shelf.user_id = 9999
    shelf.is_public = 1
    actor.role |= constants.ROLE_EDIT_SHELFS
    session.commit()
    monkeypatch.setattr(shelves_api, "prepare_user_shelf_add", lambda *_args: None)

    response = app.test_client().post("/api/v1/shelves/9/books/2")

    assert response.status_code == 403, response.get_json()
    assert response.get_json() == {
        "error": {
            "code": "invalid_shelf_owner",
            "message": INVALID_OWNER_REFUSAL,
        }
    }
    assert _membership_count(session, actor.id, 2) == 0
    assert _shelf_count(session, shelf.id, 2) == 0


def test_non_numeric_owner_is_classic_refusal_not_server_error(shelf_server):
    app, session, actor, shelf = shelf_server
    shelf.user_id = "not-an-integer"
    shelf.is_public = 1
    actor.role |= constants.ROLE_EDIT_SHELFS
    session.commit()
    client = app.test_client()

    response = client.post(
        "/shelf/add/9/2",
        headers={"Referer": "/book/2"},
    )

    assert response.status_code == 302
    with client.session_transaction() as client_session:
        flashes = client_session.get("_flashes", [])
    assert ("error", INVALID_OWNER_REFUSAL) in flashes
    assert _membership_count(session, actor.id, 2) == 0
    assert _shelf_count(session, shelf.id, 2) == 0


def test_api_allows_managed_user_to_shelf_an_existing_member(shelf_server):
    app, session, user, shelf = shelf_server
    user.role &= ~constants.ROLE_BROWSE_GLOBAL
    session.commit()

    response = app.test_client().post("/api/v1/shelves/9/books/1")

    assert response.status_code == 200, response.get_json()
    assert _membership_count(session, user.id, 1) == 1
    assert _shelf_count(session, shelf.id, 1) == 1


def test_shared_core_refuses_non_member_without_calling_it_invalid(shelf_server):
    from cps import shelf as shelf_module

    _app, session, user, shelf = shelf_server
    status, message = shelf_module.add_book_to_shelf(shelf, 2)

    assert status == shelf_module.SHELF_NOT_IN_LIBRARY
    assert message == (
        "This book is not in your library. Add it to My Library before adding "
        "it to a shelf."
    )
    assert _membership_count(session, user.id, 2) == 0
    assert _shelf_count(session, shelf.id, 2) == 0


def test_api_keeps_genuinely_missing_book_as_not_found(shelf_server):
    app, session, user, shelf = shelf_server

    response = app.test_client().post("/api/v1/shelves/9/books/404")

    assert response.status_code == 404, response.get_json()
    assert response.get_json() == {
        "error": {
            "code": "not_found",
            "message": "Book not found in the visible global library.",
        }
    }
    assert _membership_count(session, user.id, 404) == 0
    assert _shelf_count(session, shelf.id, 404) == 0


def test_classic_add_establishes_membership_and_reports_managed_refusal(shelf_server):
    app, session, user, shelf = shelf_server
    client = app.test_client()

    response = client.post(
        "/shelf/add/9/2",
        headers={"Referer": "/book/2"},
    )
    assert response.status_code == 302
    assert _membership_count(session, user.id, 2) == 1
    assert _shelf_count(session, shelf.id, 2) == 1

    session.query(ub.BookShelf).filter_by(shelf=shelf.id, book_id=2).delete()
    session.query(ub.UserLibraryBook).filter_by(user_id=user.id, book_id=2).delete()
    user.role &= ~constants.ROLE_BROWSE_GLOBAL
    session.commit()

    response = client.post(
        "/shelf/add/9/2",
        headers={"Referer": "/book/2"},
    )
    assert response.status_code == 302
    with client.session_transaction() as client_session:
        flashes = client_session.get("_flashes", [])
    assert ("error", MANAGED_REFUSAL) in flashes
    assert _membership_count(session, user.id, 2) == 0
    assert _shelf_count(session, shelf.id, 2) == 0

    xhr_response = client.post(
        "/shelf/add/9/2",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert xhr_response.status_code == 403
    assert xhr_response.get_data(as_text=True) == MANAGED_REFUSAL
