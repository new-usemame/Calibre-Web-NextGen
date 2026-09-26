# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for /api/v1 reader bookmark endpoints (auth gate + format casing +
the save/clear write path). GET contracts use SQLite; write/settings tests use
mocks. Legacy interop uses the same row and lowercase format."""
import inspect
import json
import flask
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock


def _ctx(path, method="GET", body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": method}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context(path, **kwargs)


def _auth_user(*, browse_global=False):
    return SimpleNamespace(
        is_authenticated=True, is_anonymous=False, id=1,
        role_browse_global=lambda: browse_global,
    )


@pytest.mark.unit
def test_get_bookmark_anonymous_401():
    from cps.api import reader as mod
    with _ctx("/api/v1/books/5/bookmark?format=epub"):
        with patch.object(mod, "current_user",
                          SimpleNamespace(is_authenticated=False, is_anonymous=True)):
            resp = inspect.unwrap(mod.get_bookmark)(5)
    assert resp[1] == 401


@pytest.fixture
def bookmark_client(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from cps import ub
    from cps.api import reader as mod
    # The app session supports in-memory SQLite too; optional carrier failure
    # must not change the GET contract of the mandatory local store.
    engine = create_engine('sqlite:///:memory:')
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, 'session', session)
    monkeypatch.setattr(mod, 'current_user', _auth_user())
    app = flask.Flask(__name__)
    app.add_url_rule('/api/v1/books/<int:book_id>/bookmark',
                     view_func=inspect.unwrap(mod.get_bookmark))
    yield app.test_client(), session
    session.close()
    engine.dispose()


def test_get_bookmark_returns_key(bookmark_client):
    from cps import ub
    client, session = bookmark_client
    session.add(ub.Bookmark(user_id=1, book_id=5, format='epub',
                           bookmark_key='epubcfi(/6/8)'))
    session.commit()
    response = client.get('/api/v1/books/5/bookmark?format=EPUB')
    assert response.status_code == 200
    assert response.json == {'bookmark': 'epubcfi(/6/8)', 'resume': None}


def test_get_bookmark_none_when_absent(bookmark_client):
    from cps import ub
    client, session = bookmark_client
    session.add_all([
        ub.Bookmark(user_id=2, book_id=5, format='epub', bookmark_key='other-user'),
        ub.Bookmark(user_id=1, book_id=6, format='epub', bookmark_key='other-book'),
        ub.Bookmark(user_id=1, book_id=5, format='pdf', bookmark_key='other-format'),
    ])
    session.commit()
    response = client.get('/api/v1/books/5/bookmark')
    assert response.status_code == 200
    assert response.json == {'bookmark': None, 'resume': None}


@pytest.mark.unit
def test_save_bookmark_lowercases_format_and_merges():
    """Format must be stored lowercase (legacy interop) and the new bookmark merged."""
    from cps.api import reader as mod
    mock_ub = MagicMock()
    with _ctx("/api/v1/books/5/bookmark", method="POST",
              body={"format": "EPUB", "bookmark": "epubcfi(/6/8)"}):
        with patch.object(mod, "current_user", _auth_user()), patch.object(mod, "ub", mock_ub):
            resp = inspect.unwrap(mod.save_bookmark)(5)
    assert resp[1] == 204
    _args, kwargs = mock_ub.Bookmark.call_args
    assert kwargs["format"] == "epub", "format must be lowercased for legacy interop"
    assert kwargs["bookmark_key"] == "epubcfi(/6/8)"
    assert mock_ub.session.merge.called


@pytest.mark.unit
def test_save_empty_bookmark_clears_without_merge():
    from cps.api import reader as mod
    mock_ub = MagicMock()
    with _ctx("/api/v1/books/5/bookmark", method="POST", body={"format": "epub", "bookmark": ""}):
        with patch.object(mod, "current_user", _auth_user()), patch.object(mod, "ub", mock_ub):
            resp = inspect.unwrap(mod.save_bookmark)(5)
    assert resp[1] == 204
    assert mock_ub.session.query.return_value.filter.return_value.delete.called
    assert not mock_ub.session.merge.called


@pytest.mark.unit
def test_source_preview_save_keeps_device_sharing_disabled():
    from cps.api import reader as mod
    mock_ub = MagicMock()
    mock_ub.session_flush.return_value = True
    mock_ub.session_commit.return_value = True
    with _ctx("/api/v1/books/5/bookmark", method="POST", body={
        "format": "epub",
        "bookmark": "epubcfi(/6/8)",
        "percentage": 22.0,
        "share_with_devices": False,
    }):
        with patch.object(mod, "current_user", _auth_user()), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod.reading_position, "coerce_percentage", return_value=22.0), \
             patch.object(mod.reading_position, "record_web_reader_progress") as record:
            resp = inspect.unwrap(mod.save_bookmark)(5)

    assert resp[1] == 204
    assert record.call_args.kwargs["share_with_devices"] is False


@pytest.mark.unit
def test_get_reader_settings_returns_complete_defaults_plus_saved_values():
    from cps.api import reader as mod
    user = _auth_user()
    user.view_settings = {"reader": {"font": "Arial", "margin": 32}}
    with _ctx("/api/v1/reader/settings"):
        with patch.object(mod, "current_user", user):
            resp = inspect.unwrap(mod.get_reader_settings)()
    body = json.loads(resp.get_data())["reader"]
    assert body["font"] == "Arial"
    assert body["margin"] == 32
    assert body["lineHeight"] == 150
    assert body["theme"] == "lightTheme"


@pytest.mark.unit
def test_save_reader_settings_merges_partial_patch_without_erasing_siblings():
    from cps.api import reader as mod
    user = _auth_user()
    user.view_settings = {"reader": {"font": "Arial", "margin": 32, "fontSize": 120}}
    mock_ub = MagicMock()
    with _ctx("/api/v1/reader/settings", method="POST", body={"lineHeight": 180}):
        with patch.object(mod, "current_user", user), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "flag_modified"):
            resp = inspect.unwrap(mod.save_reader_settings)()
    assert resp.status_code == 200
    assert user.view_settings["reader"] == {
        "font": "Arial", "margin": 32, "fontSize": 120, "lineHeight": 180,
    }
    assert json.loads(resp.get_data())["reader"]["lineHeight"] == 180
    mock_ub.session.commit.assert_called_once()


@pytest.mark.unit
def test_save_reader_settings_rejects_non_object_payload():
    from cps.api import reader as mod
    user = _auth_user()
    user.view_settings = {}
    with _ctx("/api/v1/reader/settings", method="POST", body=["bad"]):
        with patch.object(mod, "current_user", user):
            resp = inspect.unwrap(mod.save_reader_settings)()
    assert resp[1] == 400


@pytest.mark.unit
def test_reading_sources_requires_visible_book():
    from cps.api import reader as mod

    with _ctx("/api/v1/books/5/reading-sources"):
        with patch.object(mod, "current_user", _auth_user()), \
             patch.object(mod.calibre_db, "get_filtered_book", return_value=None):
            response = inspect.unwrap(mod.get_reading_sources)(5)

    assert response[1] == 404


@pytest.mark.unit
def test_reading_sources_anonymous_401_before_book_lookup():
    from cps.api import reader as mod

    with _ctx("/api/v1/books/5/reading-sources"):
        with patch.object(
            mod, "current_user",
            SimpleNamespace(is_authenticated=False, is_anonymous=True),
        ), patch.object(mod.calibre_db, "get_filtered_book") as visible:
            response = inspect.unwrap(mod.get_reading_sources)(5)

    assert response[1] == 401
    visible.assert_not_called()


@pytest.mark.unit
def test_reading_sources_matches_hidden_archived_global_detail_visibility():
    from cps.api import reader as mod

    book = SimpleNamespace(
        id=5, title="Book", path="Author/Book (5)", authors=[], data=[],
    )
    session = MagicMock()
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.first.return_value = None
    with _ctx("/api/v1/books/5/reading-sources"):
        with patch.object(mod, "current_user", _auth_user(browse_global=True)), \
             patch.object(mod.calibre_db, "get_filtered_book", return_value=book) as visible, \
             patch.object(mod.ub, "session", session), \
             patch.object(mod.storyteller_source, "configured_client", return_value=None):
            response = inspect.unwrap(mod.get_reading_sources)(5)

    assert response.status_code == 200
    visible.assert_called_once_with(
        5,
        allow_show_archived=True,
        allow_show_hidden=True,
        allow_show_global=True,
        allow_public_shelf_books=True,
    )


@pytest.mark.unit
def test_reading_sources_returns_devices_and_separate_resolved_carrier(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from cps import ub
    from cps.api import reader as mod

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    browser = ub.Device(
        user_id=1, kind="webreader", display_name="Browser", active=True,
        created_by="auto",
    )
    other = ub.Device(
        user_id=2, kind="webreader", display_name="Someone else", active=True,
        created_by="auto",
    )
    session.add_all([browser, other])
    session.flush()
    session.add_all([
        ub.DeviceReadingPosition(
            device_id=browser.id, book_id=5, progress_percent=12.5,
            cfi="epubcfi(/6/4!/4/2:4)",
        ),
        ub.DeviceReadingPosition(
            device_id=other.id, book_id=5, progress_percent=99.0,
            cfi="other-user",
        ),
    ])
    state = ub.KoboReadingState(user_id=1, book_id=5)
    state.current_bookmark = ub.KoboBookmark(progress_percent=29.0)
    session.add(state)
    session.commit()

    book = SimpleNamespace(
        id=5, title="Book", path="Author/Book (5)",
        authors=[SimpleNamespace(name="Author")], data=[],
    )
    app = flask.Flask(__name__)
    app.add_url_rule(
        "/api/v1/books/<int:book_id>/reading-sources",
        view_func=inspect.unwrap(mod.get_reading_sources),
    )
    with patch.object(mod, "current_user", _auth_user()), \
         patch.object(mod.calibre_db, "get_filtered_book", return_value=book), \
         patch.object(mod.storyteller_source, "configured_client", return_value=None):
        response = app.test_client().get("/api/v1/books/5/reading-sources")

    assert response.status_code == 200
    assert [row["label"] for row in response.json["sources"]] == [
        "Browser", "Other saved position",
    ]
    assert response.json["sources"][0]["progress_percent"] == 12.5
    assert response.json["sources"][1]["provenance"] == "unknown"
    assert all(row.get("label") != "Someone else" for row in response.json["sources"])
    session.close()
    engine.dispose()


@pytest.mark.unit
def test_reading_sources_list_one_browser_after_browsers_are_consolidated(
        monkeypatch, tmp_path):
    """Two browsers recorded separately become the account's one Browser place.

    The upgrade keeps each folded browser as an inactive alias that still
    holds its historical position rows. A reading place is a source the reader
    can choose, so an alias must not come back as a second, stale browser; its
    latest position already lives on Browser.
    """
    from datetime import datetime, timedelta
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from cps import ub
    from cps.api import reader as mod
    from cps.services.browser_source import migrate_account_browser_source

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    session.add(ub.User(id=1, name="reader", email="reader@example.invalid"))
    laptop = ub.Device(user_id=1, kind="webreader", display_name="Web reader",
                       active=True, created_by="auto")
    phone = ub.Device(user_id=1, kind="webreader", display_name="Web reader 2",
                      active=True, created_by="auto")
    session.add_all([laptop, phone])
    session.flush()
    then = datetime(2026, 9, 20, 12, 0)
    later = then + timedelta(hours=1)
    session.add_all([
        ub.DeviceReadingPosition(
            device_id=laptop.id, book_id=5, progress_percent=12.5,
            cfi="epubcfi(/6/4!/4/2:4)", client_modified_at=then,
            server_modified_at=then,
        ),
        ub.DeviceReadingPosition(
            device_id=phone.id, book_id=5, progress_percent=40.0,
            cfi="epubcfi(/6/8!/4/2:4)", client_modified_at=later,
            server_modified_at=later,
        ),
    ])
    session.commit()
    migrate_account_browser_source(engine)
    session.expire_all()

    book = SimpleNamespace(
        id=5, title="Book", path="Author/Book (5)",
        authors=[SimpleNamespace(name="Author")], data=[],
    )
    app = flask.Flask(__name__)
    app.add_url_rule(
        "/api/v1/books/<int:book_id>/reading-sources",
        view_func=inspect.unwrap(mod.get_reading_sources),
    )
    with patch.object(mod, "current_user", _auth_user()), \
         patch.object(mod.calibre_db, "get_filtered_book", return_value=book), \
         patch.object(mod.storyteller_source, "configured_client", return_value=None):
        response = app.test_client().get("/api/v1/books/5/reading-sources")

    assert response.status_code == 200
    assert [(row["label"], row["progress_percent"]) for row in response.json["sources"]] == [
        ("Browser", 40.0),
    ]
    session.close()
    engine.dispose()
