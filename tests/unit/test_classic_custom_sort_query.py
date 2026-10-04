# SPDX-License-Identifier: GPL-3.0-or-later
"""Execute Classic/OPDS custom-sort callers against production SQLite queries."""
import inspect
from pathlib import Path
from types import SimpleNamespace
from html.parser import HTMLParser

import flask
import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from sqlalchemy import text, true
from sqlalchemy.orm import sessionmaker

from tests.unit.test_custom_column_sort import ColumnDefinition, sortable_library as shared_sortable_library

sortable_library = shared_sortable_library
pytestmark = pytest.mark.unit


@pytest.fixture
def classic_library(sortable_library, monkeypatch):
    from cps import db, web, search, ub, custom_column_sort
    engine, difficulty, _decoy = sortable_library
    with engine.begin() as conn:
        conn.execute(text("ATTACH DATABASE ':memory:' AS calibre"))
    db.Base.metadata.create_all(engine)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    library = db.CalibreDB()
    library.session = session
    config = SimpleNamespace(config_sortable_custom_columns="12",
        config_books_per_page=20, config_read_column=0, config_columns_to_ignore="")
    library.config = config
    user = SimpleNamespace(id=7, is_anonymous=False, is_authenticated=True,
        show_detail_random=lambda: False, check_visibility=lambda *_: True, filter_language=lambda: "all",
        get_view_property=lambda *_: None, set_view_property=lambda *_: None)
    monkeypatch.setattr(library, "common_filters", lambda *a, **k:
        k.get("extra_filter") if k.get("extra_filter") is not None else true())
    monkeypatch.setattr(library, "get_cc_columns", lambda *a, **k: [])
    for module in [web, search, custom_column_sort]:
        monkeypatch.setattr(module, "calibre_db", library)
        monkeypatch.setattr(module, "config", config, raising=False)
    for module in [db, web, search, ub]:
        monkeypatch.setattr(module, "current_user", user)
    monkeypatch.setattr(custom_column_sort, "load_configured_columns",
        lambda _config: [ColumnDefinition(12, name="Difficulty")])
    monkeypatch.setattr(ub, "searched_ids", {})
    monkeypatch.setattr(ub, "session", session)
    for module in [web, search]:
        monkeypatch.setattr(module, "render_title_template", lambda _template, **ctx: ctx)
        monkeypatch.setattr(module, "_", lambda message, **values: message % values if values else message)
    app = flask.Flask(__name__)
    app.secret_key = "fixture"
    from flask_babel import Babel
    Babel(app)
    try:
        with app.test_request_context():
            yield SimpleNamespace(library=library, session=session, user=user, app=app)
    finally:
        session.close()


@pytest.mark.parametrize("direction,expected", [
    ("asc", [4, 1, 2, 6, 3, 5]), ("desc", [6, 2, 1, 4, 5, 3]),
])
@pytest.mark.parametrize("advanced", [False, True])
def test_classic_search_runs_custom_join(classic_library, direction, expected, advanced):
    from cps import web, search, ub
    order = web._sort_context(f"cc-12-{direction}", "search")
    if advanced:
        term = {"title": "Book", "read_status": "Any", "authors": "", "publisher": ""}
        for element in ["tag", "serie", "shelf", "language", "extension"]:
            term["include_" + element] = []
            term["exclude_" + element] = []
        result = search.render_adv_search_results(term,
            offset=0, order=order, limit=20)
    else:
        result = search.render_search_results("Book", offset=0, order=order, limit=20)
    assert [row.Books.id for row in result["entries"]] == expected
    assert result["result_count"] == 6
    assert ub.searched_ids[7] == expected


@pytest.mark.parametrize("read", [True, False])
def test_opds_read_feeds_allow_default_order_and_keep_restrictions(classic_library, monkeypatch, read):
    from cps import db, ub, opds
    classic_library.session.add(ub.ReadBook(user_id=7, book_id=1,
        read_status=ub.ReadBook.STATUS_FINISHED))
    classic_library.session.commit()
    monkeypatch.setattr(opds.auth, "current_user", lambda: classic_library.user)
    monkeypatch.setattr(opds, "config", classic_library.library.config)
    monkeypatch.setattr(opds, "get_opds_book_filter", lambda: db.Books.id <= 4)
    monkeypatch.setattr(opds, "render_xml_template", lambda _template, **ctx: ctx)
    function = opds.feed_read_books if read else opds.feed_unread_books
    result = inspect.unwrap(function)()
    assert sorted(row.Books.id for row in result["entries"]) == ([1] if read else [2, 3, 4])
    assert result["pagination"].total_count == (1 if read else 3)


def test_format_none_runs_custom_join_and_excludes_books_with_formats(classic_library):
    from cps import db, web
    classic_library.session.execute(db.Data.__table__.insert(),
        {"book": 1, "format": "EPUB", "uncompressed_size": 0, "name": "Book"})
    classic_library.session.commit()
    result = web.render_formats_books(1, "-1", web._sort_context("cc-12-asc", "formats"))
    assert [row.Books.id for row in result["entries"]] == [4, 2, 6, 3, 5]
    assert result["pagination"].total_count == 5


def test_real_custom_relationship_filter_and_sort_sql():
    # Production setup_db_cc_classes extends process-global Books mappings.
    # Run that actual setup in an owned finite child so other tests keep theirs.
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-c",
        "from tests.unit.test_classic_custom_sort_query import _exercise_real_custom_relationships; "
        "_exercise_real_custom_relationships()"], capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[2], timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real custom relationships: numeric ranges and hierarchy passed" in result.stdout


def template_environment():
    env = Environment(loader=ChoiceLoader([
        DictLoader({"layout.html": "{% block body %}{% endblock %}"}),
        FileSystemLoader(Path(__file__).resolve().parents[2] / "cps/templates")]))
    env.globals.update(_=lambda value: value, csrf_token=lambda: "fixture",
        url_for=lambda endpoint, **kw: "/" + endpoint + "?" + "&".join(f"{k}={v}" for k,v in kw.items()),
        current_user=SimpleNamespace(role_edit=lambda: False, role_delete_books=lambda: False,
            check_visibility=lambda *_: True, locale="en"))
    return env


@pytest.mark.parametrize("page,query", [("root", None), ("global_library", None), ("search", "two words"),
    ("advsearch", ""), ("cc_5", None)])
def test_classic_menu_offers_custom_sort_links(page, query):
    env = template_environment()
    html = env.from_string("{% import '_book_organizer.html' as o with context %}"
        "{{ o.books_list_organizer(page, 'Node', 'new', query=query, multiselect=false, settings=false,"
        "custom_sort_columns=columns) }}").render(page=page, query=query,
            columns=[ColumnDefinition(12, name="Difficulty")])
    assert "sort_param=cc-12-asc" in html
    assert "sort_param=cc-12-desc" in html
    if query is not None:
        assert "query=" + query in html


@pytest.mark.parametrize("page", ["hot", "download", "discover"])
def test_specialized_menu_does_not_offer_custom_sort(page):
    env = template_environment()
    html = env.from_string("{% import '_book_organizer.html' as o with context %}"
        "{{ o.books_list_organizer(page, 1, 'new', multiselect=false, settings=false,"
        "custom_sort_columns=columns) }}").render(page=page,
            columns=[ColumnDefinition(12)])
    assert "sort_param=cc-12-" not in html


class Headers(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "th" and "data-field" in values:
            self.fields[values["data-field"]] = values


def test_classic_table_headers_offer_only_validated_custom_sort_ids():
    env = template_environment()
    columns = [ColumnDefinition(12, "int"), ColumnDefinition(13, "float"),
        ColumnDefinition(14, "datetime"), ColumnDefinition(15, "int"), ColumnDefinition(16, "text")]
    html = env.get_template("book_table.html").render(cc=columns, visiblility={},
        sortable_custom_column_ids={12, 13, 14})
    parser = Headers()
    parser.feed(html)
    for column_id in [12, 13, 14]:
        assert parser.fields[f"custom_column_{column_id}"]["data-sortable"] == "true"
    for column_id in [15, 16]:
        assert parser.fields[f"custom_column_{column_id}"].get("data-sortable", "false") == "false"


@pytest.mark.parametrize("page", ["root", "search", "download", "hot", "discover"])
def test_classic_menu_loads_choices_lazily_for_supported_pages(page):
    env = template_environment()
    calls = []
    def choices():
        calls.append(True)
        return [ColumnDefinition(12)]
    env.globals["classic_sort_columns"] = choices
    html = env.from_string("{% import '_book_organizer.html' as o with context %}"
        "{{ o.books_list_organizer(page, 1, 'new', multiselect=false, settings=false) }}").render(page=page)
    supported = page in ["root", "search"]
    assert len(calls) == int(supported)
    assert ("sort_param=cc-12-asc" in html) is supported


class MenuLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("role") == "menuitem":
            self.urls.append(attrs["href"])


def test_custom_category_menu_keeps_opaque_path_for_every_sort():
    from urllib.parse import parse_qs, unquote, urlsplit
    from cps import web
    app = flask.Flask(__name__)
    app.register_blueprint(web.web)
    env = template_environment()
    env.globals["url_for"] = flask.url_for
    path = "Node.With/Slash & Space."
    with app.test_request_context():
        html = env.from_string("{% import '_book_organizer.html' as o with context %}"
            "{{ o.books_list_organizer('cc_5', category_path, 'new', multiselect=false, settings=false,"
            "custom_sort_columns=columns) }}").render(
                category_path=path, columns=[ColumnDefinition(12)])
        links = MenuLinks()
        links.feed(html)
        adapter = app.url_map.bind("localhost")
        keys = []
        for url in links.urls:
            parsed = urlsplit(url)
            endpoint, values = adapter.match(unquote(parsed.path))
            assert endpoint == "web.cc_category_list"
            assert values["column_id"] == 5
            assert values["category_path"] == path
            query = parse_qs(parsed.query)
            assert set(query) == {"sort_param"}
            assert len(query["sort_param"]) == 1
            keys.append(query["sort_param"][0])
        assert set(keys) == {"new", "old", "abc", "zyx", "authaz", "authza", "pubnew", "pubold",
                             "cc-12-asc", "cc-12-desc"}


@pytest.mark.parametrize("recent_missing", [False, True])
def test_global_menu_links_use_actual_global_route_and_preserve_search(recent_missing):
    from urllib.parse import parse_qs, urlsplit
    from cps import web
    app = flask.Flask(__name__)
    app.register_blueprint(web.web)
    env = template_environment()
    env.globals["url_for"] = flask.url_for
    search = "two words & exact"
    with app.test_request_context("/global-library?search=two+words"):
        html = env.from_string("{% import '_book_organizer.html' as o with context %}"
            "{{ o.books_list_organizer('global_library', id, 'new', multiselect=false, settings=false,"
            "custom_sort_columns=columns) }}").render(
                columns=[ColumnDefinition(12)], global_search=search, recent_missing=recent_missing)
        links = MenuLinks()
        links.feed(html)
        adapter = app.url_map.bind("localhost")
        keys = []
        for url in links.urls:
            parsed = urlsplit(url)
            endpoint, values = adapter.match(parsed.path)
            assert endpoint == "web.global_library"
            assert parse_qs(parsed.query) == {"search": [search]}
            keys.append(values["sort_param"])
    if recent_missing:
        assert keys == ["recent-missing"]
    else:
        assert set(keys) == {"new", "old", "abc", "zyx", "authaz", "authza", "pubnew", "pubold", "cc-12-asc", "cc-12-desc"}


def test_author_menu_offers_both_custom_directions_on_actual_author_route():
    from urllib.parse import urlsplit
    from cps import web
    app = flask.Flask(__name__)
    app.register_blueprint(web.web)
    env = template_environment()
    env.globals.update(url_for=flask.url_for,
        classic_sort_columns=lambda: [ColumnDefinition(12, name="Difficulty")])
    env.globals["current_user"].get_view_property = lambda *_: None
    with app.test_request_context():
        html = env.from_string("{% import '_book_organizer.html' as o with context %}"
            "{{ o.author_organizer(37, 'cc-12-desc') }}").render()
        links = MenuLinks()
        links.feed(html)
        adapter = app.url_map.bind("localhost")
        choices = []
        for url in links.urls:
            if url.startswith("#"):
                continue
            endpoint, values = adapter.match(urlsplit(url).path)
            assert endpoint == "web.books_list"
            assert values["data"] == "author" and values["book_id"] == "37"
            choices.append(values["sort_param"])
    assert "cc-12-asc" in choices and "cc-12-desc" in choices
    assert '<span class="book-organizer-label">Difficulty ↓</span>' in html.split('<ul class="dropdown-menu', 1)[0]


def _exercise_real_custom_relationships():
    from cps import db, web, search, ub, custom_column_sort, custom_column_visibility
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS calibre")
    columns = [ColumnDefinition(12, "int", name="Difficulty"), ColumnDefinition(5, "text", name="Topics")]
    db.CalibreDB.setup_db_cc_classes(columns)
    db.Base.metadata.create_all(engine)
    ub.Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(db.Books.__table__.insert(), [
            {"id": i, "title": f"Book {i}", "sort": f"Book {i}", "author_sort": "", "path": "."}
            for i in range(1, 7)])
        connection.execute(db.CustomColumns.__table__.insert(), [
            {"id": column.id, "name": column.name, "datatype": column.datatype,
             "is_multiple": False, "mark_for_delete": False} for column in columns])
        difficulty = db.cc_classes[12]
        connection.execute(difficulty.__table__.insert(), [
            {"id": i, "book": i, "value": value} for i, value in [(1,20),(2,20),(3,None),(4,10),(6,20)]])
        topics = db.cc_classes[5]
        connection.execute(topics.__table__.insert(), [
            {"id": 1, "value": "Node"}, {"id": 2, "value": "Node.Child"},
            {"id": 3, "value": "NodeX"}, {"id": 4, "value": "Node.Child.Grand"}])
        links = db.Base.metadata.tables["books_custom_column_5_link"]
        connection.execute(links.insert(), [
            {"book": book, "value": value} for book, value in [(1,1),(3,2),(4,4),(5,1),(6,3)]])
    session = sessionmaker(bind=engine)()
    library = db.CalibreDB()
    library.session = session
    library.config = config = SimpleNamespace(config_sortable_custom_columns="12",
        config_books_per_page=20, config_read_column=0, config_columns_to_ignore="")
    user = SimpleNamespace(id=7, is_anonymous=False, filter_language=lambda: "all",
        show_detail_random=lambda: False, check_visibility=lambda *_: True,
        get_view_property=lambda *_: None, set_view_property=lambda *_: None)
    library.common_filters = lambda *args, **kwargs: true()
    for module in [web, search, custom_column_sort, custom_column_visibility]:
        module.calibre_db = library
        module.config = config
    for module in [db, web, search, ub]:
        module.current_user = user
    for module in [web, search]:
        module.render_title_template = lambda _template, **context: context
        module._ = lambda message, **values: message % values if values else message
    ub.searched_ids = {}
    app = flask.Flask(__name__)
    app.secret_key = "fixture"
    # The signed export snapshot imports translated API/task labels, so this
    # isolated app needs the same Babel initialization as the real Flask app.
    from flask_babel import Babel
    Babel(app)
    try:
        with app.test_request_context():
            for direction, low, high, expected in [
                    ("asc", 15, 25, [1,2,6]), ("desc", 15, 25, [6,2,1]),
                    ("asc", 10, 10, [4]), ("desc", 10, 10, [4])]:
                term = {"title": "Book", "read_status": "Any", "authors": "", "publisher": "",
                    "custom_column_12_low": str(low), "custom_column_12_high": str(high)}
                for element in ["tag", "serie", "shelf", "language", "extension"]:
                    term["include_" + element] = []
                    term["exclude_" + element] = []
                result = search.render_adv_search_results(term, offset=0, limit=20,
                    order=web._sort_context(f"cc-12-{direction}", "advsearch"))
                assert [row.Books.id for row in result["entries"]] == expected
                assert result["result_count"] == len(expected)
                assert ub.searched_ids[7] == expected
            # Real tree loading, custom text relationship.any(), and subtree filter.
            for direction, expected in [("asc", [4,1,3,5]), ("desc", [1,4,5,3])]:
                result = web.render_cc_category(1, 5, "Node",
                    web._sort_context(f"cc-12-{direction}", "cc_5"))
                assert [row.Books.id for row in result["entries"]] == expected
                assert result["pagination"].total_count == 4
        print("real custom relationships: numeric ranges and hierarchy passed")
    finally:
        session.close()
        engine.dispose()
