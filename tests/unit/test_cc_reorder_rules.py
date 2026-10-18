# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 3 -- the reorder rule and the published move flags.

Reorder is the one value operation that changes **no stored value**. A node and
its subtree keep their paths; only the order in which siblings are listed moves.
That is what makes it easy to get wrong quietly -- a broken reorder looks like a
sorting bug, not like data loss -- and it is why the "nothing else moved"
assertions below are the load-bearing ones rather than incidental.

``can_move_up``/``can_move_down`` are *published* rather than client-derived,
unlike ``can_merge_into``. The answer turns only on the node's place among its
siblings, so there was never a reason for a client to work it out for itself.
"""
import pytest

from cps import hierarchy

pytestmark = pytest.mark.unit


def node(path, children=()):
    parts = path.split(".")
    return {
        "name": parts[-1],
        "path": path,
        "count": 0,
        "total_count": 0,
        "children": list(children),
        "books": set(),
        "raw_values": set(),
    }


SIBLINGS = ["Arts", "History", "Science"]


# ---------------------------------------------------------------------------
# plan_reorder
# ---------------------------------------------------------------------------

def test_moving_up_swaps_with_the_sibling_above():
    ordered, error = hierarchy.plan_reorder("History", SIBLINGS, "up")

    assert error is None
    assert ordered == ["History", "Arts", "Science"]


def test_moving_down_swaps_with_the_sibling_below():
    ordered, error = hierarchy.plan_reorder("Arts", SIBLINGS, "down")

    assert error is None
    assert ordered == ["History", "Arts", "Science"]


def test_a_move_returns_every_sibling_not_just_the_one_that_moved():
    """The caller writes the whole list back, which is what takes the nodes the
    user arranged out of the alphabetical fallback. Returning only the moved
    node would leave its neighbours unordered."""
    ordered, _ = hierarchy.plan_reorder("Arts", SIBLINGS, "down")

    assert sorted(ordered) == sorted(SIBLINGS)


def test_the_first_sibling_cannot_move_up():
    ordered, error = hierarchy.plan_reorder("Arts", SIBLINGS, "up")

    assert ordered == SIBLINGS, "a refused move must leave the order alone"
    assert error == (hierarchy.ERR_NO_NEIGHBOUR, "up")


def test_the_last_sibling_cannot_move_down():
    ordered, error = hierarchy.plan_reorder("Science", SIBLINGS, "down")

    assert ordered == SIBLINGS
    assert error == (hierarchy.ERR_NO_NEIGHBOUR, "down")


def test_an_only_child_can_move_neither_way():
    for direction in ("up", "down"):
        ordered, error = hierarchy.plan_reorder("Arts", ["Arts"], direction)
        assert error == (hierarchy.ERR_NO_NEIGHBOUR, direction)
        assert ordered == ["Arts"]


def test_a_value_that_is_not_a_sibling_is_refused():
    """The guard that stops a stale click writing an order for a node that has
    been renamed or deleted since the tree was rendered."""
    ordered, error = hierarchy.plan_reorder("Gone", SIBLINGS, "up")

    assert error == (hierarchy.ERR_MISSING_SOURCE, "Gone")
    assert ordered == SIBLINGS


def test_a_move_changes_no_path_anywhere_in_the_subtree():
    """The property that distinguishes reorder from rename and move.

    Renaming an interior node rewrites every value beneath it; re-parenting
    changes where a subtree hangs. Reordering touches neither -- it is purely a
    change to the order of a list, so re-sorting the tree by the new order must
    produce the same set of paths.
    """
    values = ["Arts.Painting", "Arts.Sculpture", "History", "Science"]
    tree = hierarchy.parse_tag_hierarchy(values)
    before = _all_paths(tree)

    ordered, error = hierarchy.plan_reorder("History", ["Arts", "History", "Science"], "up")

    assert error is None
    resorted = hierarchy.parse_tag_hierarchy(
        values, {"Arts": 1, "History": 0, "Science": 2,
                 "Arts.Painting": 0, "Arts.Sculpture": 1})
    assert _all_paths(resorted) == before, "a reorder must not add, drop or rename a path"
    assert [n["path"] for n in resorted] == ["History", "Arts", "Science"]


def _all_paths(nodes):
    paths = []
    stack = list(nodes)
    while stack:
        current = stack.pop()
        paths.append(current["path"])
        stack.extend(current["children"])
    return sorted(paths)


def test_the_rule_does_not_mutate_the_list_it_is_given():
    """The caller reuses that list -- it is the live sibling order, not a copy."""
    siblings = list(SIBLINGS)
    hierarchy.plan_reorder("Arts", siblings, "down")

    assert siblings == SIBLINGS


def test_a_move_is_its_own_inverse():
    ordered, _ = hierarchy.plan_reorder("Arts", SIBLINGS, "down")
    restored, error = hierarchy.plan_reorder("Arts", ordered, "up")

    assert error is None
    assert restored == SIBLINGS


def test_repeated_moves_reach_every_position_and_then_stop():
    """Walking one value all the way down, and the refusal that ends it."""
    current = list(SIBLINGS)
    seen = [list(current)]
    for _ in range(len(SIBLINGS)):
        current, error = hierarchy.plan_reorder("Arts", current, "down")
        if error is not None:
            break
        seen.append(list(current))

    assert seen == [
        ["Arts", "History", "Science"],
        ["History", "Arts", "Science"],
        ["History", "Science", "Arts"],
    ]
    assert error == (hierarchy.ERR_NO_NEIGHBOUR, "down")


# ---------------------------------------------------------------------------
# The published flags
# ---------------------------------------------------------------------------

def test_a_middle_sibling_can_move_both_ways():
    available = hierarchy.node_availability(node("History"), SIBLINGS)

    assert available["can_move_up"] is True
    assert available["can_move_down"] is True
    assert available["move_up_reason"] is None
    assert available["move_down_reason"] is None


def test_the_first_sibling_is_published_as_cannot_move_up():
    available = hierarchy.node_availability(node("Arts"), SIBLINGS)

    assert available["can_move_up"] is False
    assert available["move_up_reason"] == hierarchy.ERR_NO_NEIGHBOUR
    assert available["can_move_down"] is True


def test_the_last_sibling_is_published_as_cannot_move_down():
    available = hierarchy.node_availability(node("Science"), SIBLINGS)

    assert available["can_move_down"] is False
    assert available["move_down_reason"] == hierarchy.ERR_NO_NEIGHBOUR


def test_the_flags_are_the_rules_own_verdict():
    """Not a restatement of plan_reorder's logic -- the actual planners are run
    against the same node, so a flag and the endpoint cannot drift."""
    for path in SIBLINGS:
        for direction, flag, reason in (
                ("up", "can_move_up", "move_up_reason"),
                ("down", "can_move_down", "move_down_reason")):
            _, error = hierarchy.plan_reorder(path, SIBLINGS, direction)
            available = hierarchy.node_availability(node(path), SIBLINGS)
            assert available[flag] is (error is None), path
            assert available[reason] == (None if error is None else error[0])


def test_move_flags_do_not_depend_on_the_node_having_children():
    """An interior node reorders exactly as a leaf does. Whether it has children
    is the delete/merge rule's business, and coupling them would quietly stop
    anyone being able to move a whole branch."""
    interior = node("Arts", [node("Arts.Painting")])

    available = hierarchy.node_availability(interior, ["Arts", "History"])

    assert available["can_move_down"] is True
    assert available["can_delete"] is False, "still a leaf-only operation"


def test_without_a_sibling_list_the_move_flags_are_refused():
    """A node alone cannot answer "can this move up", and the safe direction is
    to offer nothing. A client that cannot tell must not offer a live button."""
    available = hierarchy.node_availability(node("History"))

    assert available["can_move_up"] is False
    assert available["can_move_down"] is False
    assert available["move_up_reason"] == hierarchy.ERR_NO_NEIGHBOUR


def test_the_wire_shape_gained_exactly_two_flag_pairs():
    """Pinned so a future flag cannot be added without this test noticing."""
    available = hierarchy.node_availability(node("History"), SIBLINGS)

    assert set(available) == {
        "can_delete", "delete_reason",
        "can_merge_source", "merge_reason",
        "can_move_up", "move_up_reason",
        "can_move_down", "move_down_reason",
    }


def test_the_published_flags_still_answer_delete_and_merge():
    """Adding two pairs must not have disturbed the two that were already there."""
    leaf = hierarchy.node_availability(node("History"), SIBLINGS)
    assert leaf["can_delete"] is True
    assert leaf["can_merge_source"] is True

    parent = hierarchy.node_availability(
        node("Arts", [node("Arts.Painting")]), ["Arts", "History"])
    assert parent["can_delete"] is False
    assert parent["can_merge_source"] is False


# ---------------------------------------------------------------------------
# Both transports publish the same verdict
# ---------------------------------------------------------------------------

def test_the_classic_rows_and_the_json_tree_agree_about_moving():
    """Two transports, one decision -- the arrangement the whole single-home
    refactor exists to protect, now extended to the move flags."""
    from cps import web
    from cps.api.columns import _node_to_json

    tree = hierarchy.parse_tag_hierarchy(["Arts.Painting", "History", "Science"])

    rows = {r["path"]: r for r in web.cc_value_edit_rows(tree, True)}
    wire = {}
    stack = list(tree)
    while stack:
        current = stack.pop()
        payload = _node_to_json(current, _siblings_of(tree, current["path"]))
        wire[payload["path"]] = payload["available"]
        stack.extend(current["children"])

    for path, row in rows.items():
        assert row["can_move_up"] == wire[path]["can_move_up"], path
        assert row["can_move_down"] == wire[path]["can_move_down"], path


def _siblings_of(nodes, path):
    """The sibling list a node belongs to, found the way a client would."""
    level = nodes
    parts = path.split(".")
    for index in range(len(parts) - 1):
        match = [n for n in level if n["name"] == parts[index]]
        if not match:
            return []
        level = match[0]["children"]
    return level


def test_a_root_node_and_its_parent_each_get_their_own_sibling_list():
    """The easy mistake: passing the whole column to every node, which would
    make ``History`` look movable when it is the only root."""
    from cps import web

    tree = hierarchy.parse_tag_hierarchy(
        ["Arts.Painting", "Arts.Sculpture", "History", "Science"])

    rows = {r["path"]: r for r in web.cc_value_edit_rows(tree, True)}

    # History is the second of three roots, so it can go up but not down.
    assert rows["History"]["can_move_up"] is True
    assert rows["History"]["can_move_down"] is True
    # Arts is the first root.
    assert rows["Arts"]["can_move_up"] is False
    # The children of Arts are compared with each other, NOT with the roots:
    # Painting is first among its own two siblings.
    assert rows["Arts.Painting"]["can_move_up"] is False
    assert rows["Arts.Painting"]["can_move_down"] is True
    assert rows["Arts.Sculpture"]["can_move_down"] is False
