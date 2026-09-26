import json
import inspect
import pytest
import flask
from types import SimpleNamespace
from unittest.mock import patch


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


@pytest.mark.unit
def test_list_books_sort_abc():
    """GET /api/v1/books?sort=abc passes SORT_MAP['abc'] as the order arg to fill_indexpage."""
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    inner = SimpleNamespace(id=1, title="A", series_index="1.0", has_cover=0,
                            authors=[], series=[], data=[])
    row = SimpleNamespace(Books=inner, is_archived=False, read_status=None)
    pag = Pagination(1, 60, 1)

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?sort=abc"):
        with patch.object(books_mod.calibre_db, "fill_indexpage",
                          return_value=([row], None, pag)) as mock_fill, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True):
            view = inspect.unwrap(books_mod.list_books)
            view()

    call_args = mock_fill.call_args
    assert call_args is not None, "fill_indexpage was never called"
    # 5th positional arg (index 4) is the order list
    positional = call_args.args
    assert len(positional) >= 5, f"Expected ≥5 positional args, got {len(positional)}"
    assert positional[4] == books_mod.SORT_MAP["abc"], (
        f"Expected SORT_MAP['abc'] for sort=abc, got {positional[4]!r}"
    )


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
    """GET /api/v1/books?search=dune routes through get_search_results and total==1.

    Regression (real-library 500): get_search_results → order_authors(combined=True)
    returns SQLAlchemy Row objects whose book is under .Books, NOT at the top level.
    _row_to_item must unwrap .Books and surface read/archived from the Row.
    This test returns a Row-shaped object (SimpleNamespace with .Books, .read_status,
    .is_archived) so it fails against code that passes entries straight to
    serialize_book_list_item.
    """
    from cps.api import books as books_mod
    from cps import ub as ub_mod

    inner_book = SimpleNamespace(id=42, title="Dune", series_index="1.0", has_cover=1,
                                 authors=[SimpleNamespace(name="Frank Herbert")],
                                 series=[], data=[SimpleNamespace(format="EPUB")])
    # Simulate the Row object with read_status=STATUS_FINISHED to verify read=True surfacing
    row_entry = SimpleNamespace(
        Books=inner_book,
        is_archived=None,
        read_status=ub_mod.ReadBook.STATUS_FINISHED,
    )

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?search=dune"):
        with patch.object(books_mod.calibre_db, "get_search_results",
                          return_value=([row_entry], 1, None)) as mock_search, \
             patch.object(books_mod.config, "config_books_per_page", 60, create=True), \
             patch.object(books_mod.config, "config_read_column", 0, create=True):
            view = inspect.unwrap(books_mod.list_books)
            resp = view()

    mock_search.assert_called_once()
    call_args = mock_search.call_args
    # first positional arg is the search term
    assert call_args.args[0] == "dune", (
        f"get_search_results first arg should be 'dune', got {call_args.args[0]!r}"
    )

    data = json.loads(resp.get_data(as_text=True))
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == 42, (
        "id must come from .Books.id — if this fails, the Row normalization is missing"
    )
    assert data["items"][0]["title"] == "Dune"
    assert data["items"][0]["read"] is True, (
        "read must be True when read_status == STATUS_FINISHED"
    )
    assert "read" in data["items"][0], "read key must be present in search results"


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
