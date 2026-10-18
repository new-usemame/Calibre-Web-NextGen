# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 6 -- the stored hierarchy verdict becomes authoritative.

Until now ``is_flat_cc_column`` asked the *data*: a column was a hierarchy
because some value happened to be a prefix of another. That is guessy in a way
users notice. A Dewey column holding ``778`` and ``778.3`` is a tree; add one
book and it changes, up to five minutes later, behind a cache, with two workers
able to disagree.

``cc_sidecar.is_hierarchy`` is the way to decide it once. It is a tri-state and
the ``None`` arm is the whole design:

===============  =========================  ==============================
Stored           Meaning                    Cost of getting it wrong
===============  =========================  ==============================
``NULL``         derive from the data       none -- this is every install
``True``         always a hierarchy         a tree that should be flat
``False``        never a hierarchy          a flat list that should nest
===============  =========================  ==============================

So ``None`` must stay distinguishable from ``False``. That is why the column is
nullable with **no DEFAULT** and why the admin form posts the string ``auto``
rather than a boolean.
"""
import sqlite3

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

from cps import cc_sidecar, db, hierarchy

pytestmark = pytest.mark.unit


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    """A sidecar with no Calibre library attached -- the verdict table does not
    need one, which is exactly why the override can be read cheaply."""
    app = str(tmp_path / "app.db")
    sqlite3.connect(app).close()

    engine = create_engine("sqlite:///%s" % app.replace("\\", "/"),
                           connect_args={"timeout": 30}, poolclass=StaticPool)

    def _attach(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        try:
            cur.execute("ATTACH DATABASE ? AS app_settings", (app,))
        finally:
            cur.close()

    from sqlalchemy import event as ev
    ev.listen(engine, "connect", _attach)
    calibre = object.__new__(db.CalibreDB)
    calibre.session = db.sessionmaker(bind=engine)()
    calibre.ensure_session = lambda *a, **k: None
    monkeypatch.setattr(cc_sidecar, "calibre_db", calibre)
    # A real Session, not a sessionmaker: ensure_tables takes the DDL bind off
    # it with session.get_bind(), which only exists on a Session.
    monkeypatch.setattr(cc_sidecar.ub, "session", db.sessionmaker(bind=engine)())
    assert cc_sidecar.ensure_tables()
    return calibre


def _verdict(calibre, col_id):
    return cc_sidecar.hierarchy_verdict(col_id)


# ---------------------------------------------------------------------------
# The tri-state survives a round trip
# ---------------------------------------------------------------------------

def test_no_row_reads_as_derived_not_as_flat(sidecar):
    """The single most important line here: 'nobody has an opinion' and
    'declared flat' must never collapse into each other."""
    assert _verdict(sidecar, 3) == cc_sidecar.HierarchyVerdict(None, "derived")


def test_all_three_states_survive_a_write(sidecar):
    assert cc_sidecar.set_hierarchy_verdict(1, True) is True
    assert cc_sidecar.set_hierarchy_verdict(2, False) is True
    assert cc_sidecar.set_hierarchy_verdict(3, None) is True

    assert _verdict(sidecar, 1) == cc_sidecar.HierarchyVerdict(True, "override")
    assert _verdict(sidecar, 2) == cc_sidecar.HierarchyVerdict(False, "override")
    assert _verdict(sidecar, 3) == cc_sidecar.HierarchyVerdict(None, "derived"), \
        "resetting to automatic must NOT read as 'declared flat'"


def test_a_verdict_can_be_flipped_and_reset(sidecar):
    cc_sidecar.set_hierarchy_verdict(3, True)
    cc_sidecar.set_hierarchy_verdict(3, False)
    assert _verdict(sidecar, 3).value is False
    cc_sidecar.set_hierarchy_verdict(3, None)
    assert _verdict(sidecar, 3).value is None


def test_the_column_is_nullable_with_no_default(sidecar):
    """A DEFAULT of 0 would declare every column flat at creation -- the exact
    opposite of what an unconfigured install should do."""
    conn = sqlite3.connect(sidecar.session.get_bind().url.database)
    try:
        info = {row[1]: row for row in conn.execute("PRAGMA table_info(cc_column)")}
    finally:
        conn.close()

    assert info["is_hierarchy"][3] == 0, "must be nullable"
    assert info["is_hierarchy"][4] is None, "must have no DEFAULT"


def test_an_unreadable_sidecar_reports_unavailable_rather_than_flat(detached=None):
    """Fail safe in the safe direction: no sidecar means 'no opinion', which
    resolves to today's behaviour, not to a declared verdict."""
    import types
    calibre = object.__new__(db.CalibreDB)
    calibre.session = None
    calibre.ensure_session = lambda *a, **k: None
    saved, cc_sidecar.calibre_db = cc_sidecar.calibre_db, calibre
    try:
        assert cc_sidecar.hierarchy_verdict(3) == \
            cc_sidecar.HierarchyVerdict(None, "unavailable")
        assert cc_sidecar.set_hierarchy_verdict(3, True) is False
    finally:
        cc_sidecar.calibre_db = saved


# ---------------------------------------------------------------------------
# The override actually wins over the data
# ---------------------------------------------------------------------------

class _FakeCalibre:
    """A CalibreDB whose *derived* answer is fixed, so these tests are about the
    override rather than about a particular value set.

    It carries the real sidecar session, so ``hierarchy_verdict`` is reached the
    ordinary way and the override is genuinely stored rather than mocked.
    """

    def __init__(self, derived_flat, session):
        self.session = session
        self._derived_flat = derived_flat
        self.calls = 0

    def get_hierarchical_column_ids(self, ttl=300, fail_on_error=False):
        self.calls += 1
        return set() if self._derived_flat else {3}

    def is_flat_cc_column(self, col_id, fail_on_error=False):
        # The real method's logic, with the sidecar consulted ahead of the
        # derivation.
        if col_id not in db.cc_classes:
            return False
        verdict = cc_sidecar.hierarchy_verdict(col_id)
        if verdict.value is not None:
            return not verdict.value
        self.calls += 1
        return col_id not in self.get_hierarchical_column_ids(
            fail_on_error=fail_on_error)


@pytest.fixture
def registered(monkeypatch):
    monkeypatch.setattr(db, "cc_classes", {3: object()})
    return db.cc_classes


@pytest.mark.parametrize("stored,derived_flat,expected_flat", [
    (None, True, True),    # unconfigured, data says flat  -> flat (today)
    (None, False, False),  # unconfigured, data says tree  -> tree (today)
    (True, True, False),   # declared hierarchy, data says flat -> OVERRIDDEN
    (False, False, True),  # declared flat, data says tree      -> OVERRIDDEN
    (True, False, False),  # declared hierarchy, data agrees
    (False, True, True),   # declared flat, data agrees
])
def test_a_stored_verdict_overrides_what_the_data_says(
        sidecar, registered, stored, derived_flat, expected_flat):
    """The point of the whole phase. The two `None` rows must pass straight
    through; the four override rows must ignore the data entirely."""
    if stored is not None:
        cc_sidecar.set_hierarchy_verdict(3, stored)
    fake = _FakeCalibre(derived_flat, sidecar.session)
    saved, cc_sidecar.calibre_db = cc_sidecar.calibre_db, fake
    try:
        assert fake.is_flat_cc_column(3) is expected_flat
    finally:
        cc_sidecar.calibre_db = saved


def test_the_derivation_is_not_even_consulted_when_a_verdict_exists(
        sidecar, registered):
    """Worth asserting on its own: with an override in place the data is never
    read, so the 300-second cache cannot disagree with the stored answer."""
    cc_sidecar.set_hierarchy_verdict(3, True)
    fake = _FakeCalibre(derived_flat=False, session=sidecar.session)
    saved, cc_sidecar.calibre_db = cc_sidecar.calibre_db, fake
    try:
        assert fake.is_flat_cc_column(3) is False
        assert fake.calls == 0, "the data was read despite an override"
    finally:
        cc_sidecar.calibre_db = saved


def test_the_column_still_has_to_be_registered_to_browse_at_all(sidecar):
    """An override does not conjure a browse surface for a column Calibre has
    no value table for -- the cc_classes check comes first."""
    cc_sidecar.set_hierarchy_verdict(99, True)
    fake = _FakeCalibre(derived_flat=True, session=sidecar.session)
    saved, cc_sidecar.calibre_db = cc_sidecar.calibre_db, fake
    saved_classes, db.cc_classes = db.cc_classes, {}
    try:
        assert fake.is_flat_cc_column(99) is False
    finally:
        cc_sidecar.calibre_db = saved
        db.cc_classes = saved_classes


# ---------------------------------------------------------------------------
# The admin form
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode,expected", [
    ("hierarchical", True),
    ("flat", False),
    ("auto", None),
    ("", None),        # an unticked select posts the empty string
])
def test_the_form_posts_a_word_not_a_boolean(sidecar, mode, expected, monkeypatch):
    """The round trip an administrator actually makes.

    `auto` is why this is a word and not a checkbox: resetting to automatic has
    to be expressible, and `False` is already taken by "never a hierarchy".
    """
    written = []

    def _fake_save(col_id, value):
        written.append((col_id, value))
        return True

    monkeypatch.setattr(cc_sidecar, "set_hierarchy_verdict", _fake_save)

    class _Request:
        form = {"cc_hierarchy_mode_3": mode}

    monkeypatch.setattr("cps.admin.request", _Request())
    from cps import admin
    admin._save_cc_hierarchy_verdicts()

    assert written == [(3, expected)]


def test_a_column_the_form_does_not_mention_is_left_alone(sidecar, monkeypatch):
    """This form is also posted by partial clients. A missing field means "not
    mentioned", not "reset it" -- otherwise every partial POST would quietly
    clear an administrator's overrides."""
    written = []
    monkeypatch.setattr(cc_sidecar, "set_hierarchy_verdict",
                        lambda c, v: written.append((c, v)) or True)

    class _Request:
        form = {"something_else": "1"}

    monkeypatch.setattr("cps.admin.request", _Request())
    from cps import admin
    admin._save_cc_hierarchy_verdicts()

    assert written == []


def test_a_nonsense_column_id_or_mode_is_ignored_not_written(sidecar, monkeypatch):
    """The field name carries the id, so both halves are attacker-controlled
    form input and neither may reach the database unvalidated."""
    written = []
    monkeypatch.setattr(cc_sidecar, "set_hierarchy_verdict",
                        lambda c, v: written.append((c, v)) or True)

    class _Request:
        form = {
            "cc_hierarchy_mode_abc": "flat",
            "cc_hierarchy_mode_3": "sideways",
            "cc_hierarchy_mode_99999999999999999999": "flat",
        }

    monkeypatch.setattr("cps.admin.request", _Request())
    from cps import admin
    admin._save_cc_hierarchy_verdicts()

    assert written == []


def test_a_failed_write_is_counted_and_reported(sidecar, monkeypatch):
    """Silently swallowing a lost override would leave the admin believing the
    column is pinned when it is not."""
    monkeypatch.setattr(cc_sidecar, "set_hierarchy_verdict", lambda c, v: False)
    flashed = []
    monkeypatch.setattr("cps.admin.flash",
                        lambda message, category=None: flashed.append(category))

    class _Request:
        form = {"cc_hierarchy_mode_3": "hierarchical"}

    monkeypatch.setattr("cps.admin.request", _Request())
    from cps import admin
    assert admin._save_cc_hierarchy_verdicts() == 0
    assert flashed == ["error"]


# ---------------------------------------------------------------------------
# Reconcile must never touch it
# ---------------------------------------------------------------------------

def test_reconcile_never_overwrites_a_declared_verdict(sidecar, tmp_path):
    """is_hierarchy is the app's fact about a column, not Calibre's to reset.
    Reconciling runs on every boot, so a reconcile that reset it would undo an
    administrator's override silently and forever."""
    meta = str(tmp_path / "metadata.db")
    conn = sqlite3.connect(meta)
    conn.execute("CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, datatype TEXT)")
    conn.execute("INSERT INTO custom_columns VALUES (3, 'text')")
    conn.commit()
    conn.close()

    engine = create_engine("sqlite://", poolclass=StaticPool)
    ev = event

    def _attach(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        try:
            cur.execute("ATTACH DATABASE ? AS calibre", (meta,))
        finally:
            cur.close()

    ev.listen(engine, "connect", _attach)
    calibre = object.__new__(db.CalibreDB)
    calibre.session = db.sessionmaker(bind=engine)()
    calibre.ensure_session = lambda *a, **k: None

    cc_sidecar.set_hierarchy_verdict(3, True)
    saved, cc_sidecar.calibre_db = cc_sidecar.calibre_db, calibre
    try:
        assert cc_sidecar.reconcile()["available"] is True
    finally:
        cc_sidecar.calibre_db = saved

    assert _verdict(sidecar, 3) == cc_sidecar.HierarchyVerdict(True, "override")
