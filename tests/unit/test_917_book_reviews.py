"""Behavioral coverage for private per-account book reviews (#917)."""

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def review_client(monkeypatch):
    from cps import ub
    from cps.api import book_reviews

    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    monkeypatch.setattr(ub, "session", session)
    actor = {"user": SimpleNamespace(
        is_authenticated=True, is_anonymous=False, id=1,
        role_browse_global=lambda: False,
    )}

    class CurrentUser:
        def __getattr__(self, name):
            return getattr(actor["user"], name)

    visible = {"value": True, "calls": []}

    class CalibreDB:
        def get_book_read_archived(self, book_id, read_column, **kwargs):
            visible["calls"].append((book_id, read_column, kwargs))
            return (object(), None, False) if visible["value"] else None

    monkeypatch.setattr(book_reviews, "current_user", CurrentUser())
    monkeypatch.setattr(book_reviews, "calibre_db", CalibreDB())
    monkeypatch.setattr(book_reviews.config, "config_read_column", "read", raising=False)
    app = flask.Flask(__name__)
    for method, view in (("GET", book_reviews.get_book_review),
                         ("PUT", book_reviews.put_book_review),
                         ("DELETE", book_reviews.delete_book_review)):
        app.add_url_rule(
            "/api/v1/books/<int:book_id>/review", view_func=inspect.unwrap(view),
            methods=[method], endpoint="review_" + method.lower(),
        )
    yield app.test_client(), actor, visible, session
    session.close()
    engine.dispose()


@pytest.mark.unit
def test_review_api_is_private_validates_plain_text_and_isolates_accounts(
    review_client, monkeypatch,
):
    from cps import ub

    client, actor, visible, session = review_client
    url = "/api/v1/books/31/review"

    missing = client.get(url)
    assert missing.status_code == 200
    assert missing.json == {"review": None}
    assert missing.headers["Cache-Control"] == "private, no-store"

    for payload in ({}, {"text": None}, {"text": 4}, {"text": " \n\t "},
                    {"text": "x" * 10001}):
        response = client.put(url, json=payload)
        assert response.status_code == 400
    assert session.query(ub.BookReview).count() == 0

    original = "  Keep the author's voice.\nSecond line.  "
    saved = client.put(url, json={"text": original})
    assert saved.status_code == 200
    assert saved.json["review"]["text"] == original
    assert saved.json["review"]["updated_at"].endswith("Z")
    row = session.query(ub.BookReview).one()
    created_at = row.created_at

    actor["user"] = SimpleNamespace(
        is_authenticated=True, is_anonymous=False, id=2,
        role_browse_global=lambda: False,
    )
    assert client.get(url).json == {"review": None}
    assert client.put(url, json={"text": "other account"}).status_code == 200
    actor["user"] = SimpleNamespace(
        is_authenticated=True, is_anonymous=False, id=1,
        role_browse_global=lambda: False,
    )
    updated = client.put(url, json={"text": "updated"})
    assert updated.status_code == 200
    assert updated.json["review"]["text"] == "updated"
    session.expire_all()
    owner_row = session.query(ub.BookReview).filter_by(user_id=1, book_id=31).one()
    assert owner_row.text == "updated"
    assert owner_row.created_at == created_at
    assert session.query(ub.BookReview).count() == 2

    assert client.delete(url).status_code == 204
    assert client.delete(url).status_code == 204
    assert session.query(ub.BookReview).filter_by(user_id=1).count() == 0
    assert session.query(ub.BookReview).filter_by(user_id=2).one().text == "other account"
    assert visible["calls"][-1] == (31, "read", {
        "allow_show_archived": True, "allow_show_hidden": True,
        "allow_show_global": False, "allow_public_shelf_books": True,
    })


@pytest.mark.unit
def test_review_access_requires_real_account_and_visible_book(review_client):
    from cps import ub

    client, actor, visible, session = review_client
    url = "/api/v1/books/31/review"
    actor["user"] = SimpleNamespace(
        is_authenticated=True, is_anonymous=True, id=3,
        role_browse_global=lambda: False,
    )
    assert client.get(url).status_code == 401
    assert client.put(url, json={"text": "private"}).status_code == 401

    actor["user"] = SimpleNamespace(
        is_authenticated=True, is_anonymous=False, id=3,
        role_browse_global=lambda: True,
    )
    visible["value"] = False
    denied = client.put(url, json={"text": "private"})
    assert denied.status_code == 404
    assert denied.json["error"]["code"] == "not_found"
    assert session.query(ub.BookReview).count() == 0
    assert visible["calls"][-1][2]["allow_show_global"] is True


@pytest.mark.unit
def test_put_returns_saved_mutation_even_if_another_request_deletes_next(
    review_client, monkeypatch,
):
    from cps import ub

    client, _, _, session = review_client
    original_commit = session.commit

    def commit_then_concurrent_delete():
        original_commit()
        session.query(ub.BookReview).filter_by(user_id=1, book_id=31).delete(
            synchronize_session=False)
        original_commit()

    monkeypatch.setattr(session, "commit", commit_then_concurrent_delete)
    response = client.put("/api/v1/books/31/review", json={"text": "saved"})
    assert response.status_code == 200
    assert response.json["review"]["text"] == "saved"
    assert session.query(ub.BookReview).filter_by(user_id=1, book_id=31).count() == 0


@pytest.mark.unit
def test_review_rows_merge_and_purge_without_losing_text(review_client):
    from cps import ub
    from cps.user_book_data import PER_USER_BOOK_MODELS, migrate_user_book_data, purge_user_book_data

    _, _, _, session = review_client
    assert "BookReview" in PER_USER_BOOK_MODELS
    older = datetime(2025, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session.add_all([
        ub.BookReview(user_id=1, book_id=100, text="kept note", created_at=older,
                      updated_at=older),
        ub.BookReview(user_id=1, book_id=200, text="source note", created_at=newer,
                      updated_at=newer),
        ub.BookReview(user_id=2, book_id=200, text="other user", created_at=older,
                      updated_at=older),
    ])
    session.commit()

    migrate_user_book_data(200, 100, session=session)
    session.commit()
    rows = session.query(ub.BookReview).order_by(ub.BookReview.user_id).all()
    assert [(row.user_id, row.book_id) for row in rows] == [(1, 100), (2, 100)]
    assert rows[0].text == "kept note\n\nsource note"
    assert rows[0].created_at.replace(tzinfo=timezone.utc) == older
    assert rows[0].updated_at.replace(tzinfo=timezone.utc) == newer
    assert rows[1].text == "other user"

    # A repeated exact-text collision is deduplicated while retaining both
    # authored notes from non-identical rows.
    session.add(ub.BookReview(user_id=1, book_id=200, text=rows[0].text,
                              created_at=newer, updated_at=newer))
    session.commit()
    migrate_user_book_data(200, 100, session=session)
    session.commit()
    assert session.query(ub.BookReview).filter_by(user_id=1, book_id=100).one().text == rows[0].text

    purge_user_book_data(user_id=1, session=session)
    session.commit()
    assert session.query(ub.BookReview).filter_by(user_id=1).count() == 0
    assert session.query(ub.BookReview).filter_by(user_id=2).count() == 1
    purge_user_book_data(book_id=100, session=session)
    session.commit()
    assert session.query(ub.BookReview).count() == 0
