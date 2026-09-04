# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral coverage for configurable Magic Shelf custom-column sorting."""

import inspect
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker


pytestmark = pytest.mark.unit


class ColumnDefinition:
    def __init__(self, column_id, datatype="int", *, multiple=False, deleted=False, name=None):
        self.id = column_id
        self.name = name or f"Column {column_id}"
        self.datatype = datatype
        self.is_multiple = multiple
        self.mark_for_delete = deleted


@pytest.fixture()
def sortable_library(monkeypatch):
    """Two direct-per-book columns whose decimal IDs have a prefix relation."""
    from cps import db

    custom_base = declarative_base()

    class Decoy(custom_base):
        __tablename__ = "custom_column_1"
        id = Column(Integer, primary_key=True)
        book = Column(Integer)
        value = Column(Integer)

    class Difficulty(custom_base):
        __tablename__ = "custom_column_12"
        id = Column(Integer, primary_key=True)
        book = Column(Integer)
        value = Column(Integer)

    engine = create_engine("sqlite://")
    db.Books.__table__.create(engine)
    Decoy.__table__.create(engine)
    Difficulty.__table__.create(engine)
    with engine.begin() as connection:
        for book_id in range(1, 7):
            connection.execute(
                text(
                    "INSERT INTO books "
                    "(id, title, sort, author_sort, timestamp, pubdate, series_index, "
                    "last_modified, path, has_cover, uuid) "
                    "VALUES (:id, :title, :title, 'Author', :timestamp, :timestamp, "
                    "1.0, :timestamp, '.', 0, :uuid)"
                ),
                {
                    "id": book_id,
                    "title": f"Book {book_id}",
                    "timestamp": f"2026-01-0{book_id} 00:00:00+00:00",
                    "uuid": f"uuid-{book_id}",
                },
            )
        connection.execute(
            Decoy.__table__.insert(),
            [
                {"id": book_id, "book": book_id, "value": 7 - book_id}
                for book_id in range(1, 7)
            ],
        )
        connection.execute(
            Difficulty.__table__.insert(),
            [
                {"id": 1, "book": 1, "value": 20},
                {"id": 2, "book": 2, "value": 20},
                {"id": 3, "book": 3, "value": None},
                {"id": 4, "book": 4, "value": 10},
                # Book 5 has no custom row: an outer-join empty value.
                {"id": 6, "book": 6, "value": 20},
            ],
        )
    # Keep the shorter ID first. A substring lookup for custom_column_1 in
    # custom_column_12 will select Decoy instead of the exact target.
    monkeypatch.setattr(db, "cc_classes", {1: Decoy, 12: Difficulty})
    try:
        yield engine, Difficulty, Decoy
    finally:
        engine.dispose()


def _paged_ids(session, query, order_by, page_size=2):
    pages = []
    for offset in range(0, 6, page_size):
        pages.append([
            row[0]
            for row in query.order_by(*order_by).offset(offset).limit(page_size).all()
        ])
    return pages


def test_configured_integer_sort_is_total_and_keeps_empties_last_across_pages(
        sortable_library):
    """Both directions produce stable pages, including ties and both empty shapes."""
    from cps import db
    from cps.custom_column_sort import resolve_magic_shelf_sort

    engine, difficulty, decoy = sortable_library
    config = SimpleNamespace(config_sortable_custom_columns="12")
    columns = [ColumnDefinition(12, name="Difficulty")]
    session = sessionmaker(bind=engine)()
    try:
        ascending = resolve_magic_shelf_sort("cc-12-asc", config, columns)
        descending = resolve_magic_shelf_sort("cc-12-desc", config, columns)

        assert ascending.key == "cc-12-asc"
        assert descending.key == "cc-12-desc"

        base = session.query(db.Books.id).outerjoin(*ascending.join)
        assert _paged_ids(session, base, ascending.order_by) == [[4, 1], [2, 6], [3, 5]]

        base = session.query(db.Books.id).outerjoin(*descending.join)
        assert _paged_ids(session, base, descending.order_by) == [[6, 2], [1, 4], [5, 3]]

        assert len(ascending.join) == 2 and ascending.join[0] is difficulty
        assert len(descending.join) == 2 and descending.join[0] is difficulty
        assert ascending.join[0] is not decoy
        assert descending.join[0] is not decoy
    finally:
        session.close()


def test_hostile_unknown_and_deleted_keys_execute_only_the_default_order(sortable_library):
    """Untrusted or stale keys fall back before SQL construction sees request text."""
    from cps import db
    from cps.custom_column_sort import resolve_magic_shelf_sort

    engine, _difficulty, _decoy = sortable_library
    config = SimpleNamespace(config_sortable_custom_columns="12,999")
    rejected = (
        ("id; DROP TABLE books", [ColumnDefinition(12)]),
        ("1) OR (1=1", [ColumnDefinition(12)]),
        ("cc-١٢-asc", [ColumnDefinition(12)]),
        ("cc-999-asc", [ColumnDefinition(12)]),
        ("cc-12-desc", [ColumnDefinition(12, deleted=True)]),
        ("cc-12-asc", [ColumnDefinition(12, "text")]),
        ("cc-12-desc", [ColumnDefinition(12, multiple=True)]),
    )
    statements = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture_statement)
    session = sessionmaker(bind=engine)()
    try:
        for raw_key, live_columns in rejected:
            resolved = resolve_magic_shelf_sort(raw_key, config, live_columns)
            assert resolved.key == "new"
            assert resolved.join == ()
            assert [row[0] for row in session.query(db.Books.id)
                    .order_by(*resolved.order_by).all()] == [6, 5, 4, 3, 2, 1]
    finally:
        session.close()
        event.remove(engine, "before_cursor_execute", capture_statement)

    emitted_sql = "\n".join(statements)
    assert all(raw_key not in emitted_sql for raw_key, _columns in rejected)
    assert "DROP TABLE" not in emitted_sql
    assert "OR (1=1" not in emitted_sql


def test_oversized_decimal_custom_column_id_resolves_to_default(sortable_library):
    from cps.custom_column_sort import (
        configured_column_ids,
        persist_configured_columns,
        resolve_magic_shelf_sort,
    )

    _engine, _difficulty, _decoy = sortable_library
    oversized_id = "9" * 5000
    config = SimpleNamespace(config_sortable_custom_columns=oversized_id)

    resolved = resolve_magic_shelf_sort(
        f"cc-{oversized_id}-asc",
        config,
        [ColumnDefinition(12)],
    )

    assert resolved.key == "new"
    assert resolved.join == ()
    assert configured_column_ids(config) == frozenset()
    assert persist_configured_columns(
        config, [oversized_id], [ColumnDefinition(12)]
    ) == ""


def test_only_scalar_numeric_and_datetime_columns_can_be_persisted(tmp_path):
    """A legacy app.db migrates, then refuses text and multi-value selections."""
    from cps.config_sql import _Settings, _migrate_table
    from cps.custom_column_sort import eligible_columns, persist_configured_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-app.db'}")
    metadata = MetaData()
    Table(
        "settings",
        metadata,
        *(column.copy() for column in _Settings.__table__.columns
          if column.name != "config_sortable_custom_columns"),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO settings (id) VALUES (1)"))

    session = sessionmaker(bind=engine)()
    try:
        _migrate_table(session, _Settings)
        config_row = session.query(_Settings).one()
        columns = [
            ColumnDefinition(2, "int", name="Pages"),
            ColumnDefinition(3, "float", name="Score"),
            ColumnDefinition(4, "datetime", name="Started"),
            ColumnDefinition(5, "text", name="Mood"),
            ColumnDefinition(6, "int", multiple=True, name="Multiple numbers"),
        ]

        assert [column.id for column in eligible_columns(columns)] == [2, 3, 4]
        persist_configured_columns(config_row, ["6", "5", "2", "2", "hostile"], columns)
        session.commit()
        session.expire_all()

        assert session.execute(text(
            "SELECT config_sortable_custom_columns FROM settings WHERE id = 1"
        )).scalar_one() == "2"
    finally:
        session.close()
        engine.dispose()


def test_failed_column_load_preserves_selection_but_a_real_clear_is_persisted(monkeypatch):
    """Unavailable definitions are not equivalent to an empty admin selection."""
    from cps import custom_column_sort
    from cps.config_sql import _Settings

    class DefinitionQuery:
        def filter(self, *_criteria):
            return self

        def order_by(self, *_columns):
            return self

    calibre_session = SimpleNamespace(query=lambda _model: DefinitionQuery())
    monkeypatch.setattr(custom_column_sort.calibre_db, "session", calibre_session)

    def fail_column_query(_query):
        raise custom_column_sort.SQLAlchemyError("library unavailable")

    monkeypatch.setattr(custom_column_sort, "_query_columns", fail_column_query)
    engine = create_engine("sqlite://")
    _Settings.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    try:
        config = _Settings(id=1, config_sortable_custom_columns="12")
        session.add(config)
        session.commit()

        unavailable_columns = custom_column_sort.load_eligible_columns()
        custom_column_sort.persist_configured_columns(config, [], unavailable_columns)
        session.commit()
        session.expire_all()
        assert session.query(_Settings.config_sortable_custom_columns).scalar() == "12"

        custom_column_sort.persist_configured_columns(config, [], [ColumnDefinition(12)])
        session.commit()
        session.expire_all()
        assert session.query(_Settings.config_sortable_custom_columns).scalar() == ""
    finally:
        session.close()
        engine.dispose()


def test_failed_configured_column_load_marks_only_custom_fallback_unpersistable(monkeypatch):
    from cps import custom_column_sort

    class DefinitionQuery:
        def filter(self, *_criteria):
            return self

        def order_by(self, *_columns):
            return self

    calibre_session = SimpleNamespace(query=lambda _model: DefinitionQuery())
    monkeypatch.setattr(custom_column_sort.calibre_db, "session", calibre_session)
    monkeypatch.setattr(
        custom_column_sort,
        "_query_columns",
        lambda _query: (_ for _ in ()).throw(
            custom_column_sort.SQLAlchemyError("library unavailable")
        ),
    )
    config = SimpleNamespace(config_sortable_custom_columns="12")

    unavailable_columns = custom_column_sort.load_configured_columns(config)
    custom_fallback = custom_column_sort.resolve_magic_shelf_sort(
        "cc-12-asc", config, unavailable_columns
    )
    builtin_sort = custom_column_sort.resolve_magic_shelf_sort(
        "abc", config, unavailable_columns
    )

    assert unavailable_columns is None
    assert custom_fallback.key == "new"
    assert custom_fallback.persistable is False
    assert builtin_sort.key == "abc"
    assert builtin_sort.persistable is True
    assert custom_column_sort.custom_sort_options(config, unavailable_columns) == []


def test_spa_response_exposes_that_an_outage_fallback_must_not_be_persisted():
    from cps.api import magicshelves

    shelf = SimpleNamespace(id=7, user_id=42, is_public=False, rules={})
    user = SimpleNamespace(id=42, is_authenticated=True)

    class ShelfQuery:
        def get(self, shelf_id):
            return shelf if shelf_id == shelf.id else None

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/magicshelf/7?sort=cc-12-asc"):
        with patch.object(
            magicshelves.ub,
            "session",
            SimpleNamespace(query=lambda _model: ShelfQuery()),
        ), patch.object(
            magicshelves, "current_user", user
        ), patch.object(
            magicshelves,
            "config",
            SimpleNamespace(
                config_sortable_custom_columns="12",
                config_books_per_page=20,
            ),
        ), patch.object(
            magicshelves, "load_configured_columns", return_value=None
        ), patch.object(
            magicshelves.magic_shelf, "build_query_from_rules", return_value=None
        ), patch.object(
            magicshelves, "_shelf_item", return_value={"id": 7}
        ):
            response = inspect.unwrap(magicshelves.magic_shelf_books)(7)

    body = response.get_json()
    assert body["sort"] == "new"
    assert body["sort_persistable"] is False
    assert body["custom_sort_options"] == []


def test_classic_route_persists_real_fallback_but_not_outage_fallback():
    from cps import web
    from flask_babel import Babel

    shelf = SimpleNamespace(
        id=7,
        user_id=42,
        is_public=False,
        name="On Kobo",
        icon="wand",
    )
    persisted = []
    user = SimpleNamespace(
        id=42,
        name="reader",
        is_authenticated=True,
        get_view_property=lambda *_args: "cc-12-asc",
        set_view_property=lambda *_args: persisted.append(_args),
    )

    class ShelfQuery:
        def get(self, shelf_id):
            return shelf if shelf_id == shelf.id else None

    app = flask.Flask(__name__)
    app.config["BABEL_DEFAULT_LOCALE"] = "en"
    Babel(app)
    with app.test_request_context("/magicshelf/7/cc-12-asc/1"):
        with patch.object(
            web.ub,
            "session",
            SimpleNamespace(query=lambda _model: ShelfQuery()),
        ), patch.object(
            web, "current_user", user
        ), patch.object(
            web,
            "config",
            SimpleNamespace(
                config_sortable_custom_columns="12",
                config_books_per_page=20,
            ),
        ), patch.object(
            web, "load_configured_columns", return_value=None
        ), patch.object(
            web.magic_shelf, "get_books_for_magic_shelf", return_value=([], 0)
        ), patch.object(
            web, "render_title_template", side_effect=lambda _template, **values: values
        ), patch(
            "cps.cwa_db_loader.load_cwa_db", side_effect=RuntimeError("disabled in test")
        ):
            outage_result = inspect.unwrap(web.render_magic_shelf)(7, "cc-12-asc", 1)
            assert persisted == []
            invalid_result = inspect.unwrap(web.render_magic_shelf)(7, "malformed", 1)

    assert outage_result["order"] == "new"
    assert persisted == [("magicshelf", "stored", "new")]
    assert invalid_result["order"] == "new"
