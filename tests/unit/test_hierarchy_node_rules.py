# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the node-manipulation rules in cps/hierarchy.py.

These are the rename / move / merge / delete rules, tested with NO Flask, NO
SQLAlchemy and no database at all -- which is the point of moving them here.
``tests/unit/test_cc_value_bookless.py`` still covers them the long way round,
through the real tree query and the API adapters; this file pins the rules
themselves, so a break shows up as a rule break rather than as an endpoint
break.

The tree modelled throughout:

    TestNode
    TestNode.TestSubNode
    TestNode.TestSubNode.TestDeepNode
    History
    History.AncientHistory
    History.AncientHistory.India
    Other

Deliberately three deep, because every bug worth catching lives in the
difference between "this node", "its parent" and "its own ancestor".
"""
import ast
import os

import pytest

from cps import hierarchy


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

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
def tree():
    return hierarchy.parse_tag_hierarchy([(1, v) for v in CUT_VALUES])


@pytest.fixture()
def leaves(tree):
    """The ``(exists, has_children)`` pair the rule functions take.

    Same shape as ``cc_values._leaves_of`` builds from a live tree -- spelled
    out here so these tests do not import the API module, which would drag in
    Flask for what is a pure-rules check.
    """
    def exists(path):
        return hierarchy.get_node_by_path(tree, path) is not None

    def has_children(path):
        node = hierarchy.get_node_by_path(tree, path)
        return bool(node and node['children'])

    return exists, has_children


# --------------------------------------------------------------------------- #
# is_descendant / can_place_under
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_a_node_is_a_descendant_of_itself():
    assert hierarchy.is_descendant('History', 'History')


@pytest.mark.unit
def test_everything_is_a_descendant_of_the_top_level():
    assert hierarchy.is_descendant('History.Ancient.India', '')


@pytest.mark.unit
def test_the_top_level_is_a_descendant_of_nothing():
    assert not hierarchy.is_descendant('', '')


@pytest.mark.unit
def test_a_shared_prefix_is_not_a_descendant():
    """'Historyics' merely STARTS with 'History'; it is a sibling, not a child."""
    assert not hierarchy.is_descendant('Historyics', 'History')
    assert not hierarchy.is_descendant('History', 'Historyics')


@pytest.mark.unit
def test_a_node_may_be_placed_under_its_own_ancestor():
    """The reported case: moving History.AncientHistory.India under History.

    That yields History.India -- a SIBLING of AncientHistory, not a descendant
    of it -- so there is no cycle and the move is legal. Only the node itself
    and its own descendants are refused.
    """
    assert hierarchy.can_place_under('History.AncientHistory.India', 'History')


@pytest.mark.unit
def test_a_node_may_not_be_placed_inside_itself():
    assert not hierarchy.can_place_under('History', 'History')


@pytest.mark.unit
def test_a_node_may_not_be_placed_inside_its_own_descendant_at_depth():
    assert not hierarchy.can_place_under(
        'TestNode', 'TestNode.TestSubNode.TestDeepNode')


@pytest.mark.unit
def test_the_top_level_is_always_a_valid_destination():
    assert hierarchy.can_place_under('History.AncientHistory.India', '')


@pytest.mark.unit
def test_nothing_can_be_moved_when_nothing_was_cut():
    assert not hierarchy.can_place_under('', 'History')


# --------------------------------------------------------------------------- #
# relocated_path
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_relocating_rebuilds_only_the_destination():
    assert hierarchy.relocated_path(
        'History.AncientHistory.India', 'History') == 'History.India'


@pytest.mark.unit
def test_relocating_to_the_top_level_drops_every_parent():
    assert hierarchy.relocated_path(
        'History.AncientHistory.India', '') == 'India'


@pytest.mark.unit
def test_relocating_a_blank_destination_is_the_top_level():
    assert hierarchy.relocated_path('History.Ancient', '   ') == 'Ancient'


@pytest.mark.unit
def test_relocating_normalises_the_destination():
    assert hierarchy.relocated_path('India', ' History . Ancient ') == \
        'History.Ancient.India'


# --------------------------------------------------------------------------- #
# plan_rename
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_renaming_an_interior_node_rewrites_the_prefix_on_every_descendant():
    mapping, error = hierarchy.plan_rename(
        'History', 'Chronicle', {'History', 'History.Ancient', 'History.Ancient.India'},
        True)

    assert error is None
    assert mapping == {
        'History': 'Chronicle',
        'History.Ancient': 'Chronicle.Ancient',
        'History.Ancient.India': 'Chronicle.Ancient.India',
    }


@pytest.mark.unit
def test_renaming_a_leaf_leaves_its_ancestors_alone():
    mapping, error = hierarchy.plan_rename(
        'History.Ancient.India', 'Bharat', {'History.Ancient.India'}, True)

    assert error is None
    assert mapping == {'History.Ancient.India': 'History.Ancient.Bharat'}


@pytest.mark.unit
def test_a_node_may_not_be_renamed_into_its_own_subtree():
    """Renaming History.Ancient to 'Ancient.India' would make it a CHILD of
    itself, and nothing in the read path prevents that.

    The plain case -- renaming it to 'History' -- is not a cycle at all: it
    lands on History.History, a sibling of Ancient. The guard has to compare
    the rebuilt path against the node it started from, not against its parent.
    """
    mapping, error = hierarchy.plan_rename(
        'History.Ancient', 'Ancient.India', {'History.Ancient'}, True)

    assert mapping == {}
    assert error == (hierarchy.ERR_INTO_ITSELF, 'History.Ancient')


@pytest.mark.unit
def test_renaming_a_node_to_its_parents_name_is_not_a_cycle():
    mapping, error = hierarchy.plan_rename(
        'History.Ancient', 'History', {'History.Ancient'}, True)

    assert error is None
    assert mapping == {'History.Ancient': 'History.History'}


@pytest.mark.unit
def test_an_empty_rename_name_is_refused():
    _, error = hierarchy.plan_rename('History', '  ', {'History'}, True)
    assert error == (hierarchy.ERR_EMPTY_NAME, 'History')


@pytest.mark.unit
def test_a_flat_value_is_replaced_whole_and_keeps_its_dots():
    """Dewey 778.3 has a dot but no hierarchy, so renaming it must not try to
    rewrite a prefix -- and must not split the number either."""
    mapping, error = hierarchy.plan_rename('778.3', '778.30', {'778.3'}, False)

    assert error is None
    assert mapping == {'778.3': '778.30'}


@pytest.mark.unit
def test_a_blank_flat_value_is_refused():
    _, error = hierarchy.plan_rename('778.3', '', {'778.3'}, False)
    assert error == (hierarchy.ERR_EMPTY_VALUE, '778.3')


# --------------------------------------------------------------------------- #
# plan_move
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_moving_an_interior_node_relocates_its_whole_branch(tree):
    """Regression: cut 'Adobe' and paste it under 'software' did nothing.

    The old handler called the RENAME planner with the node's own leaf name.
    That rebuilds the destination from the CURRENT parent plus the name given,
    so it reconstructed the path exactly, tripped its own cycle guard and
    returned an empty mapping -- the caller discarded the error, the
    transaction committed nothing, and the endpoint answered 200 having done
    nothing at all.

    plan_move is deliberately a different function rather than a rename with
    different arguments, so this cannot recur.
    """
    node = hierarchy.get_node_by_path(tree, 'TestNode.TestSubNode')
    mapping, error, new_path = hierarchy.plan_move(
        'TestNode.TestSubNode', 'Other', set(hierarchy.subtree_values(node)))

    assert error is None
    assert new_path == 'Other.TestSubNode'
    assert mapping, "a branch move must rewrite something, not silently do nothing"
    assert mapping == {
        'TestNode.TestSubNode': 'Other.TestSubNode',
        'TestNode.TestSubNode.TestDeepNode': 'Other.TestSubNode.TestDeepNode',
    }


@pytest.mark.unit
def test_moving_a_leaf_moves_only_that_leaf(tree):
    node = hierarchy.get_node_by_path(tree, 'History.AncientHistory.India')
    mapping, error, new_path = hierarchy.plan_move(
        'History.AncientHistory.India', 'Other', set(hierarchy.subtree_values(node)))

    assert error is None
    assert new_path == 'Other.India'
    assert mapping == {'History.AncientHistory.India': 'Other.India'}


@pytest.mark.unit
def test_moving_a_node_does_not_drag_its_ancestors_along(tree):
    """The reported suspicion: cutting the bottom node copied the top level
    too. Only spellings UNDER the cut node may appear in the mapping."""
    node = hierarchy.get_node_by_path(tree, 'History.AncientHistory.India')
    mapping, _, _ = hierarchy.plan_move(
        'History.AncientHistory.India', 'Other', set(hierarchy.subtree_values(node)))

    assert set(mapping) == {'History.AncientHistory.India'}
    for ancestor in ('History', 'History.AncientHistory'):
        assert ancestor not in mapping, ancestor
        assert ancestor not in mapping.values(), ancestor


@pytest.mark.unit
def test_a_branch_move_preserves_every_leaf_name(tree):
    """The property that makes a branch move reversible by hand: only the
    prefix changes, so the node stays grouped instead of scattering."""
    node = hierarchy.get_node_by_path(tree, 'TestNode.TestSubNode')
    mapping, _, _ = hierarchy.plan_move(
        'TestNode.TestSubNode', 'Other', set(hierarchy.subtree_values(node)))

    for old, new in mapping.items():
        assert old.split('.')[-1] == new.split('.')[-1], (old, new)


@pytest.mark.unit
def test_moving_a_root_onto_the_top_level_is_a_no_op(tree):
    mapping, error, new_path = hierarchy.plan_move(
        'TestNode', '', {'TestNode'})

    assert mapping == {}
    assert new_path == 'TestNode'
    assert error == (hierarchy.ERR_ALREADY_THERE, 'TestNode')


@pytest.mark.unit
def test_pasting_back_onto_the_same_parent_is_a_no_op(tree):
    mapping, error, new_path = hierarchy.plan_move(
        'TestNode.TestSubNode', 'TestNode', {'TestNode.TestSubNode'})

    assert mapping == {}
    assert new_path == 'TestNode.TestSubNode'
    assert error == (hierarchy.ERR_ALREADY_THERE, 'TestNode.TestSubNode')


@pytest.mark.unit
def test_a_node_cannot_be_moved_inside_its_own_subtree(tree):
    mapping, error, _ = hierarchy.plan_move(
        'TestNode', 'TestNode.TestSubNode.TestDeepNode', {'TestNode'})

    assert mapping == {}
    assert error == (hierarchy.ERR_INTO_ITSELF, 'TestNode')


@pytest.mark.unit
def test_a_node_with_no_stored_row_moves_nothing_but_is_not_an_error(tree):
    """The node exists in the tree yet owns no row ('Create new' makes those).
    An empty mapping with no error is how the endpoint tells 'nothing to do'
    apart from 'refused'."""
    mapping, error, new_path = hierarchy.plan_move('TestNode', 'Other', set())

    assert mapping == {}
    assert error is None
    assert new_path == 'Other.TestNode'


# --------------------------------------------------------------------------- #
# merge_sources
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_the_merge_destination_is_never_its_own_source():
    assert hierarchy.merge_sources(
        ['Arts.Mystery', 'Arts.Thriller'], 'Arts.Thriller') == ['Arts.Mystery']


@pytest.mark.unit
def test_merging_keeps_every_other_ticked_value_as_a_source():
    assert hierarchy.merge_sources(
        ['A', 'B', 'C'], 'B') == ['A', 'C']


# --------------------------------------------------------------------------- #
# plan_merge
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_leaves_merge_into_a_leaf(leaves):
    keep, error = hierarchy.plan_merge(
        ['History.AncientHistory.India', 'Other'], 'History.AncientHistory', *leaves)

    assert error is None
    assert keep == ['History.AncientHistory.India', 'Other']


@pytest.mark.unit
def test_a_single_source_is_a_legitimate_merge(leaves):
    """The editor's per-row Merge button designates the survivor by clicking
    its own row, so ticking exactly two values sends ONE source."""
    keep, error = hierarchy.plan_merge(
        ['Other'], 'History.AncientHistory.India', *leaves)

    assert error is None
    assert keep == ['Other']


@pytest.mark.unit
def test_a_destination_with_sub_values_is_still_a_valid_target(leaves):
    """Folding into 'History' when 'History' has children is well defined: the
    source is repointed at the survivor and the existing children are
    untouched. Refusing it would block an operation the server accepts."""
    keep, error = hierarchy.plan_merge(['Other'], 'History', *leaves)

    assert error is None
    assert keep == ['Other']


@pytest.mark.unit
def test_a_source_with_sub_values_cannot_be_merged(leaves):
    """The children are independent rows, so folding the parent would leave
    them orphaned -- Move or Rename is the operation that means it."""
    keep, error = hierarchy.plan_merge(['History'], 'Other', *leaves)

    assert keep == []
    assert error == (hierarchy.ERR_SOURCE_HAS_CHILDREN, 'History')


@pytest.mark.unit
def test_a_value_cannot_be_merged_into_itself(leaves):
    keep, error = hierarchy.plan_merge(['Other'], 'Other', *leaves)

    assert keep == []
    assert error == (hierarchy.ERR_MERGE_INTO_ITSELF, 'Other')


@pytest.mark.unit
def test_a_value_cannot_be_merged_into_one_of_its_sub_values(leaves):
    """Reachable because the DESTINATION is not required to exist yet -- the
    merge route creates it. 'Other' is a leaf here, so the children rule does
    not fire first, but folding it into 'Other.Sub' would nest it inside
    itself."""
    keep, error = hierarchy.plan_merge(['Other'], 'Other.Sub', *leaves)

    assert keep == []
    assert error == (hierarchy.ERR_MERGE_INTO_DESCENDANT, 'Other')


@pytest.mark.unit
def test_the_children_rule_outranks_the_descendant_rule(leaves):
    """'History' has children, so the more specific complaint wins -- it names
    the operation the caller should actually use instead."""
    keep, error = hierarchy.plan_merge(['History'], 'History.Ancient', *leaves)

    assert keep == []
    assert error == (hierarchy.ERR_SOURCE_HAS_CHILDREN, 'History')


@pytest.mark.unit
def test_a_merge_stops_at_the_first_bad_source(leaves):
    """One interior node invalidates the whole request -- nothing is folded,
    because a partial merge would be impossible to reason about afterwards."""
    keep, error = hierarchy.plan_merge(
        ['Other', 'History'], 'TestNode', *leaves)

    assert keep == []
    assert error == (hierarchy.ERR_SOURCE_HAS_CHILDREN, 'History')


# --------------------------------------------------------------------------- #
# plan_delete
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_leaves_can_be_deleted(leaves):
    keep, error = hierarchy.plan_delete(
        ['Other', 'History.AncientHistory.India'], *leaves)

    assert error is None
    assert keep == ['Other', 'History.AncientHistory.India']


@pytest.mark.unit
def test_a_value_with_sub_values_cannot_be_deleted(leaves):
    """Deleting it would silently change what its children mean."""
    keep, error = hierarchy.plan_delete(['History'], *leaves)

    assert keep == []
    assert error == (hierarchy.ERR_SOURCE_HAS_CHILDREN, 'History')


@pytest.mark.unit
def test_deleting_a_value_that_no_longer_exists_is_refused(leaves):
    keep, error = hierarchy.plan_delete(['Ghost.Node'], *leaves)

    assert keep == []
    assert error == (hierarchy.ERR_MISSING_SOURCE, 'Ghost.Node')


@pytest.mark.unit
def test_a_merge_of_a_value_that_no_longer_exists_is_refused(leaves):
    _, error = hierarchy.plan_merge(['Ghost.Node'], 'Other', *leaves)

    assert error == (hierarchy.ERR_MISSING_SOURCE, 'Ghost.Node')


# --------------------------------------------------------------------------- #
# node_availability -- what the tree publishes to both interfaces
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_a_leaf_may_be_deleted_and_may_be_a_merge_source(tree):
    # Pinned exactly, deliberately. A new published flag has to come through
    # here on purpose: this is the wire contract both interfaces read, and a
    # silent addition is how a client ends up trusting a field nobody agreed to.
    #
    # `Other` is the only child of the tree root, so it has no sibling either way
    # -- hence both move flags refused with no sibling list to compare against.
    availability = hierarchy.node_availability(
        hierarchy.get_node_by_path(tree, 'Other'))

    assert availability == {
        'can_delete': True, 'delete_reason': None,
        'can_merge_source': True, 'merge_reason': None,
        'can_move_up': False, 'move_up_reason': hierarchy.ERR_NO_NEIGHBOUR,
        'can_move_down': False, 'move_down_reason': hierarchy.ERR_NO_NEIGHBOUR,
    }


@pytest.mark.unit
def test_a_value_with_sub_values_is_refused_both_operations(tree):
    availability = hierarchy.node_availability(
        hierarchy.get_node_by_path(tree, 'History'))

    assert availability['can_delete'] is False
    assert availability['delete_reason'] == hierarchy.ERR_SOURCE_HAS_CHILDREN
    assert availability['can_merge_source'] is False
    assert availability['merge_reason'] == hierarchy.ERR_SOURCE_HAS_CHILDREN
    # Having children does not block a move. A branch reorders exactly as a leaf
    # does, and coupling the two would quietly stop anyone moving a whole
    # subtree -- which is the most useful case of all.
    assert availability['can_move_up'] is False      # sole root: nowhere to go
    assert availability['can_move_down'] is False


@pytest.mark.unit
@pytest.mark.parametrize('path,expected', [
    ('TestNode', False),
    ('TestNode.TestSubNode', False),
    ('TestNode.TestSubNode.TestDeepNode', True),
    ('History', False),
    ('History.AncientHistory', False),
    ('History.AncientHistory.India', True),
    ('Other', True),
])
def test_the_published_flags_are_the_rules_own_verdict(tree, path, expected):
    """The flag published on a node must BE what the endpoint would decide.

    `node_availability` calls `plan_delete`/`plan_merge` rather than reading
    `children` itself, which is the point: if the wire flag and the rule ever
    disagree, a client that trusts the flag would offer an operation the server
    then refuses. This runs the real planners against the same node and demands
    the same answer.
    """
    node = hierarchy.get_node_by_path(tree, path)
    availability = hierarchy.node_availability(node)
    leaves = leaves_of(tree)

    deletable, delete_error = hierarchy.plan_delete([path], *leaves)
    sourceable, merge_error = hierarchy.plan_merge([path], 'Somewhere.Else', *leaves)

    assert availability['can_delete'] is (delete_error is None) is expected
    assert availability['can_merge_source'] is (merge_error is None) is expected
    if expected:
        assert deletable == [path] and sourceable == [path]


def leaves_of(tree):
    """The (exists, has_children) pair the rule functions take."""
    def exists(path):
        return hierarchy.get_node_by_path(tree, path) is not None

    def has_children(path):
        node = hierarchy.get_node_by_path(tree, path)
        return bool(node and node['children'])

    return exists, has_children


@pytest.mark.unit
def test_the_wire_payload_carries_the_flags():
    """`available` must actually reach the response.

    A rule that is computed but not serialised is a rule the client cannot read,
    and the client silently falls back to re-deriving it -- which is the
    duplication this was meant to remove.
    """
    import inspect
    from cps.api import columns

    source = inspect.getsource(columns._node_to_json)
    assert '"available"' in source, (
        "_node_to_json must publish hierarchy.node_availability(node)")
    assert 'node_availability' in source


# --------------------------------------------------------------------------- #
# The rules must stay free of the framework
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def _imported_modules():
    """Every module ``cps/hierarchy.py`` actually imports, resolved to a root.

    Parsed rather than grepped: a substring search would trip over this file's
    own prose about not importing Flask.
    """
    source = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          os.pardir, os.pardir, 'cps', 'hierarchy.py')
    with open(source, encoding='utf-8') as handle:
        tree = ast.parse(handle.read())

    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:          # a relative import -- part of the package
                roots.add('.')
            elif node.module:
                roots.add(node.module.split('.')[0])
    return roots


def test_the_rule_module_imports_nothing_from_the_web_stack():
    """hierarchy.py is loaded standalone by test_hierarchy.py, and it is the
    shared home the classic Jinja UI and the SPA are both meant to call.

    Importing Flask, SQLAlchemy or anything else from the package would make
    that impossible: the rules would need an application to be reasoned about.
    """
    roots = _imported_modules()
    for banned in ('flask', 'flask_babel', 'sqlalchemy', 'cps', '.', 'db'):
        assert banned not in roots, (
            "hierarchy.py must not import %r -- got %s from the module"
            % (banned, sorted(roots)))