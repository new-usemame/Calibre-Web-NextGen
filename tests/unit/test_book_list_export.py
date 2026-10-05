import inspect
import csv
import io
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest


@pytest.mark.unit
@pytest.mark.parametrize("selection_filter", ["search", "favorites", "rated", "archived", "in_progress", "did_not_finish", "on_hold"])
def test_catalog_query_real_sql_combines_search_author_and_unread(monkeypatch, selection_filter):
    """The visible query returns only rows satisfying all three SQL predicates."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from cps import config, constants, db, ub
    from cps.api import books as books_api

    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    ub.Base.metadata.create_all(engine)
    db.Base.metadata.create_all(engine)
    app_session = sessionmaker(bind=engine)()
    metadata_session = sessionmaker(bind=engine)()
    viewer = ub.User(
        name="export-reader", email="export-reader@example.invalid", password="",
        role=constants.ROLE_DOWNLOAD, default_language="all", allowed_tags="", denied_tags="",
    )
    app_session.add(viewer)
    app_session.commit()

    target_author = db.Authors("Frank Herbert", "Frank Herbert")
    other_author = db.Authors("Other Author", "Other Author")
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = []
    for title, author, finished in (
        ("=Dune,\nA formula-looking title", target_author, False),
        ("Dune Messiah", target_author, True),
        ("Dune notes", other_author, False),
        ("The Dragon", target_author, False),
    ):
        book = db.Books(
            title, title, author.name, now, db.Books.DEFAULT_PUBDATE, "1.0", now,
            "uuid-" + str(len(rows)), 0, [], [],
        )
        book.authors.append(author)
        metadata_session.add(book)
        rows.append((book, finished))
    metadata_session.commit()
    for book, finished in rows:
        if finished:
            app_session.add(ub.ReadBook(
                user_id=viewer.id, book_id=book.id,
                read_status=ub.ReadBook.STATUS_FINISHED,
            ))
    app_session.commit()

    cdb = object.__new__(db.CalibreDB)
    cdb.session = metadata_session
    cdb.config = config
    monkeypatch.setattr(cdb, "get_cc_columns", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(books_api, "calibre_db", cdb)
    # Advanced exports use the same real DB through the search module.
    import importlib
    search_module = importlib.import_module("cps.search")
    monkeypatch.setattr(search_module, "calibre_db", cdb)
    monkeypatch.setattr(search_module, "current_user", viewer)
    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(db, "current_user", viewer)
    monkeypatch.setattr(books_api, "current_user", viewer)
    monkeypatch.setattr(config, "config_read_column", 0, raising=False)
    monkeypatch.setattr(config, "config_restricted_column", 0, raising=False)
    monkeypatch.setattr(config, "config_columns_to_ignore", "", raising=False)

    target_id = metadata_session.query(db.Authors.id).filter(
        db.Authors.name == "Frank Herbert"
    ).scalar()
    try:
        query = books_api._catalog_book_query(
            search="dune", author_id=target_id, filter_val="unread"
        )
        assert books_api._count_export_rows(query) == 1
        found = [row.Books.title for row in query.all()]
        assert found == ["=Dune,\nA formula-looking title"]

        output = io.BytesIO()
        written = books_api._write_export(output, query, "csv")
        decoded = list(csv.reader(io.StringIO(output.getvalue().decode("utf-8"))))
        assert written == 1
        assert decoded[1][0] == "'=Dune,\nA formula-looking title"

        text_output = io.BytesIO()
        text_written = books_api._write_export(text_output, query, "txt")
        assert text_written == 1
        assert text_output.getvalue().decode("utf-8").count("\n") == 1

        # Select all must consume the same combined predicates as the visible
        # rows and exported copy, rather than widening back to search alone.
        if selection_filter == "favorites":
            app_session.add_all([
                ub.FavoriteBook(user_id=viewer.id, book_id=rows[i][0].id)
                for i in (0, 2)
            ])
        elif selection_filter == "archived":
            app_session.add_all([
                ub.ArchivedBook(user_id=viewer.id, book_id=rows[i][0].id, is_archived=True)
                for i in (0, 2)
            ])
        elif selection_filter == "rated":
            rating = db.Ratings(10)
            for i in (0, 2):
                rows[i][0].ratings.append(rating)
            metadata_session.commit()
        app_session.commit()
        if selection_filter in ("in_progress", "did_not_finish", "on_hold"):
            status = {"in_progress": ub.ReadBook.STATUS_IN_PROGRESS,
                      "did_not_finish": ub.ReadBook.STATUS_DID_NOT_FINISH,
                      "on_hold": ub.ReadBook.STATUS_ON_HOLD}[selection_filter]
            app_session.add_all([
                ub.ReadBook(user_id=viewer.id, book_id=rows[i][0].id, read_status=status)
                for i in (0, 2)
            ])
            app_session.commit()
            # The merged export/query seam must accept all five statuses while
            # retaining the same author and title predicates as Select all.
            query = books_api._catalog_book_query(
                search="dune", author_id=target_id, filter_val=selection_filter)
            assert [row.Books.id for row in query.all()] == [rows[0][0].id]
            advanced = books_api._advanced_export_query({
                "title": "dune", "read_status": selection_filter})
            assert {row.Books.id for row in advanced.all()} == {
                rows[0][0].id, rows[2][0].id}
        selection_args = ("search=dune&filter=unread" if selection_filter == "search"
                          else f"search=dune&filter={selection_filter}"
                          if selection_filter in ("in_progress", "did_not_finish", "on_hold")
                          else f"filter={selection_filter}")
        app = flask.Flask(__name__)
        with app.test_request_context(
            f"/api/v1/books?{selection_args}&author={target_id}&select_all=1"
        ):
            selected = inspect.unwrap(books_api.list_books)().get_json()
        assert selected == {"ids": [rows[0][0].id], "total": 1}
        if selection_filter in ("in_progress", "did_not_finish", "on_hold"):
            with app.test_request_context("/api/v1/books/export", method="POST", json={
                "source": "catalog", "format": "csv", "params": {
                    "search": "dune", "author": target_id, "filter": selection_filter}}):
                response = inspect.unwrap(books_api.export_book_list)()
                assert response.status_code == 200
                assert response.headers["X-Export-Count"] == "1"
                assert len(list(csv.reader(io.StringIO(response.get_data(as_text=True))))) == 2
                response.close()
            # Unread retains active reading and excludes personal pauses.
            expected_unread = 1 if selection_filter == "in_progress" else 0
            assert books_api._count_export_rows(books_api._catalog_book_query(
                search="dune", author_id=target_id, filter_val="unread")) == expected_unread
            return

        # The shared search query must retain the existing hidden-book toggle.
        monkeypatch.setattr(config, "config_user_hide_enabled", True, raising=False)
        app_session.add(ub.UserHiddenBook(user_id=viewer.id, book_id=rows[0][0].id))
        app_session.commit()
        assert books_api._count_export_rows(books_api._catalog_book_query(
            search="dune", author_id=target_id, filter_val="unread",
        )) == 0
        assert books_api._count_export_rows(books_api._catalog_book_query(
            search="dune", author_id=target_id, filter_val="unread", show_hidden=True,
        )) == 1
    finally:
        metadata_session.close()
        app_session.close()
        engine.dispose()


@pytest.mark.unit
def test_id_filter_has_a_safe_explicit_fallback_without_sqlite_json(monkeypatch):
    from cps import db
    from cps.api import books as books_api

    monkeypatch.setattr(db, "_sqlite_json_available", lambda *_args: False)
    assert books_api._export_ids_filter([1, 2]).compile().params
    with pytest.raises(books_api.BookExportRequestError) as err:
        books_api._export_ids_filter(range(1, 902))
    assert err.value.status == 503


@pytest.mark.unit
def test_csv_formula_cells_are_neutralized_even_after_invisible_prefixes():
    from cps.api.books import _safe_csv_cell

    assert _safe_csv_cell("=1+1") == "'=1+1"
    assert _safe_csv_cell("\ufeff\t@SUM(A1:A2)") == "'\ufeff\t@SUM(A1:A2)"
    assert _safe_csv_cell("-ordinary-title") == "'-ordinary-title"
    assert _safe_csv_cell("A normal title") == "A normal title"
    assert _safe_csv_cell(None) == ""


@pytest.mark.unit
def test_txt_export_flattens_metadata_into_one_physical_line():
    from cps.api.books import _one_line

    assert _one_line("Title\r\nSecond line — Author\nName") == "Title Second line — Author Name"


@pytest.mark.unit
def test_export_route_refuses_oversized_match_before_streaming():
    from cps.api import books as books_api

    app = flask.Flask(__name__)
    app.secret_key = "test"
    with app.test_request_context(
        "/api/v1/books/export", method="POST", json={"source": "catalog", "format": "csv"}
    ), patch.object(books_api, "_book_export_query", return_value=("csv", object())), \
         patch.object(books_api, "_count_export_rows", return_value=100_001), \
         patch.object(books_api, "_write_export") as writer, \
         patch.object(books_api, "_real_user_id", return_value=1):
        response = inspect.unwrap(books_api.export_book_list)()

    if isinstance(response, tuple):
        payload = response[0].get_json()
        status = response[1]
    else:
        payload = response.get_json()
        status = response.status_code
    assert status == 413
    assert payload["error"]["code"] == "export_too_large"
    assert payload["error"]["limit"] == 100_000
    writer.assert_not_called()


@pytest.mark.unit
def test_export_detects_result_count_change_before_returning_attachment():
    from cps.api import books as books_api

    app = flask.Flask(__name__)
    app.secret_key = "test"
    with app.test_request_context(
        "/api/v1/books/export", method="POST", json={"source": "catalog", "format": "csv"}
    ), patch.object(books_api, "_book_export_query", return_value=("csv", object())), \
         patch.object(books_api, "_count_export_rows", return_value=2), \
         patch.object(books_api, "_write_export", return_value=1), \
         patch.object(books_api, "_real_user_id", return_value=1):
        response = inspect.unwrap(books_api.export_book_list)()

    assert response[1] == 409
    assert response[0].get_json()["error"]["code"] == "result_changed"


@pytest.mark.unit
def test_success_response_closes_spooled_file_after_client_reads_it():
    from cps.api import books as books_api

    app = flask.Flask(__name__)
    app.secret_key = "test"
    spools = []

    def write(spool, _query, _format):
        spools.append(spool)
        spool.write(b"one row\r\n")
        return 1

    with app.test_request_context(
        "/api/v1/books/export", method="POST", json={"source": "catalog", "format": "csv"}
    ), patch.object(books_api, "_book_export_query", return_value=("csv", object())), \
         patch.object(books_api, "_count_export_rows", return_value=1), \
         patch.object(books_api, "_write_export", side_effect=write), \
         patch.object(books_api, "_real_user_id", return_value=1):
        response = inspect.unwrap(books_api.export_book_list)()

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "Cookie" in response.vary
    assert "Authorization" in response.vary
    assert response.get_data() == b"one row\r\n"
    response.close()
    assert spools[0].closed


@pytest.mark.unit
def test_spool_is_closed_when_export_preparation_raises():
    from cps.api import books as books_api

    app = flask.Flask(__name__)
    app.secret_key = "test"
    spools = []

    def fail(spool, _query, _format):
        spools.append(spool)
        spool.write(b"partial")
        raise OSError("read failed")

    with app.test_request_context(
        "/api/v1/books/export", method="POST", json={"source": "catalog", "format": "csv"}
    ), patch.object(books_api, "_book_export_query", return_value=("csv", object())), \
         patch.object(books_api, "_count_export_rows", return_value=1), \
         patch.object(books_api, "_write_export", side_effect=fail), \
         patch.object(books_api, "_real_user_id", return_value=1):
        with pytest.raises(OSError):
            inspect.unwrap(books_api.export_book_list)()
    assert spools[0].closed


@pytest.mark.unit
def test_stream_preparation_never_writes_more_than_export_limit(monkeypatch):
    from cps.api import books as books_api

    monkeypatch.setattr(books_api, "MAX_BOOK_EXPORT_ROWS", 1)
    query = MagicMock()
    query.yield_per.return_value = iter([object(), object()])
    monkeypatch.setattr(books_api.calibre_db, "order_authors", lambda rows, **_kwargs: rows)
    monkeypatch.setattr(books_api, "_book_export_values", lambda _row: ["Title", "Author"])
    output = io.BytesIO()
    with pytest.raises(books_api.BookExportRequestError) as err:
        books_api._write_export(output, query, "txt")
    assert err.value.status == 409
    assert err.value.code == "result_changed"
    assert output.getvalue().count(b"\n") == 1


@pytest.mark.unit
def test_id_filter_stays_below_sqlite_bind_limit_for_large_sample():
    """More than the historic 999 IDs still use a single variable under SQLite."""
    from sqlalchemy import select
    from cps import db as db_module
    from cps.api import books as books_api
    from cps import db as db_module

    ids = list(range(1, 1400))
    with patch.object(db_module, "_sqlite_json_available", return_value=True):
        predicate = books_api._export_ids_filter(ids)
    statement = select(db_module.Books.id).where(predicate)
    from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
    compiled = statement.compile(dialect=sqlite_dialect())
    assert len(compiled.params) == 1

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
        connection.executemany("INSERT INTO books(id) VALUES (?)", ((value,) for value in ids))
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        params = tuple(compiled.params[name] for name in compiled.positiontup)
        result = connection.execute(str(compiled), params).fetchall()
    finally:
        connection.close()
    assert len(result) == 1399


@pytest.mark.unit
def test_common_visibility_archived_and_hidden_ids_fit_small_sqlite_bind_limit(monkeypatch):
    """common_filters excludes large account sets below SQLite's 999-bind limit."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from cps import config, constants, db, ub

    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"calibre": None}},
    )
    ub.Base.metadata.create_all(engine)
    db.Base.metadata.create_all(engine)
    app_session = sessionmaker(bind=engine)()
    metadata_session = sessionmaker(bind=engine)()
    viewer = ub.User(
        name="visibility-reader", email="visibility-reader@example.invalid", password="",
        role=constants.ROLE_DOWNLOAD, default_language="all", allowed_tags="", denied_tags="",
    )
    app_session.add(viewer)
    app_session.commit()
    now = datetime.now(timezone.utc)
    metadata_session.add_all([
        db.Books(
            f"Visibility book {book_id}", f"Visibility book {book_id}", "Author",
            now, db.Books.DEFAULT_PUBDATE, "1.0", now, f"visibility-{book_id}", 0, [], [],
        )
        for book_id in range(1, 2001)
    ])
    metadata_session.commit()
    app_session.add_all([
        ub.ArchivedBook(user_id=viewer.id, book_id=book_id, is_archived=True)
        for book_id in range(1, 701)
    ])
    app_session.add_all([
        ub.UserHiddenBook(user_id=viewer.id, book_id=book_id)
        for book_id in range(701, 1401)
    ])
    app_session.commit()
    cdb = object.__new__(db.CalibreDB)
    cdb.session = metadata_session
    cdb.config = config
    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(db, "current_user", viewer)
    monkeypatch.setattr(config, "config_restricted_column", 0, raising=False)
    for session in (metadata_session, app_session):
        session.connection().connection.driver_connection.setlimit(
            sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999
        )
    try:
        result = metadata_session.query(db.Books.id).filter(
            cdb.common_filters()
        ).order_by(db.Books.id).all()
        assert result == [(book_id,) for book_id in range(1401, 2001)]
    finally:
        metadata_session.close()
        app_session.close()
        engine.dispose()


@pytest.mark.unit
def test_classic_tag_export_is_the_only_catalog_path_that_adds_viewed_tag_exception():
    from cps.api import books as books_api

    with patch.object(books_api, "_catalog_export_query", return_value=MagicMock()) as builder:
        books_api._book_export_query({"source": "classic_catalog", "params": {"tag": 21}})
        assert builder.call_args == (({"tag": 21},), {"classic_tag_view": True})
        builder.reset_mock()
        books_api._book_export_query({"source": "catalog", "params": {"tag": 21}})
        assert builder.call_args == (({"tag": 21},), {})


@pytest.mark.unit
def test_guest_cannot_export_even_when_request_payload_is_valid():
    from cps.api import books as books_api

    app = flask.Flask(__name__)
    app.secret_key = "test"
    with app.test_request_context(
        "/api/v1/books/export", method="POST", json={"source": "catalog", "format": "csv"}
    ), patch.object(books_api, "_real_user_id", return_value=None):
        response = inspect.unwrap(books_api.export_book_list)()

    assert response[1] == 401
    assert response[0].get_json()["error"]["code"] == "authentication_required"


@pytest.mark.unit
@pytest.mark.parametrize("payload", [
    {"source": "catalog", "format": "csv", "params": {"author": 1.2}},
    {"source": "catalog", "format": "csv", "params": {"language": ["eng"]}},
    {"source": "catalog", "format": "csv", "params": {"search": "x", "unexpected": True}},
    {"source": "catalog", "format": "csv", "params": {"author": "9" * 25}},
    {"source": "advanced", "format": "csv", "params": {"rating_high": {"bad": 1}}},
    {"source": "advanced", "format": "csv", "params": {"publishstart": "not-a-date"}},
    {"source": "advanced", "format": "csv", "params": {"read_status": "anything"}},
])
def test_malformed_filter_wire_types_fail_with_400(payload):
    from cps.api import books as books_api

    with pytest.raises(books_api.BookExportRequestError) as err:
        books_api._book_export_query(payload)
    assert err.value.status == 400


@pytest.mark.unit
def test_smart_shelf_classic_owner_mode_is_not_a_caller_parameter():
    """SPA payload keys cannot switch execution to the Classic owner rule context."""
    from cps.api import books as books_api

    with patch.object(books_api, "_smart_shelf_export_query", return_value="spa-query") as build:
        books_api._book_export_query({
            "source": "smart_shelf", "id": 12,
            "params": {"classic_owner_rules": True},
        })
    assert build.call_args.kwargs == {}

    with patch.object(books_api, "_smart_shelf_export_query", return_value="classic-query") as build:
        books_api._book_export_query({
            "source": "classic_smart_shelf", "id": 12, "params": {},
        })
    assert build.call_args.kwargs == {"classic_owner_rules": True}


@pytest.mark.unit
def test_directly_accessible_hidden_smart_shelf_remains_exportable():
    """Sidebar visibility is a navigation preference, not shelf authorization."""
    from cps import db
    from cps.api import books as books_api
    from cps import magic_shelf

    shelf = SimpleNamespace(id=12, user_id=7, is_public=False, rules={})
    shelf_query = MagicMock()
    shelf_query.filter.return_value.first.return_value = shelf
    result_query = MagicMock()
    result_query.filter.return_value = result_query
    result_query.outerjoin.return_value = result_query
    result_query.options.return_value = result_query
    result_query.distinct.return_value = result_query
    result_query.order_by.return_value = result_query
    sort = SimpleNamespace(join=None, order_by=())

    with patch.object(books_api.ub, "session", SimpleNamespace(query=MagicMock(return_value=shelf_query))), \
         patch.object(books_api, "_real_user_id", return_value=7), \
         patch.object(magic_shelf, "get_visible_magic_shelves_for_user",
                      side_effect=AssertionError("navigation preference must not gate direct access")), \
         patch.object(magic_shelf, "build_query_from_rules", return_value=True), \
         patch.object(magic_shelf, "rules_reference_read_status", return_value=False), \
         patch.object(books_api.calibre_db, "generate_linked_query", return_value=result_query), \
         patch.object(books_api.calibre_db, "common_filters", return_value=True), \
         patch.object(books_api.config, "config_read_column", 0, create=True), \
         patch("cps.custom_column_sort.load_configured_columns", return_value=[]), \
         patch("cps.custom_column_sort.resolve_magic_shelf_sort", return_value=sort):
        result = books_api._smart_shelf_export_query(12, {})

    assert result is result_query


@pytest.mark.unit
def test_classic_advanced_export_uses_user_bound_rendered_snapshot_not_session():
    from cps.api import books as books_api
    from cps.search import build_adv_search_query

    app = flask.Flask(__name__)
    app.secret_key = "test-snapshot-key"
    term = {"title": "Dune", "authors": "Frank Herbert"}
    with app.test_request_context("/"), patch.object(books_api, "_real_user_id", return_value=7):
        token = books_api.create_classic_advanced_export_snapshot(term)
        flask.session["query"] = '{"title":"Something from another tab"}'
        query = MagicMock()
        query.options.return_value = query
        query.distinct.return_value = query
        query.order_by.return_value = query
        with patch("cps.search.build_adv_search_query", return_value=(query, [])) as build:
            books_api._classic_advanced_export_query({"snapshot": token, "sort": "new"})

    assert build.call_args.args[0] == term


@pytest.mark.unit
def test_classic_advanced_snapshot_cannot_be_replayed_by_another_account():
    from cps.api import books as books_api

    app = flask.Flask(__name__)
    app.secret_key = "test-snapshot-key"
    with app.test_request_context("/"), patch.object(books_api, "_real_user_id", return_value=7):
        token = books_api.create_classic_advanced_export_snapshot({"title": "Dune"})
    query = MagicMock()
    query.options.return_value = query
    query.distinct.return_value = query
    query.order_by.return_value = query
    with app.test_request_context("/"), patch.object(books_api, "_real_user_id", return_value=8), \
         patch("cps.search.build_adv_search_query", return_value=(query, [])):
        with pytest.raises(books_api.BookExportRequestError) as err:
            books_api._classic_advanced_export_query({"snapshot": token})
    assert err.value.status == 403


@pytest.mark.unit
def test_classic_advanced_snapshot_refuses_oversized_saved_terms(monkeypatch):
    from cps.api import books as books_api

    app = flask.Flask(__name__)
    app.secret_key = "test-snapshot-key"
    with app.test_request_context("/"), patch.object(books_api, "_real_user_id", return_value=7):
        monkeypatch.setattr(books_api, "CLASSIC_ADV_EXPORT_SNAPSHOT_MAX_BYTES", 1)
        assert books_api.create_classic_advanced_export_snapshot({"title": "Dune"}) is None


@pytest.mark.unit
def test_classic_advanced_snapshot_rejects_tampering_and_expiration(monkeypatch):
    from cps.api import books as books_api

    app = flask.Flask(__name__)
    app.secret_key = "test-snapshot-key"
    with app.test_request_context("/"), patch.object(books_api, "_real_user_id", return_value=7):
        token = books_api.create_classic_advanced_export_snapshot({"title": "Dune"})
        with pytest.raises(books_api.BookExportRequestError) as tampered:
            books_api._classic_advanced_export_query({"snapshot": token + "x"})
        assert tampered.value.status == 400

        monkeypatch.setattr(books_api, "CLASSIC_ADV_EXPORT_SNAPSHOT_MAX_AGE", -1)
        with pytest.raises(books_api.BookExportRequestError) as expired:
            books_api._classic_advanced_export_query({"snapshot": token})
    assert expired.value.status == 409


@pytest.mark.unit
def test_global_export_requires_global_browse_role():
    from cps.api import books as books_api

    user = SimpleNamespace(
        is_authenticated=True, is_anonymous=False, role_browse_global=lambda: False
    )
    with patch.object(books_api, "current_user", user):
        with pytest.raises(books_api.BookExportRequestError) as err:
            books_api._global_export_query({"filter": "all"})
    assert err.value.status == 403


@pytest.mark.unit
@pytest.mark.parametrize("page,key", [("author", "author"), ("series", "series"),
    ("publisher", "publisher"), ("category", "tag"), ("language", "language"),
    ("ratings", "rating"), ("formats", "format")])
def test_classic_entity_export_renders_translation_and_exact_scope(page, key):
    from pathlib import Path
    import html
    import json
    import re
    from jinja2 import Environment, FileSystemLoader

    templates = Path(__file__).resolve().parents[2] / "cps" / "templates"
    template = Environment(loader=FileSystemLoader(templates), autoescape=True).get_template(
        "_book_list_export.html")
    output = template.render(page=page, id=23, order="cc-1-desc",
        current_user=SimpleNamespace(is_authenticated=True, is_anonymous=False),
        _=lambda text: "translated: " + text,
        url_for=lambda *_args, **_kwargs: "/fixture/export", csrf_token=lambda: "fixture")
    assert 'aria-label="translated: Export this book list"' in output
    params = json.loads(html.unescape(re.search(r'data-params="([^"]+)"', output).group(1)))
    assert params == {"sort": "cc-1-desc", key: 23}
    assert 'data-source="{}"'.format("classic_catalog" if page == "category" else "catalog") in output
