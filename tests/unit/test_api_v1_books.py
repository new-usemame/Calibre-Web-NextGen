import json
import inspect
import pytest
import flask
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


@pytest.mark.unit
def test_books_list_envelope():
    from cps.api import books as books_mod
    from cps.pagination import Pagination
    inner = SimpleNamespace(id=1, title="A", series_index="1.0", has_cover=1,
                            authors=[SimpleNamespace(name="Auth")], series=[],
                            data=[SimpleNamespace(format="EPUB")])
    # fill_indexpage with join_archive_read=True returns Row-shaped objects
    row = SimpleNamespace(Books=inner, is_archived=False, read_status=None)
    pag = Pagination(1, 60, 1)
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?page=1"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([row], None, pag)), \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True):
            view = inspect.unwrap(books_mod.list_books)  # strip @login_required_if_no_ano
            resp = view()
    data = json.loads(resp.get_data(as_text=True))
    assert data["total"] == 1
    assert data["page"] == 1
    assert data["per_page"] == 60
    assert data["items"][0]["title"] == "A"
    assert data["items"][0]["cover_url"] == "/cover/1/sm"
    assert data["items"][0]["read"] is False
    assert data["items"][0]["archived"] is False


@pytest.mark.unit
def test_books_list_calls_fill_indexpage_with_join_archive_read_true():
    """Regression: fill_indexpage must be called with join_archive_read=True (6th positional arg).

    When join_archive_read=True, fill_indexpage returns SQLAlchemy Row tuples
    (Books, is_archived, read_status).  _row_to_item unwraps them so
    serialize_book_list_item receives plain Books objects plus read/archived flags.
    This test pins the call signature so a regression back to False (which would
    drop read/archived from the response) fails fast.
    """
    from cps.api import books as books_mod
    from cps.pagination import Pagination
    import inspect
    from unittest.mock import patch

    inner = SimpleNamespace(id=1, title="B", series_index="1.0", has_cover=0,
                            authors=[SimpleNamespace(name="Auth")], series=[], data=[])
    row = SimpleNamespace(Books=inner, is_archived=False, read_status=None)
    pag = Pagination(1, 60, 1)

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([row], None, pag)) as mock_fill, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True):
            view = inspect.unwrap(books_mod.list_books)
            view()

    call_args = mock_fill.call_args
    # 6th positional arg (index 5) is join_archive_read; must be True
    assert call_args is not None, "fill_indexpage was never called"
    positional = call_args.args
    assert len(positional) >= 6, (
        f"Expected ≥6 positional args to fill_indexpage, got {len(positional)}: {positional}"
    )
    assert positional[5] is True, (
        f"join_archive_read (arg[5]) must be True to return Row tuples with read/archived, "
        f"got {positional[5]!r}"
    )


# Alphabetical order is exercised through real SQL and the JSON view in
# test_1050_nordic_request_collation.py; SQL expression identity is not behavior.


@pytest.mark.unit
def test_list_books_sort_unknown_defaults_to_new():
    """Unknown sort key falls back to SORT_MAP['new']."""
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    pag = Pagination(1, 60, 0)

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?sort=bogus"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([], None, pag)) as mock_fill, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True):
            view = inspect.unwrap(books_mod.list_books)
            view()

    positional = mock_fill.call_args.args
    assert positional[4] == books_mod.SORT_MAP["new"]


@pytest.mark.unit
def test_list_books_search():
    """Search results retain row serialization after query delegation."""
    from cps.api import books as books_mod
    from cps import ub

    book = SimpleNamespace(
        id=42, title="Dune", series_index=None, has_cover=0,
        authors=[], series=[], data=[], tags=[], timestamp=None, last_modified=None,
    )
    row = SimpleNamespace(Books=book, is_archived=False,
                          read_status=ub.ReadBook.STATUS_FINISHED)
    query = MagicMock()
    query.with_entities.return_value.order_by.return_value.distinct.return_value.count.return_value = 1
    query.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [row]
    app_session = MagicMock()
    app_session.query.return_value.filter.return_value.all.return_value = [(42,)]

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?search=dune&author=3&filter=unread"):
        with patch.object(books_mod, "_catalog_book_query", return_value=query) as mock_query, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True), \
             patch.object(books_mod.user_cover, "overrides_for_user", return_value={}), \
             patch.object(books_mod.ub, "session", app_session), \
             patch.object(books_mod, "_visible_shelves_by_book", return_value={}), \
             patch.object(books_mod, "_real_user_id", return_value=7):
            view = inspect.unwrap(books_mod.list_books)
            resp = view()

    mock_query.assert_called_once_with(
        search="dune", author_id=3, series_id=None, tag_id=None, publisher_id=None,
        language_code=None, rating_id=None, book_format=None, filter_val="unread",
        show_hidden=False,
    )

    data = json.loads(resp.get_data(as_text=True))
    assert data["total"] == 1
    assert data["items"][0]["id"] == 42
    assert data["items"][0]["title"] == "Dune"
    assert data["items"][0]["read"] is True
    assert data["items"][0]["favorited"] is True


@pytest.mark.unit
def test_list_books_search_empty_string_uses_fill_indexpage():
    """An empty search string must NOT route to get_search_results."""
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    pag = Pagination(1, 60, 0)

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?search="):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([], None, pag)) as mock_fill, \
             patch.object(books_mod.calibre_db, "get_search_results",
                          return_value=([], 0, None)) as mock_search, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True):
            view = inspect.unwrap(books_mod.list_books)
            view()

    mock_fill.assert_called_once()
    mock_search.assert_not_called()


@pytest.mark.unit
def test_list_books_author_filter_passes_entity_db_filter():
    """GET /api/v1/books?author=3 must call fill_indexpage with a non-True db_filter (entity filter applied)."""
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    pag = Pagination(1, 60, 2)

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?author=3"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([], None, pag)) as mock_fill, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True):
            view = inspect.unwrap(books_mod.list_books)
            view()

    call_args = mock_fill.call_args
    assert call_args is not None, "fill_indexpage was never called"
    positional = call_args.args
    # 4th positional arg (index 3) is db_filter; must NOT be plain True
    assert len(positional) >= 4, f"Expected ≥4 positional args, got {len(positional)}"
    assert positional[3] is not True, (
        "db_filter (arg[3]) must be an entity expression when ?author= is supplied, not True"
    )


@pytest.mark.unit
def test_list_books_unread_filter_passes_db_filter():
    """GET /api/v1/books?filter=unread must call fill_indexpage with a non-True db_filter."""
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    pag = Pagination(1, 60, 5)

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?filter=unread"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([], None, pag)) as mock_fill, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True):
            view = inspect.unwrap(books_mod.list_books)
            view()

    call_args = mock_fill.call_args
    assert call_args is not None, "fill_indexpage was never called"
    positional = call_args.args
    assert len(positional) >= 4, f"Expected ≥4 positional args, got {len(positional)}"
    assert positional[3] is not True, (
        "db_filter (arg[3]) must be an unread expression when ?filter=unread is supplied, not True"
    )


@pytest.mark.unit
def test_list_books_archived_filter_uses_fill_indexpage_with_archived_books():
    """GET /api/v1/books?filter=archived routes to fill_indexpage_with_archived_books."""
    from cps.api import books as books_mod
    from cps.pagination import Pagination
    from unittest.mock import MagicMock

    pag = Pagination(1, 60, 3)

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?filter=archived"):
        mock_ub_query = MagicMock()
        mock_ub_query.filter.return_value = mock_ub_query
        mock_ub_query.all.return_value = []

        with patch.object(books_mod.calibre_db, "fill_indexpage_with_archived_books",
                          return_value=([], None, pag)) as mock_fill_arch, \
             patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([], None, pag)) as mock_fill, \
             patch.object(books_mod.ub, "session") as mock_ub_session, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True), \
             patch.object(books_mod, "current_user", SimpleNamespace(id=1)):
            mock_ub_session.query.return_value = mock_ub_query
            view = inspect.unwrap(books_mod.list_books)
            resp = view()

    mock_fill_arch.assert_called_once()
    mock_fill.assert_not_called()
    data = json.loads(resp.get_data(as_text=True))
    assert "items" in data
    assert data["total"] == 3


@pytest.mark.unit
def test_list_books_no_filter_passes_true_db_filter():
    """GET /api/v1/books (no entity or filter params) passes db_filter=True (unfiltered)."""
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    pag = Pagination(1, 60, 10)

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([], None, pag)) as mock_fill, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True):
            view = inspect.unwrap(books_mod.list_books)
            view()

    call_args = mock_fill.call_args
    assert call_args is not None
    positional = call_args.args
    assert len(positional) >= 4
    # db_filter must be True when no entity or filter param supplied
    assert positional[3] is True, (
        f"db_filter should be True (unfiltered) with no params, got {positional[3]!r}"
    )


@pytest.mark.unit
def test_build_entity_filter_language_none_negates_relationship():
    """language='none' (the synthetic 'no language' category from
    speaking_language) must compile to a NOT-EXISTS over Books.languages, not a
    lang_code == 'none' comparison (which matches nothing)."""
    from cps.api.books import _build_entity_filter

    none_filter = _build_entity_filter(None, None, None, None, "none")
    eng_filter = _build_entity_filter(None, None, None, None, "eng")

    none_sql = str(none_filter.compile(compile_kwargs={"literal_binds": True}))
    eng_sql = str(eng_filter.compile(compile_kwargs={"literal_binds": True}))

    assert "NOT" in none_sql.upper(), f"language=none must negate, got: {none_sql}"
    # the eng path keys off lang_code; the none path must not
    assert "'eng'" in eng_sql
    assert "'none'" not in none_sql, (
        f"language=none must not compare lang_code to 'none', got: {none_sql}"
    )


@pytest.mark.unit
def test_build_entity_filter_no_params_returns_true():
    from cps.api.books import _build_entity_filter
    assert _build_entity_filter(None, None, None, None, None) is True


@pytest.mark.unit
def test_list_books_handles_discovery_filters():
    """Source-pin: list_books recognises the discovery ?filter= categories so a
    refactor can't silently drop a sidebar view (verified live in the container;
    this guards the branch wiring)."""
    import inspect as _inspect
    from cps.api import books as books_mod
    src = _inspect.getsource(books_mod.list_books)
    for value in ('favorites', 'rated', 'discover', 'hot'):
        assert 'filter_val == "%s"' % value in src, "discovery filter %r missing" % value


@pytest.mark.unit
def test_select_all_returns_all_matching_ids_without_serializing_book_cards():
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?select_all=1&author=3"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([7, 8, 9], None, Pagination(1, 100001, 3))) as fill, \
             patch.object(books_mod.config, "config_books_per_page", 24, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True):
            response = inspect.unwrap(books_mod.list_books)()

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True)) == {"ids": [7, 8, 9], "total": 3}
    assert fill.call_args.kwargs["ids_only"] is True
    assert fill.call_args.args[0:2] == (1, 100001)


@pytest.mark.unit
def test_select_all_rejects_more_than_the_explicit_result_limit():
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?select_all=1"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=(list(range(100001)), None,
                                        Pagination(1, 100001, 100001))), \
             patch.object(books_mod.config, "config_books_per_page", 24, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True):
            response, status = inspect.unwrap(books_mod.list_books)()

    assert status == 413
    payload = json.loads(response.get_data(as_text=True))
    assert payload["error"]["code"] == "selection_too_large"
    assert payload["error"]["max_items"] == books_mod.MAX_SELECT_ALL_BOOKS


@pytest.mark.unit
def test_select_all_search_uses_the_filtered_search_query_ids():
    from cps.api import books as books_mod

    class IDQuery:
        def __init__(self):
            self.ordered = None
            self.limit_count = None

        def with_entities(self, *_columns):
            return self

        def distinct(self):
            return self

        def count(self):
            return 2

        def order_by(self, *order):
            self.ordered = order
            return self

        def limit(self, count):
            self.limit_count = count
            return self

        def all(self):
            return [(41,), (42,)]

    query = IDQuery()
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?search=dune&select_all=1"):
        with patch.object(books_mod, "_catalog_book_query", return_value=query) as search_query, \
             patch.object(books_mod.config, "config_books_per_page", 24, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True):
            response = inspect.unwrap(books_mod.list_books)()

    assert search_query.call_args.kwargs["search"] == "dune"
    assert query.limit_count == books_mod.MAX_SELECT_ALL_BOOKS + 1
    assert json.loads(response.get_data(as_text=True)) == {"ids": [41, 42], "total": 2}


@pytest.mark.unit
def test_select_all_discover_returns_only_the_current_random_page_ids():
    from cps.api import books as books_mod

    account = SimpleNamespace(id=7, is_authenticated=True, is_anonymous=False,
                              get_view_property=lambda section, key: None)
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?filter=discover&select_all=1"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([41, 42, 43], None, None)) as fill, \
             patch.object(books_mod, "current_user", account), \
             patch.object(books_mod.config, "config_books_per_page", 24, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True), \
             patch.object(books_mod, "_real_user_id", return_value=7), \
             patch.object(books_mod, "book_ids_with_read_status", return_value=[]), \
             patch.object(books_mod, "_hidden_book_ids", return_value=set()):
            response = inspect.unwrap(books_mod.list_books)()

    assert response.status_code == 200
    assert json.loads(response.get_data(as_text=True)) == {"ids": [41, 42, 43], "total": 3}
    assert fill.call_args.args[:2] == (1, 24)
    assert fill.call_args.kwargs["ids_only"] is True


@pytest.mark.unit
@pytest.mark.parametrize("available", [True, False])
def test_select_all_discover_keeps_saved_source_filter_and_random_page_bound(available):
    """Source scope and bounded ID selection must compose at the catalog API seam."""
    from sqlalchemy import create_engine, select, true
    from cps.api import books as books_mod

    account = SimpleNamespace(
        id=7, is_authenticated=True, is_anonymous=False,
        get_view_property=lambda section, key: "shelf:12",
    )
    source = SimpleNamespace(id=12)
    engine = create_engine("sqlite:///:memory:")
    app = flask.Flask(__name__)
    with engine.connect() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS app_settings")
        connection.exec_driver_sql("CREATE TABLE books (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO books VALUES (1),(2),(3),(4),(5)")
        connection.exec_driver_sql("CREATE TABLE app_settings.book_shelf_link (book_id INTEGER,shelf INTEGER)")
        connection.exec_driver_sql("INSERT INTO app_settings.book_shelf_link VALUES (1,12),(3,12),(5,12),(2,13)")

        def fill(page, page_size, *args, ids_only=False, extra_filter=None, **kwargs):
            assert ids_only, "Select all must request IDs, not serialized cards"
            predicate = extra_filter if extra_filter is not None else true()
            ids = connection.execute(
                select(books_mod.db.Books.id).where(predicate)
                .order_by(books_mod.db.Books.id).limit(page_size)
            ).scalars().all()
            return ids, None, None

        with app.test_request_context("/api/v1/books?filter=discover&select_all=1"):
            with patch.object(books_mod, "current_user", account), \
                 patch.object(books_mod, "_real_user_id", return_value=7), \
                 patch.object(books_mod, "book_ids_with_read_status", return_value=[]), \
                 patch.object(books_mod, "_hidden_book_ids", return_value=set()), \
                 patch.object(books_mod.config, "config_books_per_page", 2, create=True), \
                 patch.object(books_mod.config, "config_read_column", 0, create=True), \
                 patch.object(books_mod.discover_source, "_source_record", return_value=source if available else None), \
                 patch.object(books_mod.calibre_db, "fill_indexpage", side_effect=fill):
                response = inspect.unwrap(books_mod.list_books)()
        assert response.status_code == 200
        assert json.loads(response.get_data(as_text=True)) == (
            {"ids": [1, 3], "total": 2} if available else {"ids": [], "total": 0}
        )
    engine.dispose()
