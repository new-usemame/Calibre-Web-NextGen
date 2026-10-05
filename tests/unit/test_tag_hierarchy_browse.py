# SPDX-License-Identifier: GPL-3.0-or-later
"""Built-in tags: real metadata rows and mounted JSON browse requests.

Authentication proxies and the visibility predicate are fixture seams; these
tests prove neither a production login nor the complete common-filter policy.
"""
import sqlite3
from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cps import db

pytestmark = pytest.mark.unit


@pytest.fixture
def library_client(monkeypatch):
    from cps.api import api_v1, browse
    import cps.api as api_package
    from cps import usermanagement
    from cps.cw_login import utils

    engine = create_engine("sqlite://")
    for table in (db.Books.__table__, db.Tags.__table__, db.books_tags_link):
        table.create(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE preferences (key TEXT PRIMARY KEY, val TEXT)"))
        connection.execute(text("INSERT INTO preferences VALUES (:key, :val)"),
                           {"key": "categories_using_hierarchy", "val": '["tags"]'})
        connection.execute(db.Books.__table__.insert(), [
            {"id": i, "title": "Book " + str(i)} for i in range(1, 6)])
        connection.execute(db.Tags.__table__.insert(), [
            {"id": 1, "name": "Horror.Gothic"},
            {"id": 2, "name": "Horror.Gothic.Southern Gothic"},
            {"id": 3, "name": "Horror.Gothic.Victorian"},
            {"id": 4, "name": "Horror. Gothic "},
            {"id": 5, "name": "Horror.Gothicish"},
            {"id": 6, "name": "..."},
            {"id": 7, "name": "100%_True.Slash/Child"}])
        connection.execute(db.books_tags_link.insert(), [
            {"book": 1, "tag": 1}, {"book": 1, "tag": 4},
            {"book": 2, "tag": 2}, {"book": 3, "tag": 2},
            {"book": 3, "tag": 3}, {"book": 4, "tag": 5},
            {"book": 4, "tag": 6}, {"book": 4, "tag": 7},
            {"book": 5, "tag": 3}])
    session = sessionmaker(bind=engine)()
    library = object.__new__(db.CalibreDB)
    library.session = session
    library.ensure_session = lambda: None
    # Deliberately excludes a book whose tag is present in the visible tree.
    # A subtree must never pass its tag IDs to viewing_tag_id and widen this.
    def common_filters(*args, **kwargs):
        assert kwargs.get("viewing_tag_id") is None
        return db.Books.id != 5
    library.common_filters = common_filters
    viewer = SimpleNamespace(is_authenticated=True, is_anonymous=False,
                             check_visibility=lambda _section: True)
    config = SimpleNamespace(config_anonbrowse=0,
                             config_allow_reverse_proxy_header_login=False)
    monkeypatch.setattr(browse, "calibre_db", library)
    for module in (browse, api_package, usermanagement, utils):
        monkeypatch.setattr(module, "current_user", viewer, raising=False)
    for module in (api_package, usermanagement):
        monkeypatch.setattr(module, "config", config)
    app = flask.Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test", WTF_CSRF_ENABLED=False)
    app.register_blueprint(api_v1)
    try:
        yield library, app.test_client()
    finally:
        session.close()
        engine.dispose()


def _tree(client):
    response = client.get("/api/v1/tags/tree")
    assert response.status_code == 200, response.data
    return response.get_json()


def test_parent_counts_match_exact_visible_subtree(library_client):
    library, client = library_client
    payload = _tree(client)
    assert payload["hierarchical"] is True
    horror = next(node for node in payload["items"] if node["path"] == "Horror")
    gothic = next(node for node in horror["children"] if node["path"] == "Horror.Gothic")
    assert (gothic["count"], gothic["total_count"]) == (1, 3)
    assert [(n["name"], n["total_count"]) for n in gothic["children"]] == [
        ("Southern Gothic", 2), ("Victorian", 1)]
    from cps import tag_hierarchy
    tree = tag_hierarchy.read_tree(library)
    ids = library.session.query(db.Books.id).filter(
        library.common_filters(), tree.book_filter("Horror.Gothic")).order_by(db.Books.id).all()
    assert [row[0] for row in ids] == [1, 2, 3]
    # SQL wildcard characters and a slash remain literal stored tag components.
    ids = library.session.query(db.Books.id).filter(
        library.common_filters(), tree.book_filter("100%_True.Slash/Child")).all()
    assert [row[0] for row in ids] == [4]


def test_unparseable_tags_remain_exact_record_leaves(library_client):
    _library, client = library_client
    payload = _tree(client)
    opaque = next(node for node in payload["items"] if node.get("id") == 6)
    assert opaque == {"id": 6, "name": "...", "path": None,
                      "count": 1, "total_count": 1, "children": []}
    # The existing maintenance API remains flat and uses the raw stored names.
    flat = client.get("/api/v1/tags").get_json()["items"]
    assert next(node for node in flat if node["id"] == 4)["name"] == "Horror. Gothic "


def test_large_subtree_does_not_exhaust_sqlite_bind_variables(library_client):
    library, client = library_client
    # More IDs than SQLite's deliberately lowered limit, with a real query.
    library.session.execute(db.Tags.__table__.insert(), [
        {"id": 100 + i, "name": "Scale.Child" + str(i)} for i in range(24)])
    library.session.execute(db.books_tags_link.insert(), [
        {"book": 1, "tag": 100 + i} for i in range(24)])
    connection = library.session.connection().connection.driver_connection
    previous = connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 20)
    try:
        assert _tree(client)["hierarchical"] is True
        from cps import tag_hierarchy
        tree = tag_hierarchy.read_tree(library)
        rows = library.session.query(db.Books.id).filter(
            library.common_filters(), tree.book_filter("Scale")).all()
        assert [row[0] for row in rows] == [1]
    finally:
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous)


@pytest.mark.parametrize("preference", [None, "[]", '["#subjects"]'])
def test_only_explicit_builtin_preference_enables_tree(library_client, preference):
    library, client = library_client
    if preference is None:
        library.session.execute(text("DROP TABLE preferences"))
    else:
        library.session.execute(text("UPDATE preferences SET val=:value"), {"value": preference})
    payload = _tree(client)
    assert payload["hierarchical"] is False
    assert next(node for node in payload["items"] if node["id"] == 2)["path"] is None


@pytest.mark.parametrize("preference", ['{"tags": true}', '["tags", 42]', '{broken'])
def test_invalid_preference_is_retryable_not_an_empty_tree(library_client, preference):
    library, client = library_client
    library.session.execute(text("UPDATE preferences SET val=:value"), {"value": preference})
    response = client.get("/api/v1/tags/tree")
    assert response.status_code == 503, response.data
    assert response.get_json()["error"]["code"] == "service_unavailable"
