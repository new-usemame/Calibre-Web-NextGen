# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#    Copyright (C) 2024 Calibre-Web contributors
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.

"""Helpers for Calibre-style hierarchical custom columns ("dot notation").

Values such as ``Computers.DB.Oracle`` stored in tag-like custom columns are
converted into nested tree dictionaries so they can be rendered as collapsible
trees, and a node matches itself plus all of its descendants.

This module intentionally has no Flask/SQLAlchemy dependencies so it stays
unit-testable in isolation.
"""

SEPARATOR = '.'


def split_path(path):
    """Split a dotted path into its non-empty, stripped components."""
    return [p.strip() for p in (path or '').split(SEPARATOR) if p.strip()]


def join_path(parts):
    """Join path components back into a canonical dotted path."""
    return SEPARATOR.join(split_path(SEPARATOR.join(parts)))


#: Sort sentinel for a value nobody has curated a position for. Larger than any
#: stored ``view_order``, so unordered siblings fall to the back *as a group* and
#: then sort alphabetically among themselves -- which is exactly today's
#: behaviour, and is what makes the sidecar inert until somebody reorders
#: something.
UNORDERED = float('inf')


def _order_key(order, node):
    """Sort key for one sibling: curated position first, then name.

    ``order`` is ``{path: view_order}`` for the whole column, passed in by the
    caller. This module cannot fetch it itself: the rules below are kept free of
    database imports on purpose (see the note further down), and
    ``test_the_rule_module_imports_nothing_from_the_web_stack`` enforces it.
    """
    if not order:
        return node['name'].lower()
    return (order.get(node['path'], UNORDERED), node['name'].lower())


def parse_tag_hierarchy(values, order=None):
    """Convert an iterable of dot-notation strings into a sorted list of
    top-level tree nodes.

    Each node looks like::

        {'name': 'DB', 'path': 'Computers.DB', 'count': 1,
         'total_count': 2, 'children': [...]}

    ``values`` holds plain strings or ``(book_id, value)`` pairs. ``count``
    counts direct hits on the exact value, ``total_count`` the distinct books
    in the whole subtree (a book filed under both ``X.Y`` and ``X.Y.Z`` counts
    once for ``X``). ``raw_values`` keeps the stored spellings that normalise
    to the node, so a lookup can match exactly what the tree counted.

    ``order`` is an optional ``{path: view_order}`` map -- the curated sibling
    order held in the app.db sidecar (``cps/cc_sidecar.py``). Siblings with a
    stored position sort by it; the rest sort alphabetically after them. Omit it
    and the result is byte-identical to the pre-sidecar behaviour, which is why
    an empty sidecar changes nothing.
    """
    roots = {}
    for index, item in enumerate(values):
        # Accept any 2-element SEQUENCE, not just tuple. SQLAlchemy 2.x returns
        # `Row` objects from Query.all(), and `isinstance(row, tuple)` is False
        # for them -- the check then fell through to the "bare string" branch and
        # handed the whole Row to split_path, which raised `AttributeError: split`
        # on every hierarchical column. Callers that build plain tuples still
        # work; callers that pass query rows now work too.
        if isinstance(item, (str, bytes)) or not hasattr(item, '__len__'):
            book, value = ('anon', index), item
        else:
            book, value = item[0], item[1]
        if not value:
            continue
        parts = split_path(value)
        node_map = roots
        path = ''
        for i, part in enumerate(parts):
            path = part if i == 0 else path + SEPARATOR + part
            node = node_map.setdefault(part, {'name': part, 'path': path,
                                              'id': None, 'count': 0,
                                              'children': {}, 'books': set(),
                                              'raw_values': set()})
            if i == len(parts) - 1:
                node['count'] += 1          # direct hits only
                node['books'].add(book)
                node['raw_values'].add(value)
            node_map = node['children']
    return _finalize(list(roots.values()), order)


def _finalize(nodes, order=None):
    """Recursively convert children dicts to sorted lists; aggregate counts."""
    nodes.sort(key=lambda n: _order_key(order, n))
    for node in nodes:
        node['children'] = _finalize(list(node['children'].values()), order)
        for child in node['children']:
            node['books'] |= child['books']
        node['total_count'] = len(node['books'])
    return nodes


def subtree_values(node):
    """Every stored value counted under ``node``, descendants included."""
    found, stack = set(), [node]
    while stack:
        current = stack.pop()
        found |= current['raw_values']
        stack.extend(current['children'])
    return sorted(found)


def get_node_by_path(tree, path):
    """Locate a node by its full dotted path, e.g. ``'Computers.DB'``.

    Returns ``None`` when any component of the path does not exist.
    """
    node_map = {n['name']: n for n in tree}
    node = None
    for part in split_path(path):
        node = node_map.get(part)
        if node is None:
            return None
        node_map = {n['name']: n for n in node['children']}
    return node


def breadcrumb_trail(path):
    """Return ``[(name, full_path), ...]`` ancestors including the node itself.

    >>> breadcrumb_trail('Computers.DB')
    [('Computers', 'Computers'), ('DB', 'Computers.DB')]
    """
    trail, acc = [], []
    for part in split_path(path):
        acc.append(part)
        trail.append((part, SEPARATOR.join(acc)))
    return trail


def add_bookless_nodes(nodes, values, order=None):
    """Insert values no visible book carries, as nodes whose counts are 0.

    A value row exists whether or not any book uses it -- "Create new" makes one
    deliberately -- but :func:`parse_tag_hierarchy` derives both ``count`` and
    ``total_count`` from distinct book keys, so a value fed to it has to be
    attributed to some book. These are therefore merged in afterwards, with
    empty book sets: the node appears (so it can be found and acted on) and every
    count stays honest.

    ``total_count`` is deliberately NOT propagated upwards. A subtree containing
    nothing visible must report 0, not 1.

    Sorts through :func:`_order_key` like every other sibling list, so a
    bookless node lands where its curated position says rather than being
    alphabetised into the middle of an ordering somebody set by hand.

    Mutates and returns ``nodes``.
    """
    for value in values:
        parts = split_path(value)
        if not parts:
            continue
        siblings = nodes
        path = ''
        for i, part in enumerate(parts):
            path = part if i == 0 else path + SEPARATOR + part
            existing = None
            for candidate in siblings:
                if candidate['name'] == part:
                    existing = candidate
                    break
            if existing is None:
                existing = {'name': part, 'path': path, 'id': None,
                            'count': 0, 'children': [],
                            'books': set(), 'raw_values': {value},
                            'total_count': 0}
                siblings.append(existing)
                siblings.sort(key=lambda n: _order_key(order, n))
            siblings = existing['children']
    return nodes


# --------------------------------------------------------------------------- #
# Node manipulation rules
# --------------------------------------------------------------------------- #
#
# Everything above answers "what does this tree look like". Everything below
# answers "what would happen if you renamed/moved/merged this node" -- and
# answers it with NO database, NO Flask and NO gettext.
#
# Why the split matters: the rules used to live inside
# ``cps/api/cc_values.py``, interleaved with the queries that gather their
# inputs, which meant (a) the classic Jinja UI had no way to reach them and (b)
# the React SPA re-implemented the cycle guard in TypeScript, so the two copies
# could disagree. These functions take the values they need AS ARGUMENTS, so
# every interface can call the same implementation:
#
#     hierarchy.plan_move(path, new_parent, raw_values_under_node)
#
# Errors are returned as ``(code, subject)`` tuples rather than as translated
# sentences, because this module must stay free of flask_babel. The caller
# translates the code -- that is the only difference between the API's refusal
# and the classic UI's.

# Refusal codes. Callers map these to a message; they are deliberately
# separate strings so a caller can localise or re-word without touching the
# rules themselves.
ERR_EMPTY_NAME = 'empty-name'
ERR_EMPTY_VALUE = 'empty-value'
ERR_INTO_ITSELF = 'into-itself'
ERR_ALREADY_THERE = 'already-there'
ERR_MISSING_SOURCE = 'missing-source'
ERR_SOURCE_HAS_CHILDREN = 'source-has-children'
ERR_MERGE_INTO_ITSELF = 'merge-into-itself'
ERR_MERGE_INTO_DESCENDANT = 'merge-into-descendant'
ERR_NO_NEIGHBOUR = 'no-neighbour'


def is_descendant(candidate, ancestor):
    """True when ``candidate`` is ``ancestor`` itself or lives beneath it.

    The empty ``ancestor`` means the top level, which every real path is under.
    The empty ``candidate`` is the top level itself and is under nothing.

    >>> is_descendant('History.Ancient', 'History')
    True
    >>> is_descendant('Historyics', 'History')
    False
    """
    candidate = join_path([candidate]) if candidate else ''
    ancestor = join_path([ancestor]) if ancestor else ''
    if not candidate:
        return False
    if not ancestor:
        return True
    return candidate == ancestor or candidate.startswith(ancestor + SEPARATOR)


def can_place_under(path, target_path):
    """May the node at ``path`` be re-parented onto ``target_path``?

    The cycle rule on its own, with no selection or existence checks: only the
    node itself and its own descendants are refused, so landing a deep node on
    its own ancestor stays legal. Mirrors ``canPasteInto`` in the SPA's
    ``ccPaste.ts``, which exists to disable a button and explain why -- this is
    the rule it is a copy of.
    """
    if not path:
        return False
    return not is_descendant(target_path, path)


def relocated_path(path, new_parent):
    """Where ``path`` would land if it were re-parented onto ``new_parent``.

    Only the destination is rebuilt: the node's own name is preserved, so
    ``History.Ancient.India`` under ``History`` becomes ``History.India``, a
    sibling of ``Ancient`` rather than a descendant of it.

    >>> relocated_path('History.Ancient.India', 'History')
    'History.India'
    >>> relocated_path('History.Ancient.India', '')
    'India'
    """
    path = join_path([path])
    parent = join_path([new_parent]) if (new_parent or '').strip() else ''
    parts = split_path(path)
    if not parts:
        return ''
    leaf = parts[-1]
    return join_path([parent, leaf] if parent else [leaf])


def plan_rename(path, new_name, raw_values_under, hierarchical):
    """Map every stored spelling under ``path`` to its renamed spelling.

    ``raw_values_under`` is the set of raw rows the node counts -- the caller
    gathers it (``cc_values._raw_values_under`` does, from the tree), because a
    node can be spelled several ways in the database and all of them must move.

    Returns ``(mapping, error)``. ``mapping`` is ``old -> new``; an interior
    rename rewrites the prefix on every descendant, so ``Arts`` -> ``Culture``
    yields ``Arts -> Culture``, ``Arts.Fiction -> Culture.Fiction`` and so on.
    ``error`` is ``None`` or ``(code, subject)``.

    A flat column has no prefix to rewrite, so its value is simply replaced --
    the dots inside a Dewey number are part of the value and must survive.
    """
    if not hierarchical:
        new_path = (new_name or '').strip()
        if not new_path:
            return {}, (ERR_EMPTY_VALUE, path)
        return {path: new_path}, None

    new_name = (new_name or '').strip()
    if not new_name:
        return {}, (ERR_EMPTY_NAME, path)
    parent = path.rsplit(SEPARATOR, 1)[0] if SEPARATOR in path else ''
    new_path = join_path([parent, new_name] if parent else [new_name])
    if not new_path:
        return {}, (ERR_EMPTY_NAME, path)
    # A node may not become its own ancestor: renaming Arts.Fiction to Arts
    # would put it inside itself, and nothing in the read path prevents that.
    if new_path == path or new_path.startswith(path + SEPARATOR):
        return {}, (ERR_INTO_ITSELF, path)
    return {raw: new_path + raw[len(path):] for raw in raw_values_under}, None


def plan_move(path, new_parent, raw_values_under):
    """Map every stored spelling under ``path`` to its relocated spelling.

    This is deliberately NOT :func:`plan_rename` with the leaf name passed
    through. That call rebuilds the destination from the CURRENT parent plus
    that name, which reproduces ``path`` exactly, trips the rename planner's own
    cycle guard, and yields an empty mapping -- so every move silently rewrote
    nothing and still answered 200.

    Returns ``(mapping, error, new_path)``. An empty ``mapping`` with no error
    means the node exists in the tree but owns no stored row.

    >>> plan_move('Arts.Fiction', 'History', {'Arts.Fiction'})
    ({'Arts.Fiction': 'History.Fiction'}, None, 'History.Fiction')
    """
    path = join_path([path])
    parent = join_path([new_parent]) if (new_parent or '').strip() else ''
    new_path = relocated_path(path, parent)
    if new_path == path:
        return {}, (ERR_ALREADY_THERE, path), new_path
    if new_path.startswith(path + SEPARATOR):
        return {}, (ERR_INTO_ITSELF, path), new_path
    return ({raw: new_path + raw[len(path):] for raw in raw_values_under},
            None, new_path)


def merge_sources(chosen, destination):
    """The values folded into ``destination`` when a merge is confirmed on it.

    The DESTINATION is the row the button was pressed on, so it is excluded
    from its own sources. Mirrors ``mergeSourcePaths`` in the SPA's
    ``ccMerge.ts``.
    """
    return [p for p in chosen if p != destination]


def plan_merge(sources, into, exists, has_children):
    """Which ``sources`` may be folded into ``into``.

    Only the SOURCES must be leaves. The destination may have sub-values:
    folding "Ancient Greece" into "Greece" when "Greece" has a child "Sparta"
    is well defined -- the source row is repointed at the survivor and the
    existing children are untouched. Refusing it would block a valid operation
    the server happily accepts.

    ``exists`` and ``has_children`` are predicates, because the rule is about
    the sources and each can differ; one boolean would forbid legitimate
    multi-source merges.

    Returns ``(sources_to_apply, error)``. An empty source list is a legitimate
    request's opposite: the caller sends at least one source, and the
    destination itself is never among them.
    """
    for path in sources:
        if not exists(path):
            return [], (ERR_MISSING_SOURCE, path)
        if has_children(path):
            return [], (ERR_SOURCE_HAS_CHILDREN, path)
        if path == into:
            return [], (ERR_MERGE_INTO_ITSELF, path)
        if into.startswith(path + SEPARATOR):
            return [], (ERR_MERGE_INTO_DESCENDANT, path)
    return list(sources), None


def plan_delete(paths, exists, has_children):
    """Which ``paths`` may be deleted.

    Leaves only -- a value with children is refused, because deleting it would
    silently change what its children mean. Delete the leaves, or rename/move
    the parent.

    Returns ``(paths_to_delete, error)``.
    """
    for path in paths:
        if not exists(path):
            return [], (ERR_MISSING_SOURCE, path)
        if has_children(path):
            return [], (ERR_SOURCE_HAS_CHILDREN, path)
    return list(paths), None


def plan_reorder(path, siblings, direction):
    """Where `path` should sit among `siblings`, for a one-place move.

    Reordering is NOT the other rules with different arguments: it changes **no
    stored value at all**. A node and its whole subtree keep their paths; only
    the order in which their siblings are listed changes. That is why there is
    no value mapping here, and why a rename and a move are invisible to it.

    `siblings` is the full ordered list of the node's siblings, as the tree
    already holds it -- same level only, never the whole column. `direction` is
    'up' or 'down' and moves exactly one place.

    Returns ``(ordered_sibling_paths, error)``. On success the list is every
    sibling in its new order, including `path` itself, which is what the caller
    writes back: giving every touched sibling an explicit position means the
    ordering stops depending on the alphabetical fallback for anything the user
    has actually arranged.
    """
    if path not in siblings:
        return list(siblings), (ERR_MISSING_SOURCE, path)
    index = siblings.index(path)
    target = index - 1 if direction == 'up' else index + 1
    if target < 0 or target >= len(siblings):
        return list(siblings), (ERR_NO_NEIGHBOUR, direction)
    reordered = list(siblings)
    reordered[index], reordered[target] = reordered[target], reordered[index]
    return reordered, None


def node_availability(node, siblings=None):
    """What can be done to this node, WITHOUT knowing what else is selected.

    Returns the wire shape both interfaces read:

        {'can_delete': bool, 'delete_reason': code|None,
         'can_merge_source': bool, 'merge_reason': code|None,
         'can_move_up': bool, 'move_up_reason': code|None,
         'can_move_down': bool, 'move_down_reason': code|None}

    These are the rules that depend only on the node itself, so a client never
    has to re-derive them. They are computed BY the rule functions above rather
    than by inspecting ``children`` directly, so a flag published here cannot
    disagree with what the endpoint would decide for the same value -- the test
    ``test_the_published_flags_are_the_rules_own_verdict`` holds them together.

    `siblings` is the node's own sibling list, **the same level only**. It is a
    parameter rather than something read off the node because sibling position
    is not a property of a node -- it is a property of its position in a list --
    so a node on its own cannot answer "can this move up". Both call sites
    (``cps/api/columns.py`` and ``cps/web.py``) already hold the list. Omit it
    and the move flags are ``False``, which is the safe direction: a client that
    cannot tell offers nothing.

    What is deliberately NOT here: whether a node may receive a moved value, or
    whether it may be a merge destination. Both depend on what the user has
    currently ticked or cut, which only the client knows. Move up/down is the
    opposite case and IS published: its answer turns only on the node's place
    among its siblings, so there was never a reason for a client to derive it.
    """
    path = node['path']
    leaf = not node['children']
    _, delete_error = plan_delete(
        [path], exists=lambda p: True,
        has_children=lambda p: not leaf)
    _, merge_error = plan_merge(
        [path], '', exists=lambda p: True,
        has_children=lambda p: not leaf)

    if siblings is None:
        up_error = down_error = (ERR_NO_NEIGHBOUR, 'unknown')
    else:
        _, up_error = plan_reorder(path, list(siblings), 'up')
        _, down_error = plan_reorder(path, list(siblings), 'down')

    return {
        'can_delete': delete_error is None,
        'delete_reason': None if delete_error is None else delete_error[0],
        'can_merge_source': merge_error is None,
        'merge_reason': None if merge_error is None else merge_error[0],
        'can_move_up': up_error is None,
        'move_up_reason': None if up_error is None else up_error[0],
        'can_move_down': down_error is None,
        'move_down_reason': None if down_error is None else down_error[0],
    }


def is_hierarchical_value_set(values):
    """True when a column's value set behaves as a hierarchy.

    Requires at least one value to be a proper prefix (value + separator) of
    another value, e.g. {'Computers', 'Computers.DB'}. Avoids false positives
    on dot-containing but flat value sets (Dewey '778.3', LCC 'QA76.76.C68').
    """
    vset = {v.strip() for v in values if v}
    # A value has a descendant exactly when some value's text up to one of its
    # separators is itself a stored value. One pass over each value instead of
    # comparing every pair keeps a column with thousands of values cheap.
    for other in vset:
        start = other.find(SEPARATOR)
        while start != -1:
            if other[:start] in vset:
                return True
            start = other.find(SEPARATOR, start + 1)
    return False
