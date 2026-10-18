# SPDX-License-Identifier: GPL-3.0-or-later
"""Rule-level gaps the main suites never pinned.

Complements tests/unit/test_hierarchy_node_rules.py. These are not new rules --
they pin behaviours of the EXISTING rules that a future refactor could silently
change, each of which the endpoint layer depends on.
"""
import pytest

from cps import hierarchy

pytestmark = pytest.mark.unit


def test_plan_reorder_targets_the_first_occurrence_of_a_duplicated_sibling():
    """A sibling list should never contain a path twice, but nothing forbids
    one. Pinned current behaviour: `list.index` targets the FIRST occurrence,
    so the swap is well-defined rather than arbitrary."""
    siblings = ["Arts", "History", "Arts"]
    reordered, error = hierarchy.plan_reorder("Arts", siblings, "down")

    assert error is None
    assert reordered == ["History", "Arts", "Arts"]


@pytest.mark.xfail(
    strict=True,
    reason="BUG (found while writing this suite): plan_move/plan_rename rewrite "
           "a raw spelling as new_path + raw[len(path):], which assumes the raw "
           "spelling starts with the exact path. A whitespace-variant spelling "
           "that normalises to the same node ('Arts. Fiction' -> Arts.Fiction) "
           "does not, and the suffix slice produces garbage ('History.Fictionn'). "
           "Remove this marker when the rewrite is prefix-safe.")
def test_plan_move_relocates_every_stored_spelling_of_the_node():
    """A node can be spelled several ways in the database ('Arts.' and
    'Arts. DB' both normalise to Arts) and _raw_values_under's docstring
    promises an operation must move ALL of them. A stored spelling may also
    differ inside the path ('Arts. Fiction' normalises to Arts.Fiction), and
    the rewrite must keep it a spelling of the SAME node."""
    mapping, error, new_path = hierarchy.plan_move(
        "Arts.Fiction", "History", {"Arts.Fiction", "Arts. Fiction"})

    assert error is None
    assert new_path == "History.Fiction"
    assert mapping == {"Arts.Fiction": "History.Fiction",
                       "Arts. Fiction": "History. Fiction"}


def test_plan_rename_refuses_a_dot_only_name():
    """'..' and '.' collapse to nothing under join_path, so the rename must be
    refused as empty rather than produce a path the tree cannot represent."""
    mapping, error = hierarchy.plan_rename("Arts", "..", {"Arts"}, True)

    assert mapping == {}
    assert error == (hierarchy.ERR_EMPTY_NAME, "Arts")


def test_plan_rename_accepts_a_name_containing_the_separator():
    """Renaming Mystery to Film.Noir CREATES a level -- the new name carries
    the node one level deeper, and that is legal as long as it is not its own
    ancestor."""
    mapping, error = hierarchy.plan_rename("Mystery", "Film.Noir", {"Mystery"}, True)

    assert error is None
    assert mapping == {"Mystery": "Film.Noir"}


def test_plan_rename_of_a_deeper_node_to_a_dotted_name_stays_legal():
    """History.Ancient renamed to East.Oldest becomes History.East.Oldest --
    the parent prefix survives, the new name may add depth."""
    mapping, error = hierarchy.plan_rename(
        "History.Ancient", "East.Oldest", {"History.Ancient"}, True)

    assert error is None
    assert mapping == {"History.Ancient": "History.East.Oldest"}
