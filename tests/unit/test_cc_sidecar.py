# SPDX-License-Identifier: GPL-3.0-or-later
"""The custom-column sidecar in app.db.

Phase 1 is deliberately inert. It creates the tables and reconciles them against
the library, and nothing reads a stored hierarchy verdict yet, so an existing
install must see **no behaviour change at all**. Most of what follows is that
property, asserted.

The tests use a real two-file SQLite pair wired exactly the way
``cps/db.py`` wires the real thing -- an in-memory engine with ``metadata.db``
attached as ``calibre`` and ``app.db`` as ``app_settings`` -- because a stubbed
session cannot show whether the cross-file join or the shared transaction
actually works. Those two are the premise the whole design rests on, so they get
their own tests at the top rather than being assumed.
"""
import os
import sqlite3

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

from cps import cc_sidecar, db, hierarchy

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sidecar_library(tmp_path, monkeypatch):
    """A real two-file library, attached the way CalibreDB attaches it.

    ``metadata.db`` carries the column definitions and value tables; ``app.db``
    is where the sidecar lands. Returns a handle with both file paths.

    The sidecar's DDL goes through ``ub.engine`` (SQLite cannot create an index
    on a table in an *attached* database), so ``ub`` is pointed at the same
    ``app.db`` file that ``CalibreDB`` has attached as ``app_settings`` -- which
    is exactly the arrangement the real app has.
    """
    meta = str(tmp_path / "metadata.db")
    app = str(tmp_path / "app.db")

    conn = sqlite3.connect(meta)
    conn.executescript(
        """
        CREATE TABLE custom_columns (
            id INTEGER PRIMARY KEY, label TEXT, name TEXT, datatype TEXT,
            is_multiple INTEGER DEFAULT 1, display TEXT DEFAULT '{}');
        CREATE TABLE custom_column_3 (
            id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL COLLATE NOCASE,
            link TEXT NOT NULL DEFAULT '', UNIQUE(value));
        INSERT INTO custom_columns (id,label,name,datatype) VALUES
            (1,'g','genre','text'), (3,'d','ddc','text');
        INSERT INTO custom_column_3 (id,value) VALUES
            (1,'778.3'), (2,'778.72'), (3,'775');
        """
    )
    conn.commit()
    conn.close()
    sqlite3.connect(app).close()

    def _attach(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        try:
            cur.execute("ATTACH DATABASE ? AS calibre", (meta,))
            cur.execute("ATTACH DATABASE ? AS app_settings", (app,))
        finally:
            cur.close()

    engine = create_engine("sqlite://", isolation_level="SERIALIZABLE",
                           connect_args={"check_same_thread": False, "timeout": 30},
                           poolclass=StaticPool)
    event.listen(engine, "connect", _attach)

    calibre = object.__new__(db.CalibreDB)
    calibre.session = db.sessionmaker(bind=engine)()
    calibre.ensure_session = lambda *a, **k: None
    monkeypatch.setattr(cc_sidecar, "calibre_db", calibre)
    monkeypatch.setattr(cc_sidecar, "db", db)
    # DDL owner: a session on the same app.db the sidecar is attached as.
    # ``ub.engine`` is a local inside ub.init_db, so the module takes the bind
    # off the session instead -- patch the session.
    monkeypatch.setattr(cc_sidecar.ub, "session", db.sessionmaker(bind=create_engine(
        "sqlite:///%s" % app.replace("\\", "/"), connect_args={"timeout": 30},
        poolclass=StaticPool))())
    # reconcile() walks a column's values through db.cc_classes to work out
    # which paths the library can produce. Only __tablename__ is used, so a
    # stand-in is enough -- and it keeps this fixture out of the relationship
    # installation dance the bookless tests have to do.
    monkeypatch.setattr(cc_sidecar.db, "cc_classes",
                        {3: type("Cc3", (), {"__tablename__": "custom_column_3"})})
    return type("Library", (), {
        "meta": meta, "app": app, "engine": engine, "calibre": calibre})()


@pytest.fixture
def detached(monkeypatch):
    """A session with no app_settings attachment, as many unit tests have."""
    calibre = object.__new__(db.CalibreDB)
    calibre.session = db.sessionmaker(bind=create_engine("sqlite://",
                                                        poolclass=StaticPool))()
    calibre.ensure_session = lambda *a, **k: None
    monkeypatch.setattr(cc_sidecar, "calibre_db", calibre)
    return calibre


def _cc_index_query(session, column_id=3):
    """Read a column's distinct values straight out of the Calibre file."""
    return session.execute(
        text('SELECT DISTINCT "value" FROM calibre.custom_column_%d' % column_id)
    ).fetchall()


def _app_db_info(app_path, table):
    """Column metadata for a table in app.db.

    ``PRAGMA table_info`` will not take a schema-qualified name either, so the
    app.db file is opened directly. It is the app's own schema, so asking it
    directly is the honest way to ask.
    """
    conn = sqlite3.connect(app_path)
    try:
        return conn.execute("PRAGMA table_info(%s)" % table).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The premise: cross-file reach and one transaction
# ---------------------------------------------------------------------------

def test_the_sidecar_is_reachable_through_the_calibre_session(sidecar_library):
    """app.db must be visible from the session that also sees metadata.db.

    The whole design rests on this. If it did not hold, the sidecar would need
    its own session and the shared-transaction property would be gone.
    """
    session = sidecar_library.calibre.session

    names = {row[1] for row in session.execute(text("PRAGMA database_list"))}
    assert {"calibre", "app_settings"} <= names
    assert cc_sidecar.sidecar_ready() is True


def test_a_sidecar_write_joins_calibre_data_in_one_query(sidecar_library):
    """A column's app-side facts and its Calibre metadata read together."""
    assert cc_sidecar.ensure_tables() is True
    session = sidecar_library.calibre.session
    session.execute(text("INSERT INTO %s (id) VALUES (3)" % cc_sidecar._table("cc_column")))
    session.commit()

    rows = session.execute(text(
        "SELECT cc.id, cc.is_hierarchy, calibre.custom_columns.name "
        "FROM %s cc JOIN calibre.custom_columns ON calibre.custom_columns.id = cc.id "
        "ORDER BY cc.id" % cc_sidecar._table("cc_column"))).fetchall()

    assert [tuple(r) for r in rows] == [(3, None, "ddc")]


def test_a_sidecar_write_rolls_back_with_the_calibre_write_it_belongs_to(sidecar_library):
    """The property that makes rename-carry-over safe.

    A sidecar write and a Calibre write share one transaction, so a failure
    cannot leave a value renamed but its curated position dropped. Before this
    was true, that failure was silent: the rename succeeded, the order row did
    not move, and nothing reported an error.
    """
    assert cc_sidecar.ensure_tables() is True
    session = sidecar_library.calibre.session
    session.execute(text(
        "INSERT INTO %s (column_id, path, parent_path, view_order) "
        "VALUES (3, '778.3', '778', 0)" % cc_sidecar._table("cc_value_order")))
    session.commit()

    with pytest.raises(RuntimeError):
        with sidecar_library.engine.begin() as conn:
            conn.execute(text("UPDATE calibre.custom_column_3 SET value = '778.4' "
                              "WHERE value = '778.3'"))
            conn.execute(text(
                "DELETE FROM %s WHERE path = '778.3'"
                % cc_sidecar._table("cc_value_order")))
            raise RuntimeError("simulated failure mid-write")

    assert [r[0] for r in _cc_index_query(session)] == ["775", "778.3", "778.72"]
    assert cc_sidecar.stored_order(3) == {"778.3": 0}


# ---------------------------------------------------------------------------
# Degradation: a missing sidecar must never be an error
# ---------------------------------------------------------------------------

def test_everything_is_a_no_op_without_the_attachment(detached):
    """Many unit tests run on a session with no app_settings. Alphabetical order
    is the pre-feature behaviour, so 'no sidecar' must mean 'no sidecar' and not
    an exception."""
    assert cc_sidecar.sidecar_ready() is False
    assert cc_sidecar.ensure_tables() is False
    assert cc_sidecar.stored_order(3) == {}
    assert cc_sidecar.sibling_order(3, "") == {}
    assert cc_sidecar.hierarchy_verdict(3).source == "unavailable"
    assert cc_sidecar.reconcile() == {"columns_added": 0, "columns_removed": 0,
                                      "orders_pruned": 0, "available": False}


def test_no_sidecar_leaves_the_stored_order_empty_so_sorting_stays_alphabetical(detached):
    """The fallback property, stated as a test: with nothing stored, the caller
    receives an empty map and therefore sorts exactly as it does today."""
    order = cc_sidecar.stored_order(3)

    assert order == {}
    names = ["775", "778.3", "778.72"]
    assert sorted(names, key=lambda n: n.lower()) == names


# ---------------------------------------------------------------------------
# ensure_tables
# ---------------------------------------------------------------------------

def test_ensure_tables_is_idempotent(sidecar_library):
    """Startup calls it on every boot; a second call must not fail or reset."""
    assert cc_sidecar.ensure_tables() is True
    session = sidecar_library.calibre.session
    session.execute(text("INSERT INTO %s (id) VALUES (1)" % cc_sidecar._table("cc_column")))
    session.commit()

    assert cc_sidecar.ensure_tables() is True
    rows = session.execute(text("SELECT id FROM %s ORDER BY id"
                                % cc_sidecar._table("cc_column"))).fetchall()
    assert [r[0] for r in rows] == [1], "re-running must not wipe stored rows"


def test_the_columns_carry_the_tri_state_and_not_a_default(sidecar_library):
    """is_hierarchy NULL means 'still derived'. A DEFAULT of 0 would silently
    declare every column flat on creation, which is the opposite of the intent.
    """
    cc_sidecar.ensure_tables()

    cc_info = {row[1]: row for row in _app_db_info(sidecar_library.app, "cc_column")}
    assert cc_info["is_hierarchy"][3] == 0, "must be nullable (NOTNULL flag clear)"
    assert cc_info["is_hierarchy"][4] is None, "must have no DEFAULT"

    order_info = {row[1]: row for row in
                  _app_db_info(sidecar_library.app, "cc_value_order")}
    assert order_info["view_order"][3] == 1, "view_order must be NOT NULL"
    # Composite primary key: (column_id, path), so both are pk but at their own
    # ordinal within it.
    assert order_info["column_id"][5] == 1, "column_id is the first PK column"
    assert order_info["path"][5] == 2, "path is the second PK column"


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------

def test_reconcile_registers_every_calibre_column(sidecar_library):
    """Including composite/series columns the browse filters would hide, so
    un-ignoring one later needs no migration."""
    assert cc_sidecar.ensure_tables() is True

    summary = cc_sidecar.reconcile()

    assert summary["available"] is True
    assert summary["columns_added"] == 2
    rows = sidecar_library.calibre.session.execute(
        text("SELECT id, is_hierarchy FROM %s ORDER BY id"
             % cc_sidecar._table("cc_column"))).fetchall()
    assert [tuple(r) for r in rows] == [(1, None), (3, None)]


def test_reconcile_never_overwrites_a_stored_hierarchy_verdict(sidecar_library):
    """is_hierarchy is the app's fact about a column, not Calibre's. A reconcile
    that reset it would silently undo an administrator's override on every boot.
    """
    cc_sidecar.ensure_tables()
    cc_sidecar.reconcile()          # creates the rows a verdict can sit on
    session = sidecar_library.calibre.session
    session.execute(text("UPDATE %s SET is_hierarchy = 1 WHERE id = 3"
                         % cc_sidecar._table("cc_column")))
    session.commit()
    assert cc_sidecar.hierarchy_verdict(3).value is True, "precondition"

    cc_sidecar.reconcile()

    assert cc_sidecar.hierarchy_verdict(3) == cc_sidecar.HierarchyVerdict(True, "override")


def test_reconcile_drops_a_column_calibre_no_longer_has(sidecar_library):
    """Calibre desktop can delete a column. Its rows must not linger, or
    re-adding a column later would inherit a stale position."""
    cc_sidecar.ensure_tables()
    session = sidecar_library.calibre.session
    session.execute(text("INSERT INTO %s (id) VALUES (99)"
                         % cc_sidecar._table("cc_column")))
    session.execute(text("INSERT INTO %s (column_id, path, parent_path, view_order) "
                         "VALUES (99, 'X', '', 0)" % cc_sidecar._table("cc_value_order")))
    session.commit()

    summary = cc_sidecar.reconcile()

    assert summary["columns_removed"] == 1
    assert cc_sidecar.stored_order(99) == {}


def test_reconcile_prunes_an_order_row_for_a_value_calibre_deleted(sidecar_library):
    """The step that is easy to skip and expensive to skip.

    Without it, deleting a value in Calibre desktop and recreating it with the
    same name silently restores its old curated position -- the app appears to
    remember something the user never asked it to.
    """
    cc_sidecar.ensure_tables()
    session = sidecar_library.calibre.session
    for path, order in (("775", 0), ("778.3", 1), ("778", 2), ("GHOST", 3)):
        session.execute(text(
            "INSERT INTO %s (column_id, path, parent_path, view_order) "
            "VALUES (3, :p, '', :o)" % cc_sidecar._table("cc_value_order")),
            {"p": path, "o": order})
    session.commit()

    summary = cc_sidecar.reconcile()

    assert summary["orders_pruned"] == 1
    remaining = cc_sidecar.stored_order(3)
    assert "GHOST" not in remaining
    # '778' survives because a value '778.3' implies it, even though no row
    # stores exactly that.
    assert set(remaining) == {"775", "778.3", "778"}


def test_reconcile_costs_nothing_when_nothing_has_been_reordered(sidecar_library):
    """Only columns that actually have order rows get their values scanned, so a
    fresh install reconciles without walking the library."""
    cc_sidecar.ensure_tables()
    cc_sidecar.reconcile()

    assert cc_sidecar._ordered_column_ids() == set()


def test_reconcile_leaves_order_alone_when_the_value_table_cannot_be_read(
        sidecar_library, monkeypatch):
    """Pruning on a failed read would discard a user's curation because of our
    problem, not theirs. No live paths means 'we do not know', not 'none'.
    """
    cc_sidecar.ensure_tables()
    session = sidecar_library.calibre.session
    session.execute(text("INSERT INTO %s (column_id, path, parent_path, view_order) "
                         "VALUES (3, '778.3', '', 0)"
                         % cc_sidecar._table("cc_value_order")))
    session.commit()
    monkeypatch.setattr(cc_sidecar, "_live_paths", lambda _cid: set())

    summary = cc_sidecar.reconcile()

    assert summary["orders_pruned"] == 0
    assert cc_sidecar.stored_order(3) == {"778.3": 0}


def test_reconcile_can_be_called_repeatedly(sidecar_library):
    """It runs on every boot."""
    cc_sidecar.ensure_tables()
    cc_sidecar.reconcile()
    first = cc_sidecar.reconcile()
    assert first["available"] is True


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def test_a_column_with_no_row_reads_as_derived_not_as_flat(sidecar_library):
    """'Nothing stored' and 'declared flat' must not collapse into each other."""
    cc_sidecar.ensure_tables()

    verdict = cc_sidecar.hierarchy_verdict(3)

    assert verdict.value is None
    assert verdict.source == "derived"


def test_stored_order_is_empty_for_a_never_reordered_column(sidecar_library):
    """The property the whole read-path change depends on: no rows means the
    caller sorts alphabetically, exactly as today."""
    cc_sidecar.ensure_tables()
    cc_sidecar.reconcile()

    assert cc_sidecar.stored_order(3) == {}


def test_sibling_order_reads_one_parents_children(sidecar_library):
    cc_sidecar.ensure_tables()
    session = sidecar_library.calibre.session
    for path, parent, order in (("775", "", 0), ("778", "", 1),
                                ("778.3", "778", 0), ("778.72", "778", 1)):
        session.execute(text(
            "INSERT INTO %s (column_id, path, parent_path, view_order) "
            "VALUES (3, :p, :parent, :o)" % cc_sidecar._table("cc_value_order")),
            {"p": path, "parent": parent, "o": order})
    session.commit()

    assert list(cc_sidecar.sibling_order(3, "")) == ["775", "778"]
    assert list(cc_sidecar.sibling_order(3, "778")) == ["778.3", "778.72"]


# ---------------------------------------------------------------------------
# Phase 1 must not change behaviour
# ---------------------------------------------------------------------------

def test_nothing_in_phase_one_changes_how_a_column_is_rendered(sidecar_library):
    """The load-bearing regression guard for this phase.

    With no curated order stored, the tree must come out byte-identical to what
    the pre-sidecar code produces. If this fails, the phase is not inert and must
    not ship on its own.
    """
    cc_sidecar.ensure_tables()
    cc_sidecar.reconcile()
    values = [row[0] for row in
              sidecar_library.calibre.session.execute(
                  text('SELECT DISTINCT "value" FROM calibre.custom_column_3')).fetchall()]

    tree = hierarchy.parse_tag_hierarchy(values)

    assert [n["path"] for n in tree] == ["775", "778"]
    assert [c["path"] for c in tree[1]["children"]] == ["778.3", "778.72"]
