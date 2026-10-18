# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 2 -- the order-aware read path.

``hierarchy._finalize`` used to sort siblings alphabetically, full stop. It now
honours a curated order held in the app.db sidecar, falling back to alphabetical
for anything nobody has positioned.

The property that makes this landable is that **an empty sidecar changes
nothing**. With no ``order`` argument the function must return exactly what it
returned before, which is asserted directly rather than assumed -- it is the
difference between a safe phase and a visible one.
"""
import json

import pytest

from cps import hierarchy

pytestmark = pytest.mark.unit


DEWEY = ["775", "778.3", "778.72", "778.993925"]


def paths(nodes):
    return [n["path"] for n in nodes]


def child_paths(node):
    return [c["path"] for c in node["children"]]


# ---------------------------------------------------------------------------
# The inert property
# ---------------------------------------------------------------------------

def test_no_order_is_byte_identical_to_the_pre_sidecar_sort():
    """The whole phase rests on this. Omit the order and the alphabet wins."""
    tree = hierarchy.parse_tag_hierarchy(DEWEY)

    assert paths(tree) == ["775", "778"]
    assert child_paths(tree[1]) == ["778.3", "778.72", "778.993925"]


def test_an_empty_order_map_changes_nothing():
    """A sidecar that exists but holds no rows is the state of every install
    that has not used the feature yet, so it must behave like no sidecar."""
    assert hierarchy.parse_tag_hierarchy(DEWEY, {}) == \
        hierarchy.parse_tag_hierarchy(DEWEY, None)


def test_a_stored_order_puts_its_value_first():
    tree = hierarchy.parse_tag_hierarchy(DEWEY, {"778": 0, "775": 1})

    assert paths(tree) == ["778", "775"]


def test_unordered_siblings_sort_after_ordered_ones_alphabetically():
    """A curated position moves a value to the front; it must not scatter the
    rest. The ones with no position keep their alphabetical relative order."""
    tree = hierarchy.parse_tag_hierarchy(["Delta", "Alpha", "Charlie", "Bravo"],
                                         {"Charlie": 0})

    assert paths(tree) == ["Charlie", "Alpha", "Bravo", "Delta"]


def test_the_order_applies_at_every_level_not_just_the_root():
    """A Dewey column's interesting ordering is under ``778``, and the root
    level usually has one node, so a root-only implementation would look
    correct and do nothing."""
    tree = hierarchy.parse_tag_hierarchy(DEWEY,
                                         {"778.72": 0, "778": 1, "775": 2,
                                          "778.3": 3, "778.993925": 4})

    assert paths(tree) == ["778", "775"]
    assert child_paths(tree[0]) == ["778.72", "778.3", "778.993925"]


def test_an_order_row_for_a_value_that_no_longer_exists_is_ignored():
    """Reconcile prunes those, but the read path must not break on one -- and
    must not let a phantom reserve a slot ahead of everything else."""
    tree = hierarchy.parse_tag_hierarchy(["B", "A"], {"GHOST": 0})

    assert paths(tree) == ["A", "B"]


def test_ordering_is_case_insensitive_among_unordered_siblings():
    """The fallback sorts on ``name.lower()`` today; the curated path must not
    quietly change that."""
    tree = hierarchy.parse_tag_hierarchy(["beta", "Alpha", "Gamma"])

    assert paths(tree) == ["Alpha", "beta", "Gamma"]


# ---------------------------------------------------------------------------
# add_bookless_nodes
# ---------------------------------------------------------------------------

def test_a_bookless_node_lands_where_its_curated_position_says():
    """It sorts independently of parse_tag_hierarchy, so it needs the order
    too -- otherwise a value with no visible book is alphabetised into the
    middle of an ordering somebody set by hand."""
    tree = hierarchy.parse_tag_hierarchy(["775", "778.3"])
    hierarchy.add_bookless_nodes(tree, ["888"], {"888": 0})

    assert paths(tree) == ["888", "775", "778"]


def test_an_unordered_bookless_node_still_sorts_alphabetically():
    tree = hierarchy.parse_tag_hierarchy(["775"])
    hierarchy.add_bookless_nodes(tree, ["888"], {})

    assert paths(tree) == ["775", "888"]


def test_a_bookless_dotted_value_gets_its_synthesised_parents_too():
    """``888.1`` implies ``888``, which has no value row of its own. Both land,
    and the curated position on the parent is honoured for the parent."""
    tree = hierarchy.parse_tag_hierarchy(["775"])
    hierarchy.add_bookless_nodes(tree, ["888.1"], {"888": 0})

    assert paths(tree) == ["888", "775"]
    assert child_paths(tree[0]) == ["888.1"]


def test_add_bookless_nodes_without_an_order_is_unchanged():
    """The existing suite pins this sort; the signature grew, the behaviour
    must not have moved."""
    tree = hierarchy.parse_tag_hierarchy(["Arts"])
    hierarchy.add_bookless_nodes(tree, ["Zebra", "Alpha"])

    assert paths(tree) == ["Alpha", "Arts", "Zebra"]


# ---------------------------------------------------------------------------
# Purity: the rules module still cannot reach the sidecar itself
# ---------------------------------------------------------------------------

def test_the_rule_module_still_imports_nothing_from_the_web_stack():
    """The order map is passed IN, not fetched.

    ``cps.hierarchy`` is the single home for the node rules, and that only
    holds while it stays free of database and framework imports -- which is what
    lets the classic UI and the SPA call the same implementation. Growing a
    ``cc_sidecar`` import here to fetch the order would trade that guarantee for
    one convenience.
    """
    import ast
    import pathlib

    source = pathlib.Path("cps/hierarchy.py").read_text(encoding="utf-8")
    banned = ("flask", "flask_babel", "sqlalchemy", "cps", "db", "cc_sidecar",
              "calibre_db")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned, alias.name
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert node.level == 0, "relative import %r" % (node.module,)
            assert root not in banned, node.module


def test_a_call_with_two_arguments_still_works():
    """The signature grew an optional parameter, so every existing caller --
    tests, adapters, anything -- must keep working untouched."""
    assert hierarchy.parse_tag_hierarchy(["B", "A"])
    assert hierarchy.parse_tag_hierarchy([("1", "B"), ("2", "A")])
    assert hierarchy.parse_tag_hierarchy(["B.A", "B"])
    # add_bookless_nodes still synthesises a node with no order to go on.
    assert paths(hierarchy.add_bookless_nodes([], ["A"])) == ["A"]


def test_order_values_of_any_magnitude_sort_sensibly():
    """``max + 1`` grows monotonically and holes are left after a delete, so the
    reader must not assume a small dense range."""
    tree = hierarchy.parse_tag_hierarchy(["A", "B", "C"],
                                         {"C": 900001, "A": 900002})

    assert paths(tree) == ["C", "A", "B"]


def test_a_zero_order_value_still_beats_an_unordered_sibling():
    """0 is a legitimate first position and must not be confused with 'unset'."""
    tree = hierarchy.parse_tag_hierarchy(["Zebra", "Alpha"], {"Zebra": 0})

    assert paths(tree) == ["Zebra", "Alpha"]


def test_json_round_trip_of_a_stored_order_does_not_change_the_result():
    """The sidecar hands back plain ints from SQLite. A value that has been
    through JSON anywhere on its way must still compare as a number."""
    stored = json.loads(json.dumps({"Zebra": 0, "Alpha": 1}))
    tree = hierarchy.parse_tag_hierarchy(["Zebra", "Alpha"], stored)

    assert paths(tree) == ["Zebra", "Alpha"]


# ---------------------------------------------------------------------------
# The read surfaces all go through the same path
# ---------------------------------------------------------------------------

def test_every_reader_of_a_sibling_list_reaches_the_same_ordering_helper():
    """Six surfaces render siblings in tree order -- classic browse, classic
    editor rows, SPA browse and editor, OPDS, and the write endpoints' own tree
    queries. They differ only in where they get the tree, so the ordering
    decision has to be made in exactly one place: ``_order_key``.

    A source assertion rather than a behavioural one, which is the honest trade
    here: a fake ``CalibreDB`` cannot prove six surfaces agree, but a second
    sort key in ``db.py`` would silently split them.
    """
    import pathlib

    source = pathlib.Path("cps/db.py").read_text(encoding="utf-8")
    # The tree path never names the sort key: parse_tag_hierarchy applies it
    # internally via _finalize. Only the flat list, which sorts on its own,
    # reaches for it. Two callers naming it would mean a second sort key.
    assert source.count("hierarchy._order_key") == 1, (
        "only get_cc_flat_list should name hierarchy._order_key; the tree path "
        "gets the ordering from parse_tag_hierarchy(rows, order)")
    assert "entries.sort(key=lambda e: e['name'].lower())" not in source, (
        "a flat list is still sorting alphabetically -- curated order would "
        "not apply to flat columns")
    # ...and neither may call parse_tag_hierarchy without passing the order.
    assert "hierarchy.parse_tag_hierarchy(rows)" not in source
    assert "hierarchy.add_bookless_nodes(nodes, [" not in source
