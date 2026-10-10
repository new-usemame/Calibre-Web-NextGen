# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavior pins for the new-UI hidden-books library controls."""

import inspect
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect as sa_inspect, text
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit


@pytest.fixture
def app_database(monkeypatch):
    """List serialization reads optional favorite badges from the app DB."""
    from cps import ub

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _book(book_id=7):
    return SimpleNamespace(
        id=book_id,
        title="Hidden in plain sight",
        series_index="1.0",
        has_cover=0,
        authors=[],
        series=[],
        data=[],
        tags=[],
    )


def test_hide_books_is_enabled_for_new_instances_by_default():
    """The existing admin switch remains a kill switch, but fresh installs opt in."""
    from cps.config_sql import _Settings

    default = _Settings.__table__.c.config_user_hide_enabled.default
    assert default is not None
    assert default.arg is True


def _settings_engine_without_hide_flag(tmp_path):
    """Build the populated schema an install from before #319 would have."""
    from cps.config_sql import _Settings

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-app.db'}")
    metadata = MetaData()
    Table(
        "settings",
        metadata,
        *(column.copy() for column in _Settings.__table__.columns
          if column.name != "config_user_hide_enabled"),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO settings (id) VALUES (1)"))
    return engine


def test_existing_app_db_missing_hide_flag_migrates_enabled(tmp_path):
    """The real reflection migration must make Hide reachable after upgrade."""
    from cps.config_sql import _Settings, _migrate_table

    engine = _settings_engine_without_hide_flag(tmp_path)
    session = sessionmaker(bind=engine)()
    try:
        assert "config_user_hide_enabled" not in {
            column["name"] for column in sa_inspect(engine).get_columns("settings")
        }
        _migrate_table(session, _Settings)
        migrated = next(
            column for column in sa_inspect(engine).get_columns("settings")
            if column["name"] == "config_user_hide_enabled"
        )
        assert str(migrated["default"]).strip("()'") == "1"
        assert session.execute(text(
            "SELECT config_user_hide_enabled FROM settings WHERE id=1"
        )).scalar_one() == 1
    finally:
        session.close()
        engine.dispose()


def test_existing_admin_kill_switch_choice_stays_disabled(tmp_path):
    """Changing the model default must never overwrite an explicit stored OFF."""
    from cps.config_sql import _Settings, _migrate_table

    engine = create_engine(f"sqlite:///{tmp_path / 'configured-app.db'}")
    _Settings.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO settings (id, config_user_hide_enabled) VALUES (1, 0)"
        ))
    session = sessionmaker(bind=engine)()
    try:
        _migrate_table(session, _Settings)
        assert session.execute(text(
            "SELECT config_user_hide_enabled FROM settings WHERE id=1"
        )).scalar_one() == 0
    finally:
        session.close()
        engine.dispose()


def test_book_list_items_expose_hidden_state_for_a_visible_marker():
    from cps.api.serializers import serialize_book_list_item

    assert serialize_book_list_item(_book(), hidden=True)["hidden"] is True
    assert serialize_book_list_item(_book(), hidden=False)["hidden"] is False


def test_show_hidden_query_opts_only_the_spa_list_into_hidden_rows(app_database):
    """``show_hidden=1`` reaches common_filters through fill_indexpage."""
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    row = SimpleNamespace(Books=_book(), is_archived=False, read_status=None)
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?show_hidden=1"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([row], None, Pagination(1, 60, 1))) as fill, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True), \
             patch.object(books_mod, "current_user",
                          SimpleNamespace(id=11, is_authenticated=True, is_anonymous=False)), \
             patch.object(books_mod, "_hidden_book_ids", return_value={7}), \
             patch.object(books_mod, "_archived_book_ids", return_value={7, 8}):
            response = inspect.unwrap(books_mod.list_books)()

    assert fill.call_args.kwargs["allow_show_hidden"] is True
    assert fill.call_args.kwargs["allow_show_archived"] is True
    assert "extra_filter" in fill.call_args.kwargs
    body = json.loads(response.get_data(as_text=True))
    assert body["items"][0]["hidden"] is True
    assert body["items"][0]["favorited"] is False


def test_default_spa_list_keeps_hidden_exclusion_enabled():
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([], None, Pagination(1, 60, 0))) as fill, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True), \
             patch.object(books_mod, "current_user",
                          SimpleNamespace(id=11, is_authenticated=True, is_anonymous=False)), \
             patch.object(books_mod, "_hidden_book_ids", return_value={7}):
            inspect.unwrap(books_mod.list_books)()

    assert fill.call_args.kwargs.get("allow_show_hidden", False) is False
    assert fill.call_args.kwargs.get("allow_show_archived", False) is False


def test_show_hidden_applies_to_library_search_and_marks_results(app_database):
    from cps.api import books as books_mod

    row = SimpleNamespace(Books=_book(), is_archived=False, read_status=None)
    query = MagicMock()
    for method in ("filter", "options", "distinct", "order_by", "offset", "limit", "with_entities"):
        getattr(query, method).return_value = query
    query.count.return_value = 1
    query.all.return_value = [row]
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?search=plain&show_hidden=true"):
        with patch.object(books_mod.calibre_db, "search_query", return_value=query) as search, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True), \
             patch.object(books_mod, "current_user",
                          SimpleNamespace(id=11, is_authenticated=True, is_anonymous=False)), \
             patch.object(books_mod, "_hidden_book_ids", return_value={7}):
            response = inspect.unwrap(books_mod.list_books)()

    assert search.call_args.kwargs["allow_show_hidden"] is True
    body = json.loads(response.get_data(as_text=True))
    assert body["items"][0]["hidden"] is True
    assert body["items"][0]["favorited"] is False


def test_anonymous_role_is_explicit_in_me_payload_for_action_gating():
    from cps.api.serializers import serialize_user
    from cps import constants, ub

    guest = ub.User()
    guest.id, guest.name, guest.locale, guest.theme = 1, "Guest", "en", 1
    guest.role = constants.ROLE_ANONYMOUS
    assert serialize_user(guest)["role"]["anonymous"] is True
