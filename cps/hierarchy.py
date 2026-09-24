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
trees and filtered with prefix matching (a parent node matches itself plus all
of its descendants).

This module intentionally has no Flask/SQLAlchemy dependencies so it stays
unit-testable in isolation.
"""

SEPARATOR = '.'
ESCAPE_CHAR = '\\'


def split_path(path):
    """Split a dotted path into its non-empty, stripped components."""
    return [p.strip() for p in (path or '').split(SEPARATOR) if p.strip()]


def join_path(parts):
    """Join path components back into a canonical dotted path."""
    return SEPARATOR.join(split_path(SEPARATOR.join(parts)))


def parse_tag_hierarchy(values):
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
    """
    roots = {}
    for index, item in enumerate(values):
        book, value = item if isinstance(item, tuple) else (('anon', index), item)
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
    return _finalize(list(roots.values()))


def _finalize(nodes):
    """Recursively convert children dicts to sorted lists; aggregate counts."""
    nodes.sort(key=lambda n: n['name'].lower())
    for node in nodes:
        node['children'] = _finalize(list(node['children'].values()))
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


def escape_like(text):
    """Escape SQL LIKE wildcards so user-supplied paths match literally."""
    return (text.replace(ESCAPE_CHAR, ESCAPE_CHAR * 2)
                .replace('%', ESCAPE_CHAR + '%')
                .replace('_', ESCAPE_CHAR + '_'))


def like_pattern(path):
    """Build the escaped LIKE pattern matching a node and all descendants."""
    return escape_like(join_path(split_path(path))) + '.' + '%'


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
