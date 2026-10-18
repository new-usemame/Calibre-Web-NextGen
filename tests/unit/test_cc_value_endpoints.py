# SPDX-License-Identifier: GPL-3.0-or-later
"""The six /api/v1/columns/<id>/values/* endpoints, executed for real.

Every earlier suite in this feature drives the rule layer (``cps/hierarchy.py``)
or the sidecar primitives (``cps/cc_sidecar.py``) directly. No test anywhere
POSTs to the endpoints themselves -- the HTTP contract was entirely unpinned:
the 400/404/409 shapes, the ``confirm_merges`` gate, the ``after`` anchor
placement, the branch-deletion workflow the UI has to drive leaf-by-leaf, and
what the tree looks like after each write.

The fixture is a real two-file library (metadata.db tables + app.db sidecar)
with a REAL mapped link-table column, driven through a real Flask test client
with an authenticated editor, mirroring the three proven patterns:
``test_cc_value_bookless.py`` (mapped classes on ``db.Base``),
``test_cc_reorder_endpoint.py`` (two-file ATTACH wiring),
``test_cc_visibility_seeding.py`` (bare Flask app + current_user patching).

``metadata_db_write_lock`` is replaced by a no-op context manager: its own
behaviour is covered by the write-coordination suite, and a real file lock
would only couple these tests to the host's config directory. The enforcer
queue and the application log are recorded, not written.

Column id 2411 is deliberately private: 2/3 belong to test_flat_custom_columns,
2402/2403 to test_cc_value_bookless. Reusing a neighbour's id passes alone and
fails in a full run -- see the warning at the top of the bookless file.
"""
import contextlib
import logging
import sqlite3
from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import Column, ForeignKey, Integer, String, create_engine, event, text
from sqlalchemy.orm import relationship
from sqlalchemy.pool import StaticPool

from cps import cc_sidecar, db, hierarchy
from cps.api import cc_values
from cps.api import columns as columns_api
from cps.api import browse as browse_api

pytestmark = pytest.mark.unit

COL_ID = 2411

# Declared ONCE at module scope -- defining these inside the fixture would
# re-register the tables on the shared db.Base MetaData on every test, which
# SQLAlchemy rejects.
_CC_LINK = db.Table(
    'books_custom_column_%d_link' % COL_ID, db.Base.metadata,
    Column('book', Integer, ForeignKey('books.id'), primary_key=True),
    Column('value', Integer, ForeignKey('custom_column_%d.id' % COL_ID),
           primary_key=True),
)


class _Val(db.Base):
    """The link-table shape every hierarchical text column uses."""
    __tablename__ = 'custom_column_%d' % COL_ID
    id = Column(Integer, primary_key=True)
    value = Column(String, nullable=False, unique=True)
    # back_populates, not two free-standing relationships: both ends write the
    # same link table, and without shared state the unit of work deletes a
    # link row once per relationship ("expected to delete 2 row(s); only 1
    # were matched"). The production classes are one relationship pair.
    books = relationship(db.Books, uselist=True, secondary=_CC_LINK,
                         back_populates='custom_column_%d' % COL_ID)


def _install_books_side(col_id, model):
    """Attach ``Books.custom_column_<id>``; return a callable that undoes it.

    Same trap as in test_cc_value_bookless: monkeypatch cannot undo a
    relationship on an already-mapped class, so this is plain setattr with a
    restore callable the fixture MUST call.
    """
    name = 'custom_column_%s' % col_id
    previous = getattr(db.Books, name, None)
    setattr(db.Books, name, relationship(
        model, secondary=_CC_LINK, uselist=True,
        back_populates='books'))
    return lambda: setattr(db.Books, name, previous)


@pytest.fixture(scope='module', autouse=True)
def _books_side():
    """Install ``Books.custom_column_2411`` once for the whole module.

    Re-installing the relationship per test churns the mapper wiring: the
    back_populates pair from the previous install stays registered alongside
    the new one, and the unit of work then deletes each link row twice
    (StaleDataError, "expected to delete 1 row(s); only 0 were matched") --
    the first delete passes, every later one fails. One stable install per
    module keeps one relationship pair; sessions still resolve against each
    test's own engine.
    """
    restore = _install_books_side(COL_ID, _Val)
    yield
    restore()


@pytest.fixture()
def library(tmp_path, monkeypatch):
    meta_path = str(tmp_path / "metadata.db")
    app_path = str(tmp_path / "app.db")

    engine = create_engine(
        "sqlite://", isolation_level="SERIALIZABLE",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=StaticPool)

    def _attach(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        try:
            # The sidecar's ROWS are read through the Calibre session with an
            # `app_settings.` prefix (only the DDL goes through ub's bind --
            # SQLite cannot CREATE INDEX in an attached schema). The empty
            # metadata.db attachment exists for db.Data alone, which is
            # declared with schema='calibre' and so needs the schema present
            # for db.Base.metadata.create_all below.
            cur.execute("ATTACH DATABASE ? AS calibre", (meta_path,))
            cur.execute("ATTACH DATABASE ? AS app_settings", (app_path,))
        finally:
            cur.close()

    event.listen(engine, "connect", _attach)

    # The WHOLE db.Base metadata, not just three tables: db.Books carries
    # eight lazy='selectin' relationships (authors, tags, comments, data,
    # series, ratings, languages, publishers, ...), so the moment any code
    # loads a Book object SQLAlchemy eagerly reads all of them. A minimal
    # schema dies with "no such table: books_tags_link" on the first
    # row.books access. In-memory CREATE TABLEs are effectively free.
    db.Base.metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO custom_columns (id, label, name, datatype, is_multiple) "
            "VALUES (2411, 'genre', 'Genres', 'text', 1)"))
        connection.execute(text(
            "INSERT INTO books (id, title, sort, series_index, path) VALUES "
            "(1, 'One', 'One', 1.0, 'One (1)'), "
            "(2, 'Two', 'Two', 2.0, 'Two (2)'), "
            "(3, 'Three', 'Three', 3.0, 'Three (3)')"))
        # Arts has two sub-values (one carried by two books, one bookless);
        # Mystery is a root leaf carried by book 3.
        connection.execute(text(
            "INSERT INTO custom_column_2411 (id, value) VALUES "
            "(1, 'Arts'), (2, 'Arts.Fiction'), (3, 'Arts.History'), (4, 'Mystery')"))
        connection.execute(text(
            "INSERT INTO books_custom_column_2411_link (book, value) VALUES "
            "(1, 1), (1, 2), (2, 2), (3, 4)"))

    sqlite3.connect(meta_path).close()
    sqlite3.connect(app_path).close()

    calibre = db.CalibreDB.__new__(db.CalibreDB)
    session = db.sessionmaker(bind=engine)()
    calibre.session = session
    calibre.ensure_session = lambda *a, **k: None
    calibre.is_flat_cc_column = lambda col_id: False
    calibre.set_metadata_dirty = lambda *a, **k: None

    def _tree(col_id, **_k):
        """The real read shape: book-attached rows build the tree, bookless
        rows are merged in afterwards with zero counts."""
        order = cc_sidecar.stored_order(col_id)
        attached, bookless = [], []
        for row in session.query(_Val).order_by(_Val.id).all():
            if row.books:
                for book in row.books:
                    attached.append((book.id, row.value))
            else:
                bookless.append(row.value)
        nodes = hierarchy.parse_tag_hierarchy(attached, order)
        return hierarchy.add_bookless_nodes(nodes, bookless, order)

    calibre.get_hierarchical_tree = _tree

    def _column_row():
        return SimpleNamespace(id=COL_ID, name='Genres', datatype='text')

    editor = SimpleNamespace(
        name='editor', is_authenticated=True, is_anonymous=False,
        role_edit=lambda: True, check_visibility=lambda flag: True)

    from cps import usermanagement as usermanagement_module
    from cps.cw_login import utils as cw_login_utils
    from cps import helper as helper_module

    route_config = SimpleNamespace(
        config_anonbrowse=0, config_allow_reverse_proxy_header_login=False,
        config_columns_to_ignore=None, config_read_column=0,
        config_books_per_page=24)

    for module in (cc_values, cc_sidecar, columns_api, browse_api):
        monkeypatch.setattr(module, "calibre_db", calibre)
    # helper.mark_book_modified resolves the singleton through helper's own
    # binding; the real instance is uninitialised in a unit test.
    monkeypatch.setattr(helper_module, "calibre_db", calibre)
    monkeypatch.setattr(cc_sidecar, "db", db)
    monkeypatch.setattr(
        cc_sidecar.ub, "session",
        db.sessionmaker(bind=create_engine(
            "sqlite:///%s" % app_path.replace("\\", "/"),
            connect_args={"timeout": 30}, poolclass=StaticPool))())
    monkeypatch.setattr(db, "cc_classes", {COL_ID: _Val})
    # The endpoint resolves the column through columns._visible_columns; its
    # internals are the visibility feature's tests' business. The id match and
    # the cc_classes check in _column_or_404 stay real.
    monkeypatch.setattr(columns_api, "_visible_columns", lambda: [_column_row()])
    for module in (cc_values, browse_api, columns_api):
        monkeypatch.setattr(module, "current_user", editor, raising=False)
    monkeypatch.setattr(usermanagement_module, "current_user", editor, raising=False)
    monkeypatch.setattr(cw_login_utils, "current_user", editor, raising=False)
    monkeypatch.setattr(usermanagement_module, "config", route_config)
    import cps.api as api_package
    monkeypatch.setattr(api_package, "config", route_config, raising=False)
    # The package-level before_request (_require_api_auth) reads the proxy
    # bound in cps/api/__init__ -- patching cw_login_utils alone does not
    # reach it, and an unpatched proxy needs a login_manager the bare app
    # does not have.
    monkeypatch.setattr(api_package, "current_user", editor, raising=False)
    monkeypatch.setattr(columns_api, "config", route_config, raising=False)

    @contextlib.contextmanager
    def _no_lock():
        yield

    monkeypatch.setattr(cc_values, "metadata_db_write_lock", _no_lock)
    monkeypatch.setattr(cc_values, "log", logging.getLogger("test-cc-endpoints"))
    queued = []
    monkeypatch.setattr(cc_values.helper, "log_metadata_change",
                        lambda book, changed=None: queued.append(book.id))

    cc_sidecar.ensure_tables()

    handle = SimpleNamespace(meta_path=meta_path, app_path=app_path,
                             calibre=calibre, session=session, queued=queued)
    try:
        yield handle
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(library):
    from flask_babel import Babel

    from cps.api import api_v1
    app = flask.Flask(__name__)
    app.testing = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test"
    Babel(app)
    app.register_blueprint(api_v1)
    return app.test_client()


def _post(client, action, payload, col_id=COL_ID):
    return client.post("/api/v1/columns/%d/values/%s" % (col_id, action),
                       json=payload)


def _tree_paths(calibre):
    flat = {}

    def walk(nodes):
        for node in nodes:
            flat[node["path"]] = node
            walk(node["children"])

    walk(calibre.get_hierarchical_tree(COL_ID))
    return flat


def _stored_values(lib):
    # No `calibre.` prefix: the mapped tables live in `main` (the in-memory
    # engine); the empty metadata.db attachment is there for db.Data alone.
    return sorted(row[0] for row in lib.session.execute(
        text('SELECT "value" FROM custom_column_2411')).fetchall())


# --------------------------------------------------------------------------- #
# Reorder (sorting)
# --------------------------------------------------------------------------- #

def test_a_reorder_through_the_endpoint_positions_the_whole_level(client, library):
    """First execution of the reorder route anywhere in the suite: 200, the
    whole sibling list is positioned, and the tree reads back in the new order."""
    response = _post(client, "reorder", {"path": "Arts.History", "direction": "up"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["order"] == ["Arts.History", "Arts.Fiction"]
    assert cc_sidecar.sibling_order(COL_ID, "Arts") == {
        "Arts.History": 0, "Arts.Fiction": 1}
    children = _tree_paths(library.calibre)["Arts"]["children"]
    assert [c["path"] for c in children] == ["Arts.History", "Arts.Fiction"]


def test_reordering_the_first_value_conflicts_with_409(client):
    response = _post(client, "reorder", {"path": "Arts.Fiction", "direction": "up"})

    assert response.status_code == 409
    assert "top" in response.get_json()["error"]["message"]


def test_reordering_the_last_value_conflicts_with_409(client, library):
    _post(client, "reorder", {"path": "Arts.History", "direction": "up"})

    response = _post(client, "reorder", {"path": "Arts.History", "direction": "up"})

    assert response.status_code == 409
    assert "top" in response.get_json()["error"]["message"]


def test_reordering_the_bottom_conflicts_with_409(client):
    response = _post(client, "reorder", {"path": "Arts.History", "direction": "down"})

    assert response.status_code == 409
    assert "bottom" in response.get_json()["error"]["message"]


def test_reordering_an_unknown_value_404s(client):
    response = _post(client, "reorder", {"path": "Arts.Nope", "direction": "up"})

    assert response.status_code == 404
    assert response.get_json()["error"]["message"] == "Value not found"


@pytest.mark.parametrize("payload", [
    {"direction": "up"},                                # no path
    {"path": "   ", "direction": "up"},                 # blank path
    {"path": "Arts.Fiction"},                           # no direction
    {"path": "Arts.Fiction", "direction": "sideways"},  # not a direction
])
def test_reorder_validates_the_payload_with_400(client, payload):
    assert _post(client, "reorder", payload).status_code == 400


def test_reordering_a_root_branch_moves_it_among_its_own_siblings_only(
        client, library):
    """Roots are [Arts, Mystery]; moving Mystery up must not touch anything
    under Arts -- a move cannot cross a level."""
    response = _post(client, "reorder", {"path": "Mystery", "direction": "up"})

    assert response.status_code == 200
    assert response.get_json()["order"] == ["Mystery", "Arts"]
    flat = _tree_paths(library.calibre)
    assert [n["path"] for n in flat["Arts"]["children"]] == \
        ["Arts.Fiction", "Arts.History"], "the branch's children are untouched"
    assert flat["Arts"]["total_count"] == 2


def test_reorder_rewrites_no_stored_value(client, library):
    before = _stored_values(library)
    assert _post(client, "reorder", {"path": "Mystery", "direction": "up"}
                 ).status_code == 200

    assert _stored_values(library) == before, \
        "reorder changes only the order, never a value"


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #

def test_creating_a_value_makes_it_visible_with_zero_counts(client, library):
    response = _post(client, "create", {"path": "Arts.Crime"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["created"] == "Arts.Crime"
    assert body["books"] == 0
    flat = _tree_paths(library.calibre)
    assert flat["Arts.Crime"]["total_count"] == 0
    assert flat["Arts"]["total_count"] == 2, "the parent's count is unchanged"
    assert "Arts.Crime" in _stored_values(library)


def test_creating_a_deep_dotted_name_synthesises_every_level(client, library):
    response = _post(client, "create", {"path": "X.Y.Z"})

    assert response.status_code == 200
    flat = _tree_paths(library.calibre)
    assert {"X", "X.Y", "X.Y.Z"} <= set(flat)
    assert flat["X.Y.Z"]["total_count"] == 0
    assert flat["X"]["total_count"] == 0, "no book is visible at any level"


def test_creating_a_duplicate_conflicts_with_409(client):
    response = _post(client, "create", {"path": "Arts"})

    assert response.status_code == 409
    assert "already exists" in response.get_json()["error"]["message"]


@pytest.mark.parametrize("name", ["", "   ", ".", ".."])
def test_creating_a_blank_or_dot_only_name_is_refused_with_400(client, name):
    assert _post(client, "create", {"path": name}).status_code == 400


def test_creating_a_name_with_a_comma_is_refused_with_400(client):
    assert _post(client, "create", {"path": "Arts,Fiction"}).status_code == 400


def test_a_created_value_without_an_anchor_sorts_alphabetically(client, library):
    _post(client, "create", {"path": "Arts.Aaa"})

    assert "Arts.Aaa" not in cc_sidecar.stored_order(COL_ID), \
        "a created value has no curated position until somebody gives it one"
    children = _tree_paths(library.calibre)["Arts"]["children"]
    assert [c["path"] for c in children] == \
        ["Arts.Aaa", "Arts.Fiction", "Arts.History"]


def test_an_anchor_places_the_new_value_after_its_neighbour(client, library):
    response = _post(client, "create",
                     {"path": "Arts.Crime", "after": "Arts.Fiction"})

    assert response.status_code == 200
    assert cc_sidecar.sibling_order(COL_ID, "Arts") == {
        "Arts.Fiction": 0, "Arts.Crime": 1, "Arts.History": 2}
    children = _tree_paths(library.calibre)["Arts"]["children"]
    assert [c["path"] for c in children] == \
        ["Arts.Fiction", "Arts.Crime", "Arts.History"]


def test_an_anchor_from_another_level_is_refused_with_400(client, library):
    response = _post(client, "create",
                     {"path": "Arts.Thriller", "after": "Mystery"})

    assert response.status_code == 400
    assert "Arts.Thriller" not in _stored_values(library), \
        "a refused anchor must not create the value either"


def test_a_stale_anchor_still_creates_the_value_unordered(client, library):
    """The anchor vanished between render and click; the create itself did
    exactly what was asked, so it must succeed -- just unordered."""
    response = _post(client, "create",
                     {"path": "Arts.Thriller", "after": "Arts.Ghost"})

    assert response.status_code == 200
    assert "Arts.Thriller" in _stored_values(library)
    assert "Arts.Thriller" not in cc_sidecar.stored_order(COL_ID)


def test_creating_a_unicode_value_round_trips(client, library):
    response = _post(client, "create", {"path": "Kafe.Books"})

    assert response.status_code == 200
    flat = _tree_paths(library.calibre)
    assert "Kafe.Books" in flat
    assert flat["Kafe"]["total_count"] == 0


# --------------------------------------------------------------------------- #
# Delete -- individual nodes and whole branches
# --------------------------------------------------------------------------- #

def test_deleting_a_leaf_keeps_the_books_and_drops_the_value(client, library):
    response = _post(client, "delete", {"paths": ["Arts.Fiction"]})

    assert response.status_code == 200
    body = response.get_json()
    assert body["deleted"] == ["Arts.Fiction"]
    assert body["books"] == 2
    remaining_books = library.session.execute(
        text("SELECT COUNT(*) FROM books")).scalar()
    assert remaining_books == 3, "the books themselves survive"
    remaining_links = library.session.execute(
        text("SELECT COUNT(*) FROM books_custom_column_2411_link "
             "WHERE value = 2")).scalar()
    assert remaining_links == 0, "the books lose the value"
    assert "Arts.Fiction" not in _stored_values(library)


def test_deleting_a_value_with_sub_values_is_refused_with_400(client, library):
    response = _post(client, "delete", {"paths": ["Arts"]})

    assert response.status_code == 400
    assert "sub-values" in response.get_json()["error"]["message"]
    assert "Arts" in _stored_values(library), "nothing was deleted"


def test_deleting_an_unknown_value_is_refused_with_400(client):
    response = _post(client, "delete", {"paths": ["Arts.Ghost"]})

    assert response.status_code == 400
    assert "no longer exists" in response.get_json()["error"]["message"]


def test_deleting_requires_at_least_one_path(client):
    assert _post(client, "delete", {"paths": []}).status_code == 400


def test_deleting_several_leaves_in_one_request(client, library):
    response = _post(client, "delete", {"paths": ["Arts.History", "Mystery"]})

    assert response.status_code == 200
    assert response.get_json()["deleted"] == ["Arts.History", "Mystery"]
    assert response.get_json()["books"] == 1, "only Mystery had a book"
    assert _stored_values(library) == ["Arts", "Arts.Fiction"]


def test_a_branch_is_removed_bottom_up_one_level_at_a_time(client, library):
    """The documented workflow for deleting a BRANCH. Delete is leaves-only, so
    the UI must walk the branch: the parent is refused while it has children,
    the leaves go one by one, and the parent -- now a childless, bookless leaf
    -- becomes deletable itself. Pinned here because no other test exercises
    the sequence, and because the "childless parent lingers as a removable
    leaf" step is the behaviour a 'delete the whole branch' button would have
    to reproduce."""
    assert _post(client, "delete", {"paths": ["Arts"]}).status_code == 400, \
        "the branch root is refused while it still has sub-values"

    assert _post(client, "delete", {"paths": ["Arts.Fiction"]}).status_code == 200
    assert _post(client, "delete", {"paths": ["Arts.History"]}).status_code == 200

    flat = _tree_paths(library.calibre)
    assert "Arts.Fiction" not in flat and "Arts.History" not in flat
    assert "Arts" in flat, "the childless parent lingers as a removable leaf"
    assert flat["Arts"]["total_count"] == 1, \
        "book 1 still carries Arts directly, so the count stays honest"
    assert flat["Arts"]["children"] == []

    response = _post(client, "delete", {"paths": ["Arts"]})
    assert response.status_code == 200, "the former parent is now a deletable leaf"
    assert response.get_json()["books"] == 1
    assert set(_stored_values(library)) == {"Mystery"}


def test_delete_then_recreate_does_not_inherit_the_old_position(client, library):
    """A recreated value must not silently take the dead position of the one
    that was deleted -- the whole reason delete drops order rows."""
    assert _post(client, "reorder",
                 {"path": "Arts.Fiction", "direction": "down"}).status_code == 200
    assert cc_sidecar.sibling_order(COL_ID, "Arts") == {
        "Arts.History": 0, "Arts.Fiction": 1}

    assert _post(client, "delete", {"paths": ["Arts.Fiction"]}).status_code == 200
    assert _post(client, "create", {"path": "Arts.Fiction"}).status_code == 200

    assert "Arts.Fiction" not in cc_sidecar.stored_order(COL_ID), \
        "the recreated value must sort alphabetically, not inherit a dead position"


# --------------------------------------------------------------------------- #
# Merge and rename -- the opt-in gates
# --------------------------------------------------------------------------- #

def test_merging_a_leaf_into_a_parent_with_sub_values_is_allowed(client, library):
    """Only SOURCES must be leaves; folding Mystery into Arts (which has
    sub-values) is well defined and must go through."""
    response = _post(client, "merge", {"paths": ["Mystery"], "into": "Arts"})

    assert response.status_code == 200
    assert response.get_json()["books"] == 1
    links = library.session.execute(text(
        "SELECT cv.value FROM books_custom_column_2411_link l "
        "JOIN custom_column_2411 cv ON cv.id = l.value "
        "WHERE l.book = 3")).scalars().all()
    assert links == ["Arts"], "book 3 carries the survivor, not the source"
    assert _tree_paths(library.calibre)["Arts"]["total_count"] == 3


def test_merging_into_a_sub_value_is_refused_with_400(client, library):
    response = _post(client, "merge", {"paths": ["Arts"], "into": "Arts.Fiction"})

    assert response.status_code == 400
    assert _stored_values(library) == \
        ["Arts", "Arts.Fiction", "Arts.History", "Mystery"]


def test_renaming_onto_an_existing_value_requires_explicit_confirmation(
        client, library):
    """The opt-in contract: the first answer is 409 carrying the merge, and the
    books are untouched until the caller repeats with confirm_merges."""
    first = _post(client, "rename", {"path": "Mystery", "new_name": "Arts"})

    assert first.status_code == 409
    conflicts = first.get_json()["error"]["conflicts"]
    assert conflicts == [{"from": "Mystery", "into": "Arts", "books": 1}]
    assert "Mystery" in _stored_values(library), "nothing merged yet"

    second = _post(client, "rename",
                   {"path": "Mystery", "new_name": "Arts", "confirm_merges": True})

    assert second.status_code == 200
    assert "Mystery" not in _stored_values(library)
    links = library.session.execute(text(
        "SELECT cv.value FROM books_custom_column_2411_link l "
        "JOIN custom_column_2411 cv ON cv.id = l.value "
        "WHERE l.book = 3")).scalars().all()
    assert links == ["Arts"], "book 3 folded onto the survivor"


def test_renaming_an_interior_node_cascades_and_keeps_its_position(
        client, library):
    assert _post(client, "reorder",
                 {"path": "Arts.Fiction", "direction": "down"}).status_code == 200

    response = _post(client, "rename", {"path": "Arts", "new_name": "Culture"})

    assert response.status_code == 200
    assert response.get_json()["renamed"] == {
        "Arts": "Culture", "Arts.Fiction": "Culture.Fiction",
        "Arts.History": "Culture.History"}
    flat = _tree_paths(library.calibre)
    assert [c["path"] for c in flat["Culture"]["children"]] == \
        ["Culture.History", "Culture.Fiction"], \
        "the curated order travels onto the new spelling"


def test_renaming_to_an_empty_name_is_refused_with_400(client):
    assert _post(client, "rename", {"path": "Arts", "new_name": "   "}
                 ).status_code == 400


def test_a_non_editor_is_refused_with_403(client, library, monkeypatch):
    """The gate is the same role check every endpoint enforces; here it is
    exercised through HTTP rather than asserted from source."""
    stranger = SimpleNamespace(
        name='viewer', is_authenticated=True, is_anonymous=False,
        role_edit=lambda: False, check_visibility=lambda flag: True)
    for module in (cc_values, browse_api):
        monkeypatch.setattr(module, "current_user", stranger)

    response = _post(client, "delete", {"paths": ["Mystery"]})

    assert response.status_code == 403
    assert "Mystery" in _stored_values(library)
