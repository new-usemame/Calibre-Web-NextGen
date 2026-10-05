# SPDX-License-Identifier: GPL-3.0-or-later

"""Custom-column visibility defaults and real HTTP cross-surface parity."""

import json

from types import SimpleNamespace

import flask

import pytest

from sqlalchemy import Column, Integer, String, create_engine, text

from sqlalchemy.orm import declarative_base, sessionmaker

from cps import custom_column_visibility as ccv

from cps import db, hierarchy

pytestmark = pytest.mark.unit

def _col(col_id, name, datatype="text"):
    return SimpleNamespace(id=col_id, name=name, datatype=datatype,
                           mark_for_delete=0)

@pytest.fixture
def library(monkeypatch):
    """One hierarchical column (#2) beside one flat Dewey column (#3).

    Books 1-3 carry Dewey values, 4-5 carry Genre values. Book 3 is filed at
    two Dewey rows, which is what a shared classification looks like.
    """
    base = declarative_base()

    class Subject(base):
        __tablename__ = "custom_column_2"
        id = Column(Integer, primary_key=True)
        book = Column(Integer)
        value = Column(String)

    class Ddc(base):
        __tablename__ = "custom_column_3"
        id = Column(Integer, primary_key=True)
        book = Column(Integer)
        value = Column(String)

    engine = create_engine("sqlite://")
    db.Books.__table__.create(engine)
    db.CustomColumns.__table__.create(engine)
    for table in (Subject, Ddc):
        table.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO custom_columns (id, label, name, datatype, is_multiple) VALUES "
            "(2, 'subjects', 'Genre', 'text', 1), (3, 'ddc', 'DDC', 'text', 0)"))
        connection.execute(Subject.__table__.insert(), [
            {"id": 1, "book": 4, "value": "Art"},
            {"id": 2, "book": 5, "value": "Art.Painting"}])
        connection.execute(Ddc.__table__.insert(), [
            {"id": 1, "book": 1, "value": "778.3"},
            {"id": 2, "book": 2, "value": "778.72"},
            {"id": 3, "book": 3, "value": "778.3"},
            {"id": 4, "book": 3, "value": "778.993925"}])
    session = sessionmaker(bind=engine)()

    cdb = object.__new__(db.CalibreDB)
    cdb.session = session
    cdb.ensure_session = lambda: None
    cdb.config = SimpleNamespace(config_columns_to_ignore=None)
    monkeypatch.setattr(db, "cc_classes", {2: Subject, 3: Ddc})
    monkeypatch.setattr(ccv, "db", db)
    monkeypatch.setattr(ccv, "calibre_db", cdb)
    return cdb

class FakeUser:
    """Just enough of ``ub.User`` for the resolution and seeding helpers."""

    is_authenticated = True
    is_anonymous = False

    def __init__(self, name="u", stored=None):
        self.name = name
        self.view_settings = json.loads(stored) if stored else {}

    def get_view_property(self, page, prop):
        if not self.view_settings.get(page):
            return None
        return self.view_settings[page].get(prop)

    def set_view_property(self, page, prop, value, commit=True):
        self.view_settings.setdefault(page, {})[prop] = value
        self.committed = getattr(self, "committed", 0) + (1 if commit else 0)

def _ub_stub(monkeypatch, users):
    """The smallest thing ``backfill_existing_users`` needs from ``ub``."""
    stub = SimpleNamespace(User=object)
    stub.session = SimpleNamespace(
        query=lambda _model: SimpleNamespace(all=lambda: list(users)),
        commit=lambda: None, rollback=lambda: None)
    monkeypatch.setattr(ccv, "ub", stub)
    monkeypatch.setattr(ccv.config, "save", lambda: None, raising=False)
    return stub

@pytest.fixture
def unconfigured(monkeypatch):
    """Unconfigured library defaults, shared by all browse surfaces.

    Carries the rest of the attributes ``get_cc_columns`` reads, because the
    browsable set is built through the same call the sidebar makes.
    """
    config = SimpleNamespace(
        config_columns_to_ignore=None,
        config_read_column=0,
    )
    monkeypatch.setattr(ccv, "config", config)
    return config

def _app():
    from cps.api import api_v1
    app = flask.Flask(__name__)
    app.testing = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test"
    app.config["RATELIMIT_ENABLED"] = False
    app.register_blueprint(api_v1)
    return app

def _viewer(stored=None, categories=True):
    user = FakeUser("viewer", stored=stored)
    user.check_visibility = lambda flag: categories
    return user

def _bable_app():
    """A request context that can translate and build the URLs entries link to.

    Both the sidebar builder and the OPDS root call ``url_for`` on real
    endpoints, and a bare Flask app cannot build them: flask-babel raises
    KeyError 'babel' and werkzeug raises BuildError. The BuildError is the
    nastier of the two because the sidebar builder catches it and returns an
    empty list, so a missing stub looks exactly like "no columns matched".

    The real blueprints are far too heavy to register here and neither is the
    subject under test, so stub blueprints carry the same endpoint names.
    """
    from flask_babel import Babel
    app = flask.Flask(__name__)
    Babel(app)
    opds_stub = flask.Blueprint("opds", __name__)
    opds_stub.add_url_rule("/opds/custom_column/<int:column_id>", "feed_cc_category",
                           lambda column_id: "")
    web_stub = flask.Blueprint("web", __name__)
    web_stub.add_url_rule("/custom_column/<int:column_id>", "cc_category_list",
                          lambda column_id: "")
    app.register_blueprint(opds_stub)
    app.register_blueprint(web_stub)
    return app

def _patch_all_surfaces(monkeypatch, library, unconfigured, user, columns):
    """Point every surface at the same user, library and column list.

    The point of the exercise: each module reaches the user through a different
    name -- ``opds`` has no ``current_user`` of its own and goes through
    ``opds.auth.current_user()`` -- so a parity test that forgets one is
    silently testing a different user object rather than failing.
    """
    from cps import opds, render_template
    from cps import web as web_module
    from cps.api import columns as columns_api
    import cps.api as api_package
    from cps import usermanagement as usermanagement_module

    monkeypatch.setattr(ccv, "config", unconfigured)
    for module in (opds, render_template, web_module, columns_api):
        if hasattr(module, "current_user"):
            monkeypatch.setattr(module, "current_user", user)
    monkeypatch.setattr(opds.auth, "current_user", lambda: user)
    # render_template imports calibre_db *inside* the function, so it resolves
    # through the cps package at call time; the other three bind it at module
    # import. Patching only one of the two silently tests the real database.
    import cps
    monkeypatch.setattr(cps, "calibre_db", library)
    for module in (opds, web_module, columns_api):
        monkeypatch.setattr(module, "calibre_db", library)
    # Deliberately NOT re-stubbing db.cc_classes here: the library fixture
    # installed the real mapped classes, and hierarchy detection reads
    # cc_classes.get(cid).value off them. Substituting bare objects() makes the
    # detection fail, which the fail-closed seed then turns into "hide
    # everything" -- a test failure that reads like a bug in the seeding code.

    route_config = SimpleNamespace(
        config_columns_to_ignore=None,
        config_read_column=0,
        config_books_per_page=24,
        config_anonbrowse=0,
        config_allow_reverse_proxy_header_login=False,
    )
    monkeypatch.setattr(web_module, "config", route_config)
    monkeypatch.setattr(opds, "config", unconfigured)
    # render_template holds the real, unloaded ConfigSQL. get_cc_columns reads
    # config_columns_to_ignore off whatever config it is handed, and an
    # unloaded wrapper raises AttributeError, which it degrades into an empty
    # column list -- a silent empty sidebar that is miserable to debug.
    monkeypatch.setattr(render_template, "config", unconfigured)
    # The auth gate lives in the cps.api package and reads that module's own
    # config, not the endpoint module's, so both have to answer or the request
    # 500s in before_request and never reaches the view under test.
    monkeypatch.setattr(columns_api, "config", route_config)
    monkeypatch.setattr(api_package, "config", route_config)
    # current_user reaches the LocalProxy by four routes: the package-level
    # before_request, the per-route login_required_if_no_ano decorator in
    # usermanagement, the login decorator in cw_login.utils underneath it, and
    # the endpoint modules. A bare test app has no login_manager, so all four
    # have to be answered -- and hasattr() on the proxy itself raises that same
    # AttributeError, hence raising=False throughout.
    from cps.cw_login import utils as cw_login_utils
    monkeypatch.setattr(api_package, "current_user", user, raising=False)
    monkeypatch.setattr(usermanagement_module, "current_user", user, raising=False)
    monkeypatch.setattr(cw_login_utils, "current_user", user, raising=False)
    # The basic-auth decorator on the OPDS routes reads usermanagement's config.
    monkeypatch.setattr(usermanagement_module, "config", route_config)
    return columns_api

BOTH_COLUMNS = [_col(2, "Genre"), _col(3, "DDC")]

def test_a_hidden_column_is_absent_from_the_api_list_and_404s_both_subroutes(
        library, unconfigured, monkeypatch):
    """The SPA must not leak a column the user hid -- on the list, or by asking
    for the tree and the books directly."""
    columns_api = _patch_all_surfaces(
        monkeypatch, library, unconfigured,
        _viewer(stored=json.dumps({"cc_sidebar": {"show_cc_3": False}})), BOTH_COLUMNS)

    client = _app().test_client()
    listed = client.get("/api/v1/columns")
    assert listed.status_code == 200
    assert [c["id"] for c in listed.get_json()["items"]] == [2]
    assert client.get("/api/v1/columns/3/tree").status_code == 404
    assert client.get("/api/v1/columns/3/books?path=778.3").status_code == 404
    assert columns_api._cc_disabled(3) is True
    assert columns_api._cc_disabled(2) is False

def test_the_classic_browse_route_404s_a_column_the_sidebar_hides(
        library, unconfigured, monkeypatch):
    """The legacy hole, and the one a user is most likely to hit by pasting a
    URL: the entry vanished from the sidebar, the page it pointed at did not."""
    from cps import web as web_module
    from werkzeug.exceptions import NotFound
    _patch_all_surfaces(
        monkeypatch, library, unconfigured,
        _viewer(stored=json.dumps({"cc_sidebar": {"show_cc_3": False}})), BOTH_COLUMNS)

    with pytest.raises(NotFound):
        web_module.render_cc_category(1, 3, "", (None, None))

def test_the_opds_root_omits_a_column_the_user_hid(library, unconfigured, monkeypatch):
    """The OPDS root listed every browsable column whatever the profile page
    said, so an OPDS client saw what the web UI hid."""
    from cps import opds
    _patch_all_surfaces(
        monkeypatch, library, unconfigured,
        _viewer(stored=json.dumps({"cc_sidebar": {"show_cc_3": False}})), BOTH_COLUMNS)

    with _bable_app().test_request_context("/opds"):
        entries = opds.get_opds_hierarchy_root_entries(_viewer())

    assert [entry["title"] for entry in entries] == ["Genre"]

def test_the_opds_feed_404s_a_column_the_user_hid(library, unconfigured, monkeypatch):
    """The whole subtree, not just the root entry: a reader who bookmarked a
    node must not keep reaching it after unticking the column."""
    from cps import opds
    from werkzeug.exceptions import NotFound
    _patch_all_surfaces(
        monkeypatch, library, unconfigured,
        _viewer(stored=json.dumps({"cc_sidebar": {"show_cc_3": False}})), BOTH_COLUMNS)

    with _bable_app().test_request_context("/opds/custom_column/3"):
        # __wrapped__ skips requires_basic_auth_if_no_ano, which resolves its
        # own credentials and would short-circuit before the view's own gating
        # is ever reached. What is under test is the per-column check.
        with pytest.raises(NotFound):
            opds.feed_cc_category.__wrapped__(3, "")

@pytest.mark.parametrize("stored,expected_visible", [
    (None, False),                                       # seeded flat, never shown
    ('{"cc_sidebar": {"show_cc_3": true}}', True),      # the user enabled it
    ('{"cc_sidebar": {"show_cc_3": false}}', False),
])
def test_every_surface_agrees_about_one_column(library, unconfigured, monkeypatch,
                                               stored, expected_visible):
    """One test, all five surfaces. This is the parity check a source
    assertion cannot replace: it fails if any surface stops consulting the
    shared resolver, which is exactly how the classic route and both OPDS
    feeds drifted apart in the first place."""
    from cps import opds, render_template
    from cps import web as web_module
    from werkzeug.exceptions import NotFound

    user = _viewer(stored=stored)
    columns_api = _patch_all_surfaces(monkeypatch, library, unconfigured, user,
                                      BOTH_COLUMNS)

    # An explicit local, not `is (not expected_visible)`: `is` binds looser
    # than the reader expects and pytest's assertion rewriter turns the
    # combination into a message about two Trues, which says nothing.
    expected_disabled = not expected_visible
    assert columns_api._cc_disabled(3) is expected_disabled

    with _bable_app().test_request_context("/opds"):
        opds_titles = {entry["title"] for entry in
                       opds.get_opds_hierarchy_root_entries(user)}
    assert ("DDC" in opds_titles) is expected_visible

    with _bable_app().test_request_context("/custom_column/3"):
        sidebar = [entry["text"] for entry in
                   render_template.get_custom_column_sidebar_entries()]
    assert ("DDC" in sidebar) is expected_visible

    # For this input NotFound has exactly one source: the per-column gate.
    # SIDEBAR_CATEGORY passes, and browsable_cc_column() finds column 3, so
    # anything else that escapes means the request got *past* the gate and
    # stopped at the db.Books.custom_column_3 relationship this fixture does
    # not install. That is "reachable", which is what is being asserted.
    try:
        web_module.render_cc_category(1, 3, "", (None, None))
        reached = True
    except NotFound:
        reached = False
    except AttributeError:
        reached = True
    assert reached is expected_visible

def test_categories_disabled_list_is_an_empty_envelope(library, unconfigured, monkeypatch):
    _patch_all_surfaces(monkeypatch, library, unconfigured, _viewer(categories=False), BOTH_COLUMNS)
    response = _app().test_client().get('/api/v1/columns')
    assert response.status_code == 200
    assert response.get_json() == {'items': []}

def test_unsaved_default_tracks_calibre_mode_without_writing_reader_choice(library, unconfigured):
    session = library.session
    session.execute(text('CREATE TABLE preferences (key TEXT PRIMARY KEY, val TEXT)'))
    session.execute(text("INSERT INTO preferences VALUES ('categories_using_hierarchy', '[]')"))
    session.commit()
    user = FakeUser()
    assert not ccv.is_cc_visible(user, 3)
    session.execute(text("UPDATE preferences SET val='[\"#ddc\"]' WHERE key='categories_using_hierarchy'"))
    session.commit()
    assert ccv.is_cc_visible(user, 3)
    assert user.view_settings == {}

def test_compatibility_backfill_preserves_only_legacy_visible_columns(library, unconfigured, monkeypatch):
    user = FakeUser()
    _ub_stub(monkeypatch, [user])
    assert ccv.backfill_existing_users() == 1
    assert user.view_settings == {'cc_sidebar': {'show_cc_2': True}}


def test_unchanged_profile_checkboxes_do_not_freeze_derived_defaults(library, unconfigured):
    user = FakeUser()
    options = [{'id': 2, 'visible': True}, {'id': 3, 'visible': False}]
    ccv.save_cc_visibility(user, options, {'initial_show_cc_2': 'true', 'show_cc_2': 'on', 'initial_show_cc_3': 'false'})
    assert user.view_settings == {}
    ccv.save_cc_visibility(user, options, {'initial_show_cc_2': 'true', 'initial_show_cc_3': 'false', 'show_cc_3': 'on'})
    assert user.view_settings == {'cc_sidebar': {'show_cc_2': False, 'show_cc_3': True}}


def test_readable_empty_library_completes_compatibility_upgrade(library, unconfigured, monkeypatch):
    library.session.execute(text('DELETE FROM custom_columns'))
    library.session.commit()
    monkeypatch.setattr(db, 'cc_classes', {})
    _ub_stub(monkeypatch, [])
    assert ccv.backfill_existing_users() == 0
    assert unconfigured.config_cc_visibility_seeded

@pytest.mark.parametrize('existing', [False, True])
def test_fresh_settings_skip_legacy_upgrade_and_existing_settings_keep_it(existing):
    from cps import config_sql
    engine = create_engine('sqlite://')
    config_sql._Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        if existing:
            session.add(config_sql._Settings(config_cc_visibility_seeded=False))
            session.commit()
            # A real old table, rather than a row in an already-new schema.
            session.execute(text('ALTER TABLE settings DROP COLUMN config_cc_visibility_seeded'))
            session.commit()
            session.expire_all()
        config_sql.load_configuration(session, None)
        assert session.query(config_sql._Settings).one().config_cc_visibility_seeded is (not existing)
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("guest_choice,session_choice,expected", [
    (False, True, False), (False, "crafted", False), (True, False, True),
    (True, None, True), (False, None, False),
    (None, True, True), (None, False, False), (None, None, False),
])
def test_explicit_guest_choice_wins_over_browser_session(monkeypatch, guest_choice, session_choice, expected):
    from cps import ub
    guest = ub.Anonymous.__new__(ub.Anonymous)
    guest.view_settings = {"cc_sidebar": {"show_cc_2": guest_choice}}
    monkeypatch.setattr(ccv.calibre_db, "get_hierarchical_column_ids", lambda: set())
    with _app().test_request_context():
        if session_choice is not None:
            flask.session["view"] = {"cc_sidebar": {"show_cc_2": session_choice}}
        assert ccv.is_cc_visible(guest, 2) is expected


def test_api_definition_read_failure_is_retryable_not_empty(library, unconfigured, monkeypatch):
    from sqlalchemy.exc import OperationalError
    _patch_all_surfaces(monkeypatch, library, unconfigured, _viewer(), BOTH_COLUMNS)
    def unavailable(*args, **kwargs):
        raise OperationalError("SELECT custom_columns", {}, Exception("database locked"))
    monkeypatch.setattr(library, "get_cc_columns", unavailable)
    response = _app().test_client().get("/api/v1/columns")
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "service_unavailable"


def test_compatibility_upgrade_retries_after_a_legacy_value_read_failure(library, unconfigured, monkeypatch):
    from sqlalchemy.exc import OperationalError
    _ub_stub(monkeypatch, [FakeUser()])
    unconfigured.config_cc_visibility_seeded = False
    original_query = library.session.query
    def query(*args, **kwargs):
        if args and args[0] is db.cc_classes[2].value:
            raise OperationalError("SELECT values", {}, Exception("database locked"))
        return original_query(*args, **kwargs)
    monkeypatch.setattr(library.session, "query", query)
    assert ccv.backfill_existing_users() == 0
    assert not unconfigured.config_cc_visibility_seeded


def test_no_configured_library_does_not_seed_a_later_library(monkeypatch):
    saved = []
    config = SimpleNamespace(config_cc_visibility_seeded=False, config_calibre_dir=None, save=lambda: saved.append(True))
    monkeypatch.setattr(ccv, "config", config)
    def must_not_read():
        raise AssertionError("an unconfigured library has no legacy columns to preserve")
    monkeypatch.setattr(ccv, "load_browsable_columns", must_not_read)
    assert ccv.backfill_existing_users() == 0
    config.config_calibre_dir = "/later/library"
    assert ccv.backfill_existing_users() == 0
    assert saved == [True]


@pytest.mark.parametrize("path", ["/api/v1/columns", "/api/v1/columns/2/tree", "/api/v1/columns/2/books?path=Art"])
def test_hierarchy_preference_failure_is_retryable_primary_content(library, unconfigured, monkeypatch, path):
    from sqlalchemy.exc import OperationalError
    _patch_all_surfaces(monkeypatch, library, unconfigured, _viewer(), BOTH_COLUMNS)
    def unavailable(*args, **kwargs):
        raise OperationalError("SELECT preferences", {}, Exception("database locked"))
    monkeypatch.setattr(library, "_read_hierarchical_column_ids", unavailable)
    response = _app().test_client().get(path)
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "service_unavailable"


@pytest.mark.parametrize("column_id", [2, 3])
def test_value_query_failure_is_not_an_empty_column(library, unconfigured, monkeypatch, column_id):
    from sqlalchemy.orm import relationship, foreign
    from sqlalchemy.exc import OperationalError
    base = declarative_base()
    subject, ddc = db.cc_classes[2], db.cc_classes[3]
    class BrowseBook(base):
        __table__ = db.Books.__table__
        custom_column_2 = relationship(subject, primaryjoin=__table__.c.id == foreign(subject.book), viewonly=True)
        custom_column_3 = relationship(ddc, primaryjoin=__table__.c.id == foreign(ddc.book), viewonly=True)
    monkeypatch.setattr(db, "Books", BrowseBook)
    _patch_all_surfaces(monkeypatch, library, unconfigured, _viewer(), BOTH_COLUMNS)
    monkeypatch.setattr(library, "common_filters", lambda: text("1=1"))
    # Flat defaults are hidden until explicitly chosen.
    from cps.api import columns as api
    api.current_user.view_settings = {"cc_sidebar": {"show_cc_3": True}}
    # Determine mode successfully first; only the actual value SELECT fails.
    from sqlalchemy import event
    def fail_values(connection, cursor, statement, parameters, context, executemany):
        if "JOIN custom_column_" in statement:
            raise OperationalError(statement, {}, Exception("database locked"))
    engine = library.session.get_bind()
    event.listen(engine, "before_cursor_execute", fail_values)
    try:
        response = _app().test_client().get(f"/api/v1/columns/{column_id}/tree")
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "service_unavailable"
    finally:
        event.remove(engine, "before_cursor_execute", fail_values)


def test_delayed_compatibility_upgrade_excludes_accounts_created_after_migration(library, unconfigured, monkeypatch):
    old, new = FakeUser("old"), FakeUser("new")
    old.id, new.id = 9, 10
    unconfigured.config_cc_visibility_legacy_user_id = 9
    _ub_stub(monkeypatch, [old, new])
    monkeypatch.setattr(ccv, "load_browsable_columns", lambda: None)
    assert ccv.backfill_existing_users() == 0
    monkeypatch.setattr(ccv, "load_browsable_columns", lambda: BOTH_COLUMNS)
    assert ccv.backfill_existing_users() == 1
    assert old.view_settings == {"cc_sidebar": {"show_cc_2": True}}
    assert new.view_settings == {}


def test_upgrade_account_boundary_survives_restart_before_library_available():
    from cps import config_sql
    engine = create_engine("sqlite://")
    config_sql._Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE user (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO user VALUES (9)"))
        connection.execute(text("INSERT INTO settings (config_cc_visibility_seeded) VALUES (0)"))
        connection.execute(text("ALTER TABLE settings DROP COLUMN config_cc_visibility_seeded"))
        connection.execute(text("ALTER TABLE settings DROP COLUMN config_cc_visibility_legacy_user_id"))
    session = sessionmaker(bind=engine)()
    try:
        config_sql.load_configuration(session, None)
        assert session.query(config_sql._Settings).one().config_cc_visibility_legacy_user_id == 9
        session.execute(text("INSERT INTO user VALUES (10)"))
        session.commit()
        session.close()
        session = sessionmaker(bind=engine)()
        config_sql.load_configuration(session, None)
        assert session.query(config_sql._Settings).one().config_cc_visibility_legacy_user_id == 9
    finally:
        session.close()
        engine.dispose()


def test_admin_column_options_use_the_edited_readers_choices(library, unconfigured, monkeypatch):
    from cps import render_template
    admin = _viewer()
    target = FakeUser("Guest", stored='{"cc_sidebar":{"show_cc_2":false,"show_cc_3":true}}')
    _patch_all_surfaces(monkeypatch, library, unconfigured, admin, BOTH_COLUMNS)
    with _bable_app().test_request_context():
        options = render_template.get_custom_column_visibility_options(target)
    assert {item["id"]: item["visible"] for item in options} == {2: False, 3: True}
    assert admin.view_settings == {}
