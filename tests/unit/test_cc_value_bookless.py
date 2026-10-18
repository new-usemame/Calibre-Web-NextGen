# SPDX-License-Identifier: GPL-3.0-or-later
"""A value with no books is still a value, and must be visible.

"Create new" on the SPA browse page creates a ``custom_column_<id>`` row carrying
no book yet. Both browse readers used to be book-driven
(``.select_from(Books).join(rel)``), so such a value was invisible: it could be
created, then never found again to assign a book to it.

The first fix attempted an outer join from the value side. That does not work and
did not fail loudly -- ``Books.custom_column_<id>`` is a RELATIONSHIP, so
SQLAlchemy puts ``books`` in the FROM regardless of ``select_from`` and silently
discards it. The emitted SQL was still ``FROM books LEFT OUTER JOIN
custom_column_<id>``: book-driven, so the bookless value was still missing.

These tests run the REAL queries against a real generated schema (the existing
flat-column tests stub the query object and so never execute SQL). That is what
caught it the first time.

Counts must stay honest: a value nobody can see reports 0, and a subtree that
contains only such values reports 0 -- not 1.
"""
from sqlalchemy import Column, ForeignKey, Integer, String, create_engine, text
from sqlalchemy.orm import foreign, relationship, sessionmaker

from cps import db, hierarchy
from cps.api import cc_values

import pytest

pytestmark = pytest.mark.unit


# Declared ONCE at module scope. Defining it inside the fixture re-registers
# the table on the shared db.Base MetaData on every test, which SQLAlchemy
# rejects ("already defined for this MetaData instance").
# Column ids are deliberately HIGH and unused by the rest of the suite.
#
# Ids 2 and 3 are already claimed by test_flat_custom_columns.py, which
# monkeypatches db.Books.custom_column_2 / _3 to None for its own stubbed
# readers. Those monkeypatches are undone at teardown, but which module's
# assignment survives an import depends on import order -- so reusing those ids
# here made these tests pass alone (16/16) and fail in a full run, and moving the
# relationship into a fixture leaked into unrelated tag/kobo tests. Private ids
# remove the interaction entirely.
class Ddc(db.Base):
    __tablename__ = "custom_column_2403"
    id = Column(Integer, primary_key=True)
    book = Column(Integer, ForeignKey("books.id"))
    value = Column(String)
    # Declared on this side with uselist=True: a backref is not instrumented
    # here (db.Base is already mapper-configured at import time), and without
    # uselist SQLAlchemy reads the primaryjoin as MANYTOONE and returns a single
    # Book -- None -- instead of the list the readers iterate.
    books = relationship(db.Books, uselist=True,
                         primaryjoin=lambda: foreign(Ddc.book) == db.Books.id)


class Subj(db.Base):
    __tablename__ = "custom_column_2402"
    id = Column(Integer, primary_key=True)
    book = Column(Integer, ForeignKey("books.id"))
    value = Column(String)
    # Declared on THIS side, with uselist=True, for the same two reasons as
    # Ddc.books above: a Books-side backref is not instrumented once db.Base is
    # already mapper-configured, and SQLAlchemy otherwise infers MANYTOONE.
    books = relationship(db.Books, uselist=True,
                         primaryjoin=lambda: foreign(Subj.book) == db.Books.id)


def _install_relationship(col_id, model):
    """Attach ``Books.custom_column_<id>``; return a callable that undoes it.

    Two traps, both of which only show up in a full-suite run:

    * The relationship cannot be declared at import time. ``db.Base`` is already
      mapper-configured by then, and a relationship added to an already-mapped
      class is discarded -- the attribute resolves to ``None`` and the readers
      raise "Join target ... expected, got None".
    * ``monkeypatch.setattr`` cannot be used to undo it. Its teardown issues
      ``delattr`` when the attribute was absent beforehand, and SQLAlchemy
      refuses: "Can't un-map individual mapped attributes on a mapped class".

    So both directions are plain ``setattr``, and restoring ``None`` is
    deliberate: the attribute must stay present, because deleting it is exactly
    what SQLAlchemy rejects.
    """
    name = 'custom_column_%s' % col_id
    previous = getattr(db.Books, name, None)
    setattr(db.Books, name, relationship(
        model, primaryjoin=db.Books.id == model.book))
    return lambda: setattr(db.Books, name, previous)


# db.Books.custom_column_2403 / _2402 are installed by the fixtures, NOT here.
# db.Base is already mapper-configured by the time this module is imported, and
# a relationship assigned to an already-mapped class afterwards is discarded:
# the attribute is present but resolves to None and the readers fail with
# "Join target ... expected, got None". That is order-dependent, which is why
# the file passed 15/15 alone and failed 8 tests in a full run.


@pytest.fixture()
def library(monkeypatch):
    """A real custom_column_2403 table wired to the REAL mapped db.Books.

    The readers resolve the relationship as
    ``getattr(db.Books, 'custom_column_' + col_id)``, so the mapping must hang off
    the real Books class. Declaring a second Books here would create a second
    mapper over the same table and break every other test in the session.
    """
    engine = create_engine("sqlite://")
    db.Books.__table__.create(engine)
    db.CustomColumns.__table__.create(engine)
    Ddc.__table__.create(engine)

    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO custom_columns (id, label, name, datatype, is_multiple) VALUES "
            "(2403, 'ddc', 'DDC', 'text', 0)"))
        connection.execute(text(
            "INSERT INTO custom_column_2403 (id, book, value) VALUES (1, 1, '778.3')"))
        # The value "Create new" would make: a row, and no book.
        connection.execute(text(
            "INSERT INTO custom_column_2403 (id, book, value) VALUES (2, NULL, '999.9')"))
        connection.execute(text(
            "INSERT INTO custom_column_2403 (id, book, value) VALUES (3, NULL, 'Arts.Mystery')"))

    calibre = db.CalibreDB.__new__(db.CalibreDB)
    session = sessionmaker(bind=engine)()
    calibre.session = session
    calibre.common_filters = lambda *a, **kw: None

    monkeypatch.setattr(db, "cc_classes", {2403: Ddc})
    restore = _install_relationship(2403, Ddc)
    try:
        yield calibre
    finally:
        restore()
        session.close()
        engine.dispose()


@pytest.mark.unit
def test_flat_list_shows_a_value_no_book_carries(library):
    calibre = library
    entries = {e["name"]: e["count"] for e in calibre.get_cc_flat_list(2403, fail_on_error=True)}
    assert "999.9" in entries, "a bookless value must be listed, or Create new is a dead end"
    assert entries["999.9"] == 0, "it carries no book, so it must not claim one"


@pytest.mark.unit
def test_hierarchical_tree_shows_a_bookless_value_with_zero_counts(library):
    calibre = library
    nodes = calibre.get_hierarchical_tree(2403, fail_on_error=True)

    flat = {}

    def walk(items):
        for node in items:
            flat[node["path"]] = node
            walk(node["children"])

    walk(nodes)

    assert "999.9" in flat, "the bookless value must appear in the tree"
    assert flat["999.9"]["total_count"] == 0
    assert flat["999"]["total_count"] == 0, (
        "a parent whose only child is unassigned must not report one visible book")


@pytest.mark.unit
def test_a_bookless_intermediate_value_gets_its_whole_path(library):
    """'Arts.Mystery' carries no book either, and BOTH of its levels must exist —
    that is the shape "Create new" produces for a dotted name, and the parent is
    synthesized rather than left out."""
    calibre = library
    nodes = calibre.get_hierarchical_tree(2403, fail_on_error=True)

    flat = {}

    def walk(items):
        for node in items:
            flat[node["path"]] = node
            walk(node["children"])

    walk(nodes)
    assert {"Arts", "Arts.Mystery"} <= set(flat), sorted(flat)
    assert flat["Arts.Mystery"]["total_count"] == 0
    assert flat["Arts"]["total_count"] == 0, (
        "the synthesized parent has no visible book of its own")
    assert [c["path"] for c in flat["Arts"]["children"]] == ["Arts.Mystery"]


@pytest.mark.unit
def test_add_bookless_nodes_merges_into_an_existing_parent():
    """A bookless value whose parent already exists must attach to it rather than
    duplicate the parent -- otherwise 'Arts' appears twice in the tree."""
    nodes = hierarchy.parse_tag_hierarchy([(1, "Arts")])
    hierarchy.add_bookless_nodes(nodes, ["Arts.Mystery"])
    top = {n["name"] for n in nodes}
    assert top == {"Arts"}, top

    arts = next(n for n in nodes if n["name"] == "Arts")
    assert [c["path"] for c in arts["children"]] == ["Arts.Mystery"]
    # The parent had one real book; adding an unassigned child must not change it.
    assert arts["total_count"] == 1
    assert arts["children"][0]["total_count"] == 0


@pytest.mark.unit
def test_add_bookless_nodes_sorts_new_siblings_case_insensitively():
    nodes = hierarchy.parse_tag_hierarchy([])
    hierarchy.add_bookless_nodes(nodes, ["beta", "Alpha"])
    assert [n["name"] for n in nodes] == ["Alpha", "beta"]


@pytest.mark.unit
def test_add_bookless_nodes_ignores_blank_values():
    nodes = hierarchy.parse_tag_hierarchy([])
    hierarchy.add_bookless_nodes(nodes, ["", "   ", ".."])
    assert nodes == []


@pytest.mark.unit
def test_parse_tag_hierarchy_accepts_non_tuple_sequences():
    """Regression: every hierarchical column 500'd in the SPA browse view.

    ``Query.all()`` returns SQLAlchemy ``Row`` objects, and
    ``isinstance(row, tuple)`` is False for them. The parser's tuple check then
    treated each Row as a bare value string and handed the whole Row to
    ``split_path``, raising ``AttributeError: split`` -- so
    ``/api/v1/columns/<id>/tree`` failed for every hierarchical column.

    This pins the fix without a database: a plain tuple would pass the OLD code
    too, so the row-like input below is the whole point.
    """
    class RowLike:
        """A 2-item sequence that is not a tuple -- i.e. what SQLAlchemy hands
        back from Query.all()."""

        def __init__(self, book, value):
            self._pair = (book, value)

        def __len__(self):
            return 2

        def __getitem__(self, index):
            return self._pair[index]

    rows = [RowLike(1, "Arts"), RowLike(1, "Arts.Mystery")]
    assert not isinstance(rows[0], tuple), "the fixture must not be a tuple"

    tree = hierarchy.parse_tag_hierarchy(rows)
    arts = tree[0]
    assert arts["path"] == "Arts"
    assert [c["path"] for c in arts["children"]] == ["Arts.Mystery"], (
        "the child must nest under its parent, which is the whole point of the tree")
    assert arts["count"] == 1, "a real book must still be counted"
    assert arts["total_count"] == 1


@pytest.mark.unit
def test_parse_tag_hierarchy_still_takes_bare_strings():
    """The bare-string form must keep working -- it is how single values are
    passed, and the row fix must not have swallowed it."""
    tree = hierarchy.parse_tag_hierarchy(["Arts", "Arts.Mystery"])
    assert tree[0]["path"] == "Arts"
    assert [c["path"] for c in tree[0]["children"]] == ["Arts.Mystery"]


@pytest.mark.unit
def test_the_reader_returns_a_tree_for_real_rows(library):
    """End to end over the real reader: the SPA calls get_hierarchical_tree,
    and that is the call that was raising."""
    calibre = library
    nodes = calibre.get_hierarchical_tree(2403, fail_on_error=True)
    flat = {}

    def walk(items):
        for node in items:
            flat[node["path"]] = node
            walk(node["children"])

    walk(nodes)
    assert "999.9" in flat, sorted(flat)


# --------------------------------------------------------------------------- #
# Move / paste
# --------------------------------------------------------------------------- #

@pytest.fixture()
def tree_library(monkeypatch):
    """A hierarchical column shaped like the reported failure.

    ``software`` is a top-level node and ``Adobe`` is a *sibling* top-level node
    that itself has children -- i.e. cutting ``Adobe`` and pasting it under
    ``software`` must relocate the whole branch.
    """
    engine = create_engine("sqlite://")
    db.Books.__table__.create(engine)
    db.CustomColumns.__table__.create(engine)
    Subj.__table__.create(engine)

    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO custom_columns (id, label, name, datatype, is_multiple) VALUES "
            "(2402, 'subjects', '#subjects', 'text', 0)"))
        # A real book row, so the value -> books relationship resolves to a
        # collection rather than being left uninstrumented. Built through the
        # ORM so every NOT NULL column gets a value.
        connection.execute(text(
            "INSERT INTO books (id, title, sort, series_index, path, author_sort,"
            " timestamp, pubdate, last_modified, has_cover) VALUES "
            "(1, 'One', 'One', 1.0, '', 'One', '2020-01-01 00:00:00',"
            " '2020-01-01 00:00:00', '2020-01-01 00:00:00', 0)"))
        for index, value in enumerate([
            "software",          # destination parent, holds a book
            "Adobe",             # the node being pasted -- an interior node
            "Adobe.Photoshop",   # its child
            "Adobe.Acrobat",     # its child
        ]):
            connection.execute(text(
                "INSERT INTO custom_column_2402 (id, book, value) VALUES (:i, :b, :v)"),
                {"i": index + 1, "b": 1 if value == "software" else None, "v": value})

    calibre = db.CalibreDB.__new__(db.CalibreDB)
    session = sessionmaker(bind=engine)()
    calibre.session = session
    calibre.common_filters = lambda *a, **kw: None
    monkeypatch.setattr(db, "cc_classes", {2402: Subj})
    _install_relationship(2402, Subj)
    # cc_values does `from .. import calibre_db`, binding that NAME at import
    # time. Patching db.calibre_db would leave the module still talking to the
    # real, unconnected instance -- so the name is patched where it is used.
    monkeypatch.setattr(cc_values, "calibre_db", calibre)
    try:
        yield calibre
    finally:
        session.close()
        engine.dispose()


@pytest.mark.unit
def test_pasting_an_interior_node_relocates_its_whole_branch(tree_library):
    """Regression: cutting "Adobe" and pasting under "software" did nothing.

    The move handler called ``_plan_rename(col_id, path, leaf, True)``. That
    planner rebuilds the destination from the CURRENT parent plus the name given,
    so passing the unchanged leaf reconstructs ``path`` itself, trips its own
    "cannot be moved inside itself" guard, and returns an EMPTY mapping. The
    caller discarded the error, so the transaction committed nothing and the
    endpoint answered ``200 {"moved": {}}`` -- the banner appeared, the tree never
    changed, no error shown.

    This drives the real tree query, so an empty mapping fails here.
    """
    mapping, errors, new_path = cc_values._plan_move(2402, "Adobe", "software")

    assert not errors, errors
    assert new_path == "software.Adobe"
    assert mapping, (
        "a move of a node with children must rewrite the branch; an empty mapping "
        "is what made paste a silent no-op")
    assert mapping == {
        "Adobe": "software.Adobe",
        "Adobe.Photoshop": "software.Adobe.Photoshop",
        "Adobe.Acrobat": "software.Adobe.Acrobat",
    }, mapping


@pytest.mark.unit
def test_pasting_a_leaf_moves_only_that_leaf(tree_library):
    """A move re-parents the node under the target using the node's OWN name.

    Pasting 'Adobe.Photoshop' under 'software' yields 'software.Photoshop' --
    the original parent is dropped, not carried along. That is what 'paste below
    this node' means, and it is the only reading consistent with the client-side
    guard in canPasteInto.
    """
    mapping, errors, new_path = cc_values._plan_move(2402, "Adobe.Photoshop", "software")
    assert not errors, errors
    assert new_path == "software.Photoshop"
    assert mapping == {"Adobe.Photoshop": "software.Photoshop"}, mapping


@pytest.mark.unit
def test_pasting_a_root_onto_the_top_level_is_refused(tree_library):
    """'software' is already at the top, so there is nowhere to move it."""
    mapping, errors, new_path = cc_values._plan_move(2402, "software", "")
    assert mapping == {}
    assert errors == ["That value is already there"], errors
    assert new_path == "software"


@pytest.mark.unit
def test_pasting_an_interior_node_up_to_the_top_level(tree_library):
    """A real top-level paste of a branch: 'Adobe' -> 'Adobe' at the root is a
    no-op, so use a node that genuinely has a parent."""
    tree_library.session.execute(text(
        "INSERT INTO custom_column_2402 (id, book, value) VALUES (10, NULL, 'tools.Adobe')"))
    tree_library.session.commit()

    mapping, errors, new_path = cc_values._plan_move(2402, "tools.Adobe", "")
    assert not errors, errors
    assert new_path == "Adobe"
    assert mapping == {"tools.Adobe": "Adobe"}, mapping


@pytest.mark.unit
def test_a_node_cannot_be_pasted_inside_itself(tree_library):
    """Pasting "Adobe" under its own child must be refused, not silently applied."""
    mapping, errors, _ = cc_values._plan_move(2402, "Adobe", "Adobe.Photoshop")
    assert mapping == {}
    assert errors, "moving a node into its own subtree must be refused"


@pytest.mark.unit
def test_a_move_onto_an_occupied_destination_is_reported_as_a_merge(tree_library):
    """Renaming the destination is refused, never folded silently -- the same
    opt-in contract as rename and tag rename (#973)."""
    mapping, _, _ = cc_values._plan_move(2402, "Adobe", "software")
    # Pretend 'software.Adobe' already exists and see how the split behaves.
    tree_library.session.execute(text(
        "INSERT INTO custom_column_2402 (id, book, value) VALUES (99, NULL, 'software.Adobe')"))
    tree_library.session.commit()

    renames, merges = cc_values._resolve_merges(2402, mapping)
    assert merges, "an existing destination value must surface as a merge"
    assert renames == {
        "Adobe.Photoshop": "software.Adobe.Photoshop",
        "Adobe.Acrobat": "software.Adobe.Acrobat",
    }, renames


# --------------------------------------------------------------------------- #
# Cut / paste over a three-level tree
# --------------------------------------------------------------------------- #

# The tree these tests move values around in. Deliberately three deep, because
# every bug worth finding here lives in the difference between "this node",
# "its parent" and "its top-level ancestor":
#
#   TestNode
#   TestNode.TestSubNode
#   TestNode.TestSubNode.TestDeepNode
#   History
#   History.AncientHistory
#   History.AncientHistory.India
#   Other
CUT_VALUES = [
    "TestNode",
    "TestNode.TestSubNode",
    "TestNode.TestSubNode.TestDeepNode",
    "History",
    "History.AncientHistory",
    "History.AncientHistory.India",
    "Other",
]


@pytest.fixture()
def cut_tree(monkeypatch):
    engine = create_engine("sqlite://")
    db.Books.__table__.create(engine)
    db.CustomColumns.__table__.create(engine)
    Subj.__table__.create(engine)

    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO custom_columns (id, label, name, datatype, is_multiple) VALUES "
            "(2402, 'subjects', '#subjects', 'text', 0)"))
        connection.execute(text(
            "INSERT INTO books (id, title, sort, series_index, path, author_sort,"
            " timestamp, pubdate, last_modified, has_cover) VALUES "
            "(1, 'One', 'One', 1.0, '', 'One', '2020-01-01 00:00:00',"
            " '2020-01-01 00:00:00', '2020-01-01 00:00:00', 0)"))
        for index, value in enumerate(CUT_VALUES):
            connection.execute(text(
                "INSERT INTO custom_column_2402 (id, book, value) VALUES (:i, 1, :v)"),
                {"i": index + 1, "v": value})

    calibre = db.CalibreDB.__new__(db.CalibreDB)
    session = sessionmaker(bind=engine)()
    calibre.session = session
    calibre.common_filters = lambda *a, **kw: None
    monkeypatch.setattr(db, "cc_classes", {2402: Subj})
    monkeypatch.setattr(cc_values, "calibre_db", calibre)
    restore = _install_relationship(2402, Subj)
    try:
        yield calibre
    finally:
        restore()
        session.close()
        engine.dispose()


@pytest.mark.unit
def test_a_deep_node_can_be_pasted_under_its_own_ancestor(cut_tree):
    """The reported case: move History.AncientHistory.India up under History.

    Landing it in the grandparent is legal and is NOT a cycle -- the result is
    History.India, which is a sibling of History.AncientHistory rather than a
    descendant of it. Only pasting into oneself or one's own DESCENDANTS is a
    cycle, which _plan_move refuses.
    """
    mapping, errors, new_path = cc_values._plan_move(
        2402, "History.AncientHistory.India", "History")

    assert not errors, errors
    assert new_path == "History.India"
    assert mapping == {"History.AncientHistory.India": "History.India"}, mapping


@pytest.mark.unit
def test_pasting_a_deep_node_leaves_its_ancestors_untouched(cut_tree):
    """Cutting a bottom node must NOT drag its parents along.

    This is the reported suspicion ("when you select the bottom node, the top
    level node gets copied as well, though it is not selected"). Only the stored
    spellings UNDER the cut node may appear in the mapping; an untouched
    ancestor must not be rewritten or duplicated.
    """
    mapping, errors, _ = cc_values._plan_move(
        2402, "History.AncientHistory.India", "Other")

    assert not errors, errors
    assert set(mapping) == {"History.AncientHistory.India"}, mapping
    for ancestor in ("History", "History.AncientHistory"):
        assert ancestor not in mapping, (
            "%s was not cut, so it must not appear in the rewrite" % ancestor)
        assert ancestor not in mapping.values(), (
            "%s must not be recreated either" % ancestor)


@pytest.mark.unit
def test_cutting_an_interior_node_moves_its_whole_subtree_only(cut_tree):
    """Cutting TestNode.TestSubNode takes TestDeepNode with it, and stops there.

    TestNode itself is the PARENT of the cut node, so it must be untouched --
    this is the other half of the "does it drag the ancestors along" question.
    """
    mapping, errors, _ = cc_values._plan_move(2402, "TestNode.TestSubNode", "Other")

    assert not errors, errors
    assert set(mapping) == {
        "TestNode.TestSubNode",
        "TestNode.TestSubNode.TestDeepNode",
    }, mapping
    assert mapping["TestNode.TestSubNode"] == "Other.TestSubNode"
    assert mapping["TestNode.TestSubNode.TestDeepNode"] == "Other.TestSubNode.TestDeepNode"
    assert "TestNode" not in mapping, "the parent of the cut node must not move"


@pytest.mark.unit
def test_cutting_a_middle_node_leaves_the_grandchild_grouped(cut_tree):
    """TestSubNode moves as a unit: renaming the prefix keeps the shape intact.

    This is the property that makes a branch move reversible-by-hand and not a
    scatter of unrelated values.
    """
    mapping, errors, _ = cc_values._plan_move(2402, "TestNode.TestSubNode", "Other")

    assert not errors, errors
    for old, new in mapping.items():
        assert old.split(".")[-1] == new.split(".")[-1], (
            "the leaf name must be preserved, only the prefix changes: %r -> %r" % (old, new))


@pytest.mark.unit
def test_a_node_cannot_be_pasted_into_its_own_descendant_at_depth(cut_tree):
    """The cycle guard must hold at depth, not just for a two-level tree."""
    mapping, errors, _ = cc_values._plan_move(
        2402, "TestNode", "TestNode.TestSubNode.TestDeepNode")

    assert mapping == {}
    assert errors, "a node must not be movable inside its own subtree"


@pytest.mark.unit
def test_pasting_onto_the_same_parent_is_refused_as_a_no_op(cut_tree):
    """Pasting TestNode.TestSubNode back under TestNode changes nothing."""
    mapping, errors, new_path = cc_values._plan_move(
        2402, "TestNode.TestSubNode", "TestNode")

    assert mapping == {}
    assert new_path == "TestNode.TestSubNode"
    assert errors, "the endpoint must report this rather than answer 200 doing nothing"

