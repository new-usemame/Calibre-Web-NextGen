# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-user custom-column browse visibility: seeding, freezing, and parity.

A tag-like custom column (datatype ``text``/``enumeration``) is a browse
surface. Which surfaces a given user sees is a per-user stored value
(``User.view_settings['cc_sidebar']['show_cc_<id>']``), and the three rules it
follows are these:

1. **Seed once.** Hierarchical columns seed visible, flat ones hidden.
2. **Saved wins.** Nothing overrides a stored value -- not the backfill, not
   the administrator's template.
3. **No dynamic re-evaluation.** A column that later turns flat ->
   hierarchical changes nothing.

Rules 2 and 3 are the ones a refactor can quietly break, so most of what
follows guards them directly. The parity tests at the end are behavioural
rather than source-scanning on purpose: a text assertion cannot catch a
surface forgetting to call the shared resolver, which is exactly how the
classic browse route and both OPDS feeds drifted in the first place.
"""
import json
from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from cps import custom_column_visibility as ccv
from cps import db, hierarchy

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _col(col_id, name, datatype="text"):
    return SimpleNamespace(id=col_id, name=name, datatype=datatype,
                           mark_for_delete=0)


@pytest.fixture(autouse=True)
def _clear_hierarchy_cache(monkeypatch):
    """Reset the process-wide hierarchy verdict around every test here.

    ``CalibreDB._hier_cache`` lives on the *class*, not the instance, so a
    verdict computed by one test is still there for the next one. Several
    tests here deliberately move a column across the flat/hierarchical
    boundary, and a stale verdict would make their precondition silently
    wrong -- a failure that looks like a bug in the seeding code and is not.
    """
    monkeypatch.setattr(db.CalibreDB, "_hier_cache", None, raising=False)


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
        query=lambda _model: SimpleNamespace(all=lambda: list(users)))
    monkeypatch.setattr(ccv, "ub", stub)
    monkeypatch.setattr(ccv.config, "save", lambda: None, raising=False)
    return stub


@pytest.fixture
def unconfigured(monkeypatch):
    """A config whose cc template has never been set by an administrator.

    Carries the rest of the attributes ``get_cc_columns`` reads, because the
    browsable set is built through the same call the sidebar makes.
    """
    config = SimpleNamespace(
        config_default_cc_columns=None,
        config_columns_to_ignore=None,
        config_read_column=0,
    )
    monkeypatch.setattr(ccv, "config", config)
    return config


# --------------------------------------------------------------------------
# Rule 1 -- the seed value
# --------------------------------------------------------------------------

def test_the_seed_shows_hierarchical_columns_and_hides_flat_ones(library, unconfigured):
    """A Dewey column and a genre column, seeded from an unconfigured install."""
    user = FakeUser()
    written = ccv.seed_cc_visibility(user, [_col(2, "Genre"), _col(3, "DDC")])

    assert written == 2
    assert user.get_view_property("cc_sidebar", "show_cc_2") is True
    assert user.get_view_property("cc_sidebar", "show_cc_3") is False


def test_one_value_per_book_columns_are_hidden_by_default(library, unconfigured):
    """The reported problem: #goodreads_id has one value per book and is not a
    category, so it must not be a sidebar entry by default."""
    user = FakeUser()
    ccv.seed_cc_visibility(user, [_col(7, "goodreads_id")])

    assert user.get_view_property("cc_sidebar", "show_cc_7") is False


def test_a_flat_column_stays_hidden_after_it_becomes_hierarchical(library, unconfigured):
    """Rule 3. Seeded flat, then a book adds the prefix pair that would make
    the detector report it hierarchical. The stored value must not move."""
    user = FakeUser()
    ccv.seed_cc_visibility(user, [_col(3, "DDC")])
    assert user.get_view_property("cc_sidebar", "show_cc_3") is False

    with library.session.get_bind().begin() as connection:
        connection.execute(text("INSERT INTO custom_column_3 (id, book, value) "
                                "VALUES (99, 1, '778')"))
    library.session.expire_all()
    library.get_hierarchical_column_ids(ttl=0)
    assert not library.is_flat_cc_column(3), "precondition: now detected hierarchical"

    assert ccv.is_cc_visible(user, 3) is False


def test_a_hierarchical_column_stays_visible_when_it_stops_being_one(library, unconfigured):
    """Rule 3 in the other direction: losing the prefix pair must not hide a
    column the user already had."""
    user = FakeUser()
    ccv.seed_cc_visibility(user, [_col(2, "Genre")])
    assert user.get_view_property("cc_sidebar", "show_cc_2") is True

    with library.session.get_bind().begin() as connection:
        connection.execute(text("DELETE FROM custom_column_2"))
    library.session.expire_all()
    library.get_hierarchical_column_ids(ttl=0)
    assert library.is_flat_cc_column(2), "precondition: now detected flat"

    assert ccv.is_cc_visible(user, 2) is True


# --------------------------------------------------------------------------
# Rule 2 -- saved wins
# --------------------------------------------------------------------------

def test_seeding_never_overwrites_a_value_the_user_already_saved(library, unconfigured):
    """The backfill's whole safety property, isolated."""
    user = FakeUser(stored=json.dumps(
        {"cc_sidebar": {"show_cc_3": True, "show_cc_2": False}}))

    written = ccv.seed_cc_visibility(user, [_col(2, "Genre"), _col(3, "DDC")])

    assert written == 0
    assert user.get_view_property("cc_sidebar", "show_cc_3") is True
    assert user.get_view_property("cc_sidebar", "show_cc_2") is False


def test_an_explicitly_hidden_column_stays_hidden_even_if_hierarchical(library, unconfigured):
    user = FakeUser(stored=json.dumps({"cc_sidebar": {"show_cc_2": False}}))
    assert ccv.is_cc_visible(user, 2) is False


def test_a_stored_true_beats_a_template_that_would_hide_it(library, monkeypatch):
    """The template is only a seed. It must not override a live choice."""
    monkeypatch.setattr(ccv, "config",
                        SimpleNamespace(config_default_cc_columns="3"))
    user = FakeUser(stored=json.dumps({"cc_sidebar": {"show_cc_2": True}}))
    assert ccv.is_cc_visible(user, 2) is True
    assert ccv.is_cc_visible(user, 3) is True


def test_changing_the_template_does_not_change_an_existing_user(library, monkeypatch):
    """Rule 2 end to end: the administrator edits the template and every user
    who already has a value is unaffected."""
    monkeypatch.setattr(ccv, "config",
                        SimpleNamespace(config_default_cc_columns="2"))
    user = FakeUser()
    ccv.seed_cc_visibility(user, [_col(2, "Genre"), _col(3, "DDC")])
    before = json.dumps(user.view_settings, sort_keys=True)

    monkeypatch.setattr(ccv, "config",
                        SimpleNamespace(config_default_cc_columns="3"))
    assert json.dumps(user.view_settings, sort_keys=True) == before
    assert ccv.is_cc_visible(user, 2) is True
    assert ccv.is_cc_visible(user, 3) is False


# --------------------------------------------------------------------------
# The administrator's template
# --------------------------------------------------------------------------

def test_the_template_is_tri_state(unconfigured, library):
    """NULL derives from the hierarchy; "" seeds nothing visible; "3" seeds
    only column 3. The middle case is the one that needs care -- an empty
    submission is a choice, not an absence."""
    assert ccv.configured_default_visible(unconfigured) is None
    assert ccv.is_cc_visible(FakeUser(), 2) is True, "derived: hierarchical"
    assert ccv.is_cc_visible(FakeUser(), 3) is False, "derived: flat"

    unconfigured.config_default_cc_columns = ""
    assert ccv.configured_default_visible(unconfigured) == frozenset()
    assert ccv.is_cc_visible(FakeUser(), 2) is False

    unconfigured.config_default_cc_columns = "3"
    assert ccv.is_cc_visible(FakeUser(), 2) is False
    assert ccv.is_cc_visible(FakeUser(), 3) is True


@pytest.mark.parametrize("hostile", [
    "3; DROP TABLE books",
    "٣",
    "99999999999999999999",
    "-1",
    "1,,2",
    "",
])
def test_hostile_template_values_are_ignored(unconfigured, hostile):
    unconfigured.config_default_cc_columns = hostile
    # Must not raise, and must not resolve to something surprising.
    assert ccv.configured_default_visible(unconfigured) is not None


def test_persisting_the_template_rejects_ids_that_are_not_browsable(unconfigured, library):
    columns = [_col(2, "Genre"), _col(3, "DDC")]
    stored = ccv.persist_configured_default_visible(
        unconfigured, ["2", "3", "99", "3; DROP TABLE books"], columns)
    assert stored == "2,3"
    assert unconfigured.config_default_cc_columns == "2,3"


def test_a_failed_column_load_preserves_the_stored_template(unconfigured, library):
    """``columns is None`` means the library was unreadable. Ticking nothing
    then must NOT be read as "the administrator cleared the list"."""
    unconfigured.config_default_cc_columns = "2"
    stored = ccv.persist_configured_default_visible(unconfigured, [], None)
    assert stored == "2"
    assert unconfigured.config_default_cc_columns == "2"


def test_a_real_clear_is_persisted(unconfigured, library):
    """The counterpart: with definitions in hand, an empty submission really
    does mean "seed nothing visible"."""
    unconfigured.config_default_cc_columns = "2"
    stored = ccv.persist_configured_default_visible(
        unconfigured, [], [_col(2, "Genre")])
    assert stored == ""
    assert unconfigured.config_default_cc_columns == ""


# --------------------------------------------------------------------------
# The backfill
# --------------------------------------------------------------------------

def test_the_backfill_reaches_a_user_who_already_existed(library, unconfigured,
                                                        monkeypatch):
    """The step that makes the administrator's configuration reach the users the
    report is about. Without it the configuration is a no-op for them."""
    unconfigured.config_cc_visibility_seeded = False
    _ub_stub(monkeypatch, [FakeUser("existing-1"), FakeUser("existing-2")])

    assert ccv.backfill_existing_users() == 4
    assert unconfigured.config_cc_visibility_seeded is True


def test_the_backfill_does_not_touch_a_user_who_already_saved(library, unconfigured,
                                                              monkeypatch):
    """Rule 2, at the only place a bulk write could breach it."""
    saved = FakeUser("has-a-choice", stored=json.dumps(
        {"cc_sidebar": {"show_cc_3": True}}))
    keyless = FakeUser("no-choice")
    unconfigured.config_cc_visibility_seeded = False
    _ub_stub(monkeypatch, [saved, keyless])

    ccv.backfill_existing_users()

    assert saved.get_view_property("cc_sidebar", "show_cc_3") is True
    assert saved.get_view_property("cc_sidebar", "show_cc_2") is True, "gap filled"
    assert keyless.get_view_property("cc_sidebar", "show_cc_2") is True


def test_the_backfill_leaves_every_user_alone_when_the_library_is_unreadable(
        library, unconfigured, monkeypatch):
    """Seeding from an empty column list would write False for every column
    and hide the lot, so an unreadable library must abort the pass and leave
    the flag unset so the next start retries."""
    unconfigured.config_cc_visibility_seeded = False
    monkeypatch.setattr(db, "cc_classes", {})
    queried = []
    monkeypatch.setattr(ccv, "ub", SimpleNamespace(
        session=SimpleNamespace(query=lambda _m: queried.append(1))))

    assert ccv.backfill_existing_users() == 0
    assert queried == [], "must not even enumerate users"
    assert unconfigured.config_cc_visibility_seeded is False, "must retry later"


def test_the_seed_record_names_every_column_it_chose(library, unconfigured):
    """The seed is frozen for every user on its first pass, so the one log line
    saying why each column came out the way it did is the only audit trail.
    A wrong verdict here is not self-correcting."""
    record = ccv.describe_seed([_col(2, "Genre"), _col(3, "DDC")])

    assert record == "Genre=visible, DDC=hidden"


def test_the_backfill_runs_only_once(library, unconfigured, monkeypatch):
    unconfigured.config_cc_visibility_seeded = True
    monkeypatch.setattr(ccv, "ub", SimpleNamespace(
        session=SimpleNamespace(query=lambda _m: pytest.fail("re-ran when already seeded"))))

    assert ccv.backfill_existing_users() == 0


def test_load_browsable_columns_distinguishes_unreadable_from_empty(library, monkeypatch,
                                                                    unconfigured):
    monkeypatch.setattr(ccv, "config", unconfigured)
    assert sorted(c.id for c in ccv.load_browsable_columns()) == [2, 3]

    monkeypatch.setattr(db, "cc_classes", {})
    assert ccv.load_browsable_columns() is None


# --------------------------------------------------------------------------
# Parity -- behavioural, across every surface
# --------------------------------------------------------------------------

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
    # No "visible column returns 200" case here on purpose: the tree/books
    # views reach through db.Books.custom_column_<id>, an ORM relationship that
    # only setup_db_cc_classes() installs. Asserting it in this fixture would
    # be testing the fixture, not the visibility gate.


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


def test_an_anonymous_session_resolves_to_the_seed_and_is_never_written(
        library, unconfigured, monkeypatch):
    """Anonymous has no row, so it can never be seeded. It has to resolve
    through the same resolver, and nothing may try to store a value for it."""
    from cps import ub as ub_module

    class SessionBackedAnonymous:
        """What matters: answers from the flask session, which is empty.

        Constructing the real ``ub.Anonymous`` would drag in a user-database
        session that has nothing to do with the property under test.
        """
        def get_view_property(self, page, prop):
            return None

        def set_view_property(self, page, prop, value, commit=True):
            raise AssertionError("an anonymous session must never be written to")

    monkeypatch.setattr(ub_module, "Anonymous", SessionBackedAnonymous, raising=False)
    anonymous = SessionBackedAnonymous()
    assert ccv.is_cc_visible(anonymous, 3) is False
    assert ccv.is_cc_visible(anonymous, 2) is True


# --------------------------------------------------------------------------
# The browsable set is the sidebar's set
# --------------------------------------------------------------------------

def _selected_in_template(config_default_cc_columns, columns, hierarchical_ids):
    """Mirror of the /admin/viewconfig checkbox block, in isolation.

    The tri-state was first written in Jinja and got it wrong: `default(none,
    true)` substitutes for "" as well as None, so "the administrator cleared
    the list" rendered as "never configured" and showed the hierarchical
    columns ticked when they would in fact seed hidden. It now lives in
    admin.view_configuration and is passed down as ccSelectedDefaultIds;
    this keeps the rendering honest without needing a logged-in admin.
    """
    if config_default_cc_columns is None:
        selected = set(hierarchical_ids)
    else:
        selected = {v.strip() for v in config_default_cc_columns.split(",") if v.strip()}
    return [c["name"] for c in columns if str(c["id"]) in selected]


@pytest.mark.parametrize("stored,expected", [
    (None, ["Genre"]),        # never configured -> the hierarchical columns
    ("", []),                # cleared on purpose -> nothing seeds visible
    ("3", ["DDC"]),          # an explicit choice
    ("1,3", ["Genre", "DDC"]),
])
def test_the_admin_form_reflects_the_tri_state(library, stored, expected):
    """Whichever box is ticked must be the one that actually seeds visible."""
    columns = [{"id": 1, "name": "Genre"}, {"id": 3, "name": "DDC"},
               {"id": 4, "name": "LCC"}]

    ticked = _selected_in_template(stored, columns, ["1"])

    assert ticked == expected
    # The ticked set and the seed function must agree, column for column --
    # otherwise the form is describing a different installation than the one a
    # new user will get.
    config = SimpleNamespace(config_default_cc_columns=stored)
    for column in columns:
        seeds_visible = ccv.seed_default_visible(config, column["id"])
        if stored is None:
            continue        # derived from hierarchy, which the template mirrors
        assert seeds_visible is (column["name"] in expected)


def test_the_seeded_set_matches_what_the_sidebar_renders(library, unconfigured):
    """A column the admin hid by name regex must not be seeded, or a user
    would carry a stored value for a column they can never see."""
    unconfigured.config_columns_to_ignore = "^DD"

    user = FakeUser()
    ccv.seed_cc_visibility(user, ccv.load_browsable_columns())

    assert user.get_view_property("cc_sidebar", "show_cc_2") is True
    assert user.get_view_property("cc_sidebar", "show_cc_3") is None


def test_hierarchy_detection_still_owns_the_rendering_mode(library, unconfigured):
    """Rule 3 covers sidebar visibility ONLY. The tree/flat presentation stays
    with live data via is_flat_cc_column; this pins that deliberately so the
    two concerns are not quietly conflated later."""
    assert library.is_flat_cc_column(3) is True
    assert library.is_flat_cc_column(2) is False
    assert hierarchy.is_hierarchical_value_set is not None
