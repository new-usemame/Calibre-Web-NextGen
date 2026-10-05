# SPDX-License-Identifier: GPL-3.0-or-later
"""Tag group membership through actual visibility SQL, selection and file export.

The viewer is injected at authentication proxies; these are not login tests.
"""
import csv
import io
from datetime import datetime, timezone

import flask
import pytest
from flask_babel import Babel
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from cps import config, constants, db, ub

pytestmark = pytest.mark.unit


@pytest.fixture
def group_client(monkeypatch):
    from cps.api import api_v1, books, browse
    import cps.api as api_package
    from cps import usermanagement
    from cps.cw_login import utils

    engine = create_engine("sqlite://", execution_options={"schema_translate_map": {"calibre": None}})
    event.listen(engine, "connect", db._register_sqlite_udfs)
    ub.Base.metadata.create_all(engine)
    db.Base.metadata.create_all(engine)
    app_session = sessionmaker(bind=engine)()
    session = sessionmaker(bind=engine)()
    viewer = ub.User(name="hierarchy-reader", email="hierarchy@example.invalid", password="",
                     role=constants.ROLE_VIEWER | constants.ROLE_DOWNLOAD,
                     sidebar_view=constants.SIDEBAR_CATEGORY, default_language="all",
                     allowed_tags="", denied_tags="")
    app_session.add(viewer)
    app_session.commit()
    tags = [db.Tags(name) for name in (
        "Horror.Gothic", "Horror.Gothic.Southern", "Horror.Gothicish", "Permitted", "Blocked")]
    languages = [db.Languages(code) for code in ("eng", "deu")]
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for i, tag in enumerate((tags[0], tags[1], tags[1], tags[2]), 1):
        book = db.Books("Book " + str(i), "Book " + str(i), "Author", now,
                        db.Books.DEFAULT_PUBDATE, "1.0", now, "uuid-" + str(i), 0, [], [])
        book.id = i
        book.authors.append(db.Authors("Author " + str(i), "Author " + str(i)))
        book.tags.append(tag)
        if i == 1:
            book.tags.append(tags[3])
        if i == 3:
            book.tags.append(tags[4])
        book.languages.append(languages[1 if i == 2 else 0])
        session.add(book)
    session.commit()
    session.execute(text("CREATE TABLE preferences (key TEXT PRIMARY KEY, val TEXT)"))
    session.execute(text("INSERT INTO preferences VALUES ('categories_using_hierarchy', '[\"tags\"]')"))
    session.commit()
    library = object.__new__(db.CalibreDB)
    library.session = session
    library.config = config
    library.ensure_session = lambda: None
    monkeypatch.setattr(library, "get_cc_columns", lambda *_a, **_k: [])
    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(db, "current_user", viewer)
    for module in (books, browse):
        monkeypatch.setattr(module, "calibre_db", library)
    for module in (books, browse, api_package, usermanagement, utils):
        monkeypatch.setattr(module, "current_user", viewer, raising=False)
    for key, value in {"config_read_column": 0, "config_restricted_column": 0,
                       "config_columns_to_ignore": "", "config_allow_reverse_proxy_header_login": False,
                       "config_anonbrowse": 0, "config_books_per_page": 24}.items():
        monkeypatch.setattr(config, key, value, raising=False)
    app = flask.Flask(__name__)
    Babel(app)
    app.config.update(TESTING=True, SECRET_KEY="test", WTF_CSRF_ENABLED=False)
    app.register_blueprint(api_v1)
    try:
        yield library, app_session, viewer, app.test_client()
    finally:
        session.close()
        app_session.close()
        engine.dispose()


@pytest.mark.parametrize("policy,expected", [
    ("ordinary", [1, 2, 3]), ("hidden", [1, 3]), ("archived", [1, 3]),
    ("language", [1, 3]), ("denied", [1, 2]), ("allowed", [1]), ("library", [1]),
])
def test_counts_selection_and_export_share_real_visibility(group_client, policy, expected):
    library, app_session, viewer, client = group_client
    if policy == "hidden":
        app_session.add(ub.UserHiddenBook(user_id=viewer.id, book_id=2))
    elif policy == "archived":
        app_session.add(ub.ArchivedBook(user_id=viewer.id, book_id=2, is_archived=True))
    elif policy == "language":
        viewer.default_language = "eng"
    elif policy == "denied":
        viewer.denied_tags = "Blocked"
    elif policy == "allowed":
        viewer.allowed_tags = "Permitted"
    elif policy == "library":
        viewer.has_own_library = True
        app_session.add_all([ub.UserLibraryBook(user_id=viewer.id, book_id=i) for i in (1, 4)])
    app_session.commit()
    tree = client.get("/api/v1/tags/tree")
    assert tree.status_code == 200, tree.data
    horror = next(node for node in tree.get_json()["items"] if node["path"] == "Horror")
    gothic = next(node for node in horror["children"] if node["path"] == "Horror.Gothic")
    assert gothic["total_count"] == len(expected)
    response = client.get("/api/v1/books", query_string={"tag_path": "Horror.Gothic", "select_all": 1, "sort": "abc"})
    assert response.status_code == 200, response.data
    assert response.get_json() == {"ids": expected, "total": len(expected)}
    visible = client.get("/api/v1/books", query_string={"tag_path": "Horror.Gothic", "sort": "abc"})
    assert visible.status_code == 200, visible.data
    assert [book["id"] for book in visible.get_json()["items"]] == expected
    response = client.post("/api/v1/books/export", json={"source": "catalog", "format": "csv",
                         "params": {"tag_path": "Horror.Gothic", "sort": "abc"}})
    assert response.status_code == 200, response.data
    assert response.headers["X-Export-Count"] == str(len(expected))
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
    assert [row[0] for row in rows[1:]] == ["Book " + str(i) for i in expected]
    response.close()
    # Group scope never inherits a hidden/archived recovery escape hatch.
    recovery = client.get("/api/v1/books", query_string={"tag_path": "Horror.Gothic", "select_all": 1,
                                                          "show_hidden": 1, "filter": "archived"})
    assert recovery.status_code == 200, recovery.data
    assert recovery.get_json() == {"ids": [], "total": 0}


@pytest.mark.parametrize("value", ["hot", "discover", "invented"])
def test_group_filters_refuse_unsupported_scope_without_broadening(group_client, value):
    _library, _session, _viewer, client = group_client
    response = client.get("/api/v1/books", query_string={"tag_path": "Horror.Gothic", "filter": value, "select_all": 1})
    assert response.status_code == 400, response.data
    exported = client.post("/api/v1/books/export", json={"source": "catalog", "params": {"tag_path": "Horror.Gothic", "filter": value}})
    assert exported.status_code == 400, exported.data
