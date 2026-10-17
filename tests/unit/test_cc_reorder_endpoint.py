# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 4 -- the reorder endpoint and the five-way order maintenance.

The endpoint is the easy half. The half that matters is the **integration**:
once curated positions exist, all five existing value operations have to keep
them truthful, and every one of them fails *silently* when it does not.

The failure mode is uniform and nasty. A rename that forgets to carry its order
row still rewrites every stored value correctly -- the user sees exactly the
rename they asked for -- and the value has quietly jumped to the alphabetical
end of its siblings. Nothing errors. Nothing is logged. The only evidence is
that somebody's careful ordering has come apart.

So most of what follows is round-trip tests: order something, change the values
in each of the five ways, and assert the order survived or was deliberately
dropped. The sidecar here is a real two-file library, and the endpoint runs
through its real decorators.
"""
import sqlite3

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

from cps import cc_sidecar, db, hierarchy
from cps.api import cc_values

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixture: a real library with a real sidecar
# ---------------------------------------------------------------------------

@pytest.fixture
def library(tmp_path, monkeypatch):
    meta = str(tmp_path / "metadata.db")
    app = str(tmp_path / "app.db")

    conn = sqlite3.connect(meta)
    conn.executescript(
        """
        CREATE TABLE custom_columns (
            id INTEGER PRIMARY KEY, label TEXT, name TEXT, datatype TEXT,
            is_multiple INTEGER DEFAULT 1, display TEXT DEFAULT '{}');
        CREATE TABLE books (
            id INTEGER PRIMARY KEY, title TEXT, sort TEXT, series_index REAL,
            path TEXT DEFAULT '', author_sort TEXT DEFAULT '',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            pubdate TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            series TEXT DEFAULT '', languages TEXT DEFAULT '{}',
            identifiers TEXT DEFAULT '{}', lccn TEXT DEFAULT '',
            flags INTEGER DEFAULT 1, uuid TEXT DEFAULT '',
            last_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE custom_column_3 (
            id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT NOT NULL COLLATE NOCASE,
            link TEXT NOT NULL DEFAULT '', UNIQUE(value));
        CREATE TABLE books_custom_column_3_link (
            book INTEGER NOT NULL, value INTEGER NOT NULL,
            PRIMARY KEY (book, value));
        INSERT INTO custom_columns (id,label,name,datatype) VALUES
            (3,'d','ddc','text');
        INSERT INTO books (id,title) VALUES (1,'One'), (2,'Two');
        INSERT INTO custom_column_3 (id,value) VALUES
            (1,'775'), (2,'778'), (3,'779');
        INSERT INTO books_custom_column_3_link (book,value) VALUES (1,1), (2,3);
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

    class Value:
        """Minimal stand-in for the mapped custom-column class."""
        __tablename__ = "custom_column_3"

        def __init__(self, id=None, value=None):
            self.id = id
            self.value = value
            self.books = []

    calibre = object.__new__(db.CalibreDB)
    calibre.session = db.sessionmaker(bind=engine)()
    calibre.ensure_session = lambda *a, **k: None
    calibre.is_flat_cc_column = lambda col_id: False
    calibre.get_hierarchical_tree = lambda col_id, **_k: hierarchy.parse_tag_hierarchy(
        [row[0] for row in calibre.session.execute(
            text('SELECT DISTINCT "value" FROM calibre.custom_column_3')).fetchall()],
        cc_sidecar.stored_order(col_id))

    for module in (cc_values, cc_sidecar):
        monkeypatch.setattr(module, "calibre_db", calibre)
    monkeypatch.setattr(cc_sidecar, "db", db)
    monkeypatch.setattr(cc_sidecar.ub, "session", db.sessionmaker(bind=create_engine(
        "sqlite:///%s" % app.replace("\\", "/"), connect_args={"timeout": 30},
        poolclass=StaticPool))())
    monkeypatch.setattr(db, "cc_classes", {3: Value})
    cc_sidecar.ensure_tables()

    import types
    handle = types.SimpleNamespace(
        meta=meta, app=app, engine=engine, calibre=calibre, session=calibre.session)
    return handle


def _order(lib, column_id=3):
    return cc_sidecar.stored_order(column_id)


def _values(lib):
    return sorted(row[0] for row in lib.session.execute(
        text('SELECT "value" FROM calibre.custom_column_3')).fetchall())


def _set_order(lib, ordered_paths, column_id=3):
    cc_sidecar.set_order_for_siblings(column_id, ordered_paths)
    lib.session.commit()


# ---------------------------------------------------------------------------
# The carry/drop/set primitives
# ---------------------------------------------------------------------------

def test_parent_of_splits_a_dotted_path(library):
    assert cc_sidecar.parent_of("Arts.Painting") == "Arts"
    assert cc_sidecar.parent_of("Arts") == ""
    assert cc_sidecar.parent_of("") == ""
    assert cc_sidecar.parent_of("A.B.C") == "A.B"


def test_setting_an_order_records_every_sibling_not_just_the_moved_one(library):
    """The point of writing the whole list: an untouched sibling between two
    positioned ones would otherwise sort itself back by name."""
    _set_order(library, ["779", "775", "778"])

    assert _order(library) == {"779": 0, "775": 1, "778": 2}


def test_a_carry_moves_a_position_to_the_new_spelling(library):
    _set_order(library, ["779", "775", "778"])

    cc_sidecar.carry_order(3, {"775": "776"})
    library.session.commit()

    assert _order(library) == {"779": 0, "776": 1, "778": 2}, \
        "the renamed value keeps its place, not its name"


def test_a_carry_leaves_values_that_were_not_ordered_alone(library):
    _set_order(library, ["775"])

    cc_sidecar.carry_order(3, {"778": "780"})
    library.session.commit()

    assert _order(library) == {"775": 0}


def test_a_carry_into_an_existing_position_keeps_the_earliest(library):
    """A rename onto an existing value IS a merge, so two sources can land on
    one key. The survivor takes the topmost position of any of them."""
    _set_order(library, ["779", "775", "778"])

    cc_sidecar.carry_order(3, {"775": "778", "778": "778"})
    library.session.commit()

    order = _order(library)
    assert "778" in order
    assert order["778"] == min(
        cc_sidecar.sibling_order(3, "").get(p, 99) for p in ("778",))
    assert "775" not in order, "the source key is gone"


def test_dropping_an_order_forgets_only_the_paths_given(library):
    _set_order(library, ["779", "775", "778"])

    cc_sidecar.drop_order(3, ["775"])
    library.session.commit()

    assert set(_order(library)) == {"779", "778"}


# ---------------------------------------------------------------------------
# The five-way integration -- the tests that matter
# ---------------------------------------------------------------------------

def test_rename_carries_the_position(library):
    """The headline failure mode. Without the carry this test fails while every
    stored value is nonetheless rewritten correctly."""
    _set_order(library, ["779", "775", "778"])
    row = library.session.execute(
        text('SELECT id FROM calibre.custom_column_3 WHERE "value" = 778')).scalar()
    library.session.execute(
        text('UPDATE calibre.custom_column_3 SET "value" = :new WHERE id = :id'),
        {"new": "780", "id": row})
    library.session.commit()

    # What rename_cc_value does, in the same transaction.
    cc_sidecar.carry_order(3, {"778": "780"})
    library.session.commit()

    assert sorted(_order(library)) == ["775", "779", "780"]
    assert _order(library)["780"] == 2, "still last of the three"


def test_move_drops_the_position_rather_than_carrying_it(library):
    """The subtle one, and a test that changed the design.

    A curated position is only meaningful *relative to its siblings*: "0" among
    {775, 778, 779} says nothing among {Software.Adobe, Software.GIMP}.
    Carrying the integer across a parent change lands the value wherever its old
    number happened to fall in a list it was never part of. So a move drops the
    order and the branch sorts alphabetically among its new peers.
    """
    _set_order(library, ["775", "778"])
    library.session.execute(text(
        'UPDATE calibre.custom_column_3 SET "value" = :new WHERE "value" = 778'),
        {"new": "X.778"})
    library.session.commit()

    cc_sidecar.drop_order(3, ["778"])
    library.session.commit()

    order = _order(library)
    assert "X.778" not in order, "no position travelled to the new spelling"
    assert order == {"775": 0}
    tree = hierarchy.parse_tag_hierarchy(["775", "X.778"], order)
    assert [n["path"] for n in tree] == ["775", "X"]


def test_a_moved_value_lands_alphabetically_among_an_uncurated_new_parent(library):
    """What "the alphabetical remainder" actually means at the new level."""
    _set_order(library, ["778"])
    library.session.execute(text(
        'INSERT INTO calibre.custom_column_3 (id, "value") VALUES (90, :a), (91, :b)'),
        {"a": "X.Alpha", "b": "X.Bravo"})
    library.session.commit()
    library.session.execute(text(
        'UPDATE calibre.custom_column_3 SET "value" = :new WHERE "value" = 778'),
        {"new": "X.Charlie"})
    library.session.commit()

    cc_sidecar.drop_order(3, ["778"])
    library.session.commit()

    tree = hierarchy.parse_tag_hierarchy(
        ["X.Alpha", "X.Bravo", "X.Charlie", "775"], cc_sidecar.stored_order(3))
    x = [n for n in tree if n["path"] == "X"][0]
    assert [c["path"] for c in x["children"]] == \
        ["X.Alpha", "X.Bravo", "X.Charlie"]


def test_delete_drops_the_position(library):
    """Otherwise recreating the value would silently inherit the position of the
    one that was deleted."""
    _set_order(library, ["779", "775", "778"])

    cc_sidecar.drop_order(3, ["778"])
    library.session.execute(text('DELETE FROM calibre.custom_column_3 WHERE "value" = 778'))
    library.session.commit()

    assert set(_order(library)) == {"775", "779"}
    assert "778" not in _values(library)


def test_merge_drops_the_source_positions_and_keeps_the_survivor(library):
    _set_order(library, ["779", "775", "778"])

    cc_sidecar.drop_order(3, ["775", "778"])
    library.session.commit()

    assert set(_order(library)) == {"779"}, "the survivor keeps its own position"


def test_create_writes_no_position_at_all(library):
    """A new value sorts alphabetically among its peers. Appending it to the end
    of its level instead would be a different and less predictable default."""
    _set_order(library, ["779", "775"])
    library.session.execute(text(
        'INSERT INTO calibre.custom_column_3 (id, "value") VALUES (95, 770)'))
    library.session.commit()

    assert "770" not in _order(library)
    tree = hierarchy.parse_tag_hierarchy(
        ["770", "775", "779"], cc_sidecar.stored_order(3))
    assert [n["path"] for n in tree] == ["779", "775", "770"]


def test_a_full_session_survives_all_five_in_order(library):
    """The realistic sequence, because that is where the interactions show up:
    order some values, then rename one, move one, merge two, delete one and
    create one -- and the order must still describe reality at the end."""
    _set_order(library, ["775", "778", "779"])

    # rename 778 -> 780
    library.session.execute(text(
        'UPDATE calibre.custom_column_3 SET "value" = 780 WHERE "value" = 778'))
    cc_sidecar.carry_order(3, {"778": "780"})
    # move 779 under a new parent -- the order does NOT travel with it
    library.session.execute(text(
        'UPDATE calibre.custom_column_3 SET "value" = :n WHERE "value" = 779'),
        {"n": "X.779"})
    cc_sidecar.drop_order(3, ["779"])
    # merge 775 into 780
    library.session.execute(text('DELETE FROM calibre.custom_column_3 WHERE "value" = 775'))
    cc_sidecar.drop_order(3, ["775"])
    # delete 780
    library.session.execute(text('DELETE FROM calibre.custom_column_3 WHERE "value" = 780'))
    cc_sidecar.drop_order(3, ["780"])
    # create 800
    library.session.execute(text(
        'INSERT INTO calibre.custom_column_3 (id, "value") VALUES (96, 800)'))
    library.session.commit()

    order = _order(library)
    live = set(_values(library))
    assert set(order) <= live, "no position may point at a value that is gone"
    assert "800" not in order, "a created value gets no position until ordered"
    assert "X.779" not in order, "a moved value leaves its old ordering behind"
    assert order == {}, "every curated position was legitimately given up"
