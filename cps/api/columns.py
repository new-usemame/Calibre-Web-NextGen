# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Custom-column browse endpoints for /api/v1 — the JSON twin of the classic
``/custom_column/<id>[/<path>]`` views.

Every browsable column is in exactly one of two modes, decided by
``CalibreDB.is_flat_cc_column``:

* **hierarchical** — stored values form a real dotted hierarchy
  (``Computers`` and ``Computers.DB``). A node matches itself plus all of
  its descendants.
* **flat** — stored values only *contain* dots (Dewey ``778.3``, LCC
  ``QA76.76.C68``). Every value is an opaque atomic string, so the
  ``/tree`` endpoint returns a one-level list of whole values and the
  ``/books`` endpoint matches one value exactly, with no descendants.

Both modes share the node shape (``path == name == value``, ``children: []``
for flat), so the SPA renders one component with the ``hierarchical`` flag
choosing how to present it.

    GET /api/v1/columns                       -> browsable column list
    GET /api/v1/columns/<id>/tree             -> nodes (JSON)
    GET /api/v1/columns/<id>/books?path=...   -> books under one node

Per-user visibility mirrors the classic sidebar: a column the user disabled
via the profile page's "Show <column> Section" checkbox
(``User.view_settings``, ``cc_sidebar`` page, key ``show_cc_<id>``) is
omitted here too, and a hidden column id 404s — so the SPA and the classic
sidebar can never disagree on what is browsable, and neither can be used to
route around the other.
"""
from flask import abort, jsonify, request

from . import api_v1
from .books import _rows_to_items
from .. import calibre_db, config, constants, db, hierarchy
from ..cw_login import current_user
from ..custom_column_visibility import browsable_columns, is_cc_visible, retryable_column_reads
from ..sort_orders import book_sort_order
from ..usermanagement import login_required_if_no_ano

def _cc_disabled(col_id):
    return not is_cc_visible(current_user, col_id, fail_on_error=True)


def _visible_columns():
    """The browsable custom columns for the current user; failed reads are 503.

    Mirrors ``get_custom_column_sidebar_entries``: text/enumeration columns
    honouring the per-user ``show_cc_<id>`` toggle. The read-column and
    ``config_columns_to_ignore`` filtering come from ``get_cc_columns``.

    Every tag-like column is included regardless of hierarchy — whether it
    renders as a tree or a flat list is the *response's* business (the
    ``hierarchical`` flag), not this filter's. Filtering here is what made
    Dewey and LCC columns disappear from the SPA entirely.
    """
    items = []
    try:
        if not db.cc_classes:
            return items
        for col in browsable_columns(calibre_db.get_cc_columns(config, fail_on_error=True)):
            if _cc_disabled(col.id):
                continue
            items.append(col)
    except Exception:
        # This is the primary content of the browse surface, unlike optional
        # fields on a book detail. An unavailable read must remain retryable.
        abort(503, description="Custom-column definitions temporarily unavailable")
    return items


def _get_column_or_none(col_id):
    """The ``CustomColumns`` row for ``col_id`` when the caller may browse it."""
    for col in _visible_columns():
        if col.id == col_id:
            return col
    return None


def _not_found(message):
    return jsonify({"error": {"code": "not_found", "message": message}}), 404


def _may_browse_columns():
    """Whether the caller has the Categories section enabled.

    The classic route and the OPDS feed both check this; the API must too, or
    hiding Categories in the profile would be a UI-only change an API client
    could route straight past.
    """
    try:
        return current_user.check_visibility(constants.SIDEBAR_CATEGORY)
    except Exception:
        return False


@api_v1.route("/columns")
@login_required_if_no_ano
@retryable_column_reads
def list_columns():
    """Browsable custom columns with their hierarchy status."""
    if not _may_browse_columns():
        return jsonify({"items": []})
    return jsonify({"items": [{
        "id": col.id,
        "name": col.name,
        "datatype": col.datatype,
        # False means "values are atomic strings" — Dewey 778.3 is one
        # classification, not a 778 node with a 3 child. The SPA must know:
        # without this flag it cannot tell a flat column from a hierarchy.
        "hierarchical": not calibre_db.is_flat_cc_column(col.id, fail_on_error=True),
    } for col in _visible_columns()]})


def _node_to_json(node):
    """One node as the SPA's wire shape (recursive)."""
    return {
        "name": node["name"],
        "path": node["path"],
        "count": node["count"],
        "total_count": node["total_count"],
        "children": [_node_to_json(child) for child in node["children"]],
    }


@api_v1.route("/columns/<int:col_id>/tree")
@login_required_if_no_ano
@retryable_column_reads
def column_tree(col_id):
    """Every value of a column as nodes, honouring visibility filters.

    For a hierarchical column this is the nested tree. For a flat column it
    is a one-level list of whole values (``children`` always empty) — the
    same shape, so one renderer covers both, with ``hierarchical`` in the
    response telling it how to present them.
    """
    if not _may_browse_columns():
        return _not_found("Column not found")
    col = _get_column_or_none(col_id)
    if col is None:
        return _not_found("Column not found")
    is_hierarchical = not calibre_db.is_flat_cc_column(col_id, fail_on_error=True)
    nodes = (calibre_db.get_hierarchical_tree(col_id, fail_on_error=True) if is_hierarchical
             else calibre_db.get_cc_flat_list(col_id, fail_on_error=True))
    return jsonify({
        "column": {
            "id": col.id,
            "name": col.name,
            "datatype": col.datatype,
            "hierarchical": is_hierarchical,
        },
        "nodes": [_node_to_json(node) for node in nodes],
    })


@api_v1.route("/columns/<int:col_id>/books")
@login_required_if_no_ano
@retryable_column_reads
def column_books(col_id):
    """Books under one node of a custom column, paginated.

    ``?path=`` names the node. On a hierarchical column a node matches
    itself plus all descendants (the canonical dotted path, e.g.
    ``Computers.DB``). On a flat column it is one exact stored value — Dewey
    ``778.3`` matches the ``778.3`` rows and nothing else, with no prefix
    expansion. Without a path the endpoint lists every book carrying any
    value in the column.
    """
    if not _may_browse_columns():
        return _not_found("Column not found")
    col = _get_column_or_none(col_id)
    if col is None or col_id not in db.cc_classes:
        return _not_found("Column not found")

    page = max(1, request.args.get("page", 1, type=int))
    per_page = max(1, min(200, request.args.get(
        "per_page", config.config_books_per_page, type=int)))
    raw_path = request.args.get("path") or ""

    cc_rel = getattr(db.Books, 'custom_column_' + str(col_id))
    is_hierarchical = not calibre_db.is_flat_cc_column(col_id, fail_on_error=True)

    if is_hierarchical:
        # Normalise exactly the way web.py::render_cc_category and
        # opds.py::feed_cc_category do: segments are stripped and empty ones
        # collapse. A '/' is part of a stored value ("Photography.B/W",
        # "Software Development.C/C++"), NEVER a separator -- rewriting it to a
        # '.' here made such a node unresolvable, and the endpoint then 404'd a
        # value the classic UI lists fine. All three surfaces must agree, so
        # this stays a plain join_path with no substitution.
        path = hierarchy.join_path([raw_path]) if raw_path else ''
    else:
        # Flat values are atomic: a trailing dot, a doubled dot or a space
        # around one is part of the stored string, so it is used verbatim.
        path = raw_path

    if path:
        if is_hierarchical:
            node = hierarchy.get_node_by_path(
                calibre_db.get_hierarchical_tree(col_id, fail_on_error=True), path)
            if node is None:
                return _not_found("Category not found")
            db_filter = cc_rel.any(calibre_db.hierarchical_cc_filter(col_id, node))
        else:
            db_filter = cc_rel.any(calibre_db.flat_cc_filter(col_id, path))
    else:
        # No path: every book carrying any value in this column.
        db_filter = cc_rel.any()

    entries, _random, pagination = calibre_db.fill_indexpage(
        page, per_page, db.Books, db_filter, book_sort_order("new"),
        True, config.config_read_column)
    return jsonify({
        "items": _rows_to_items(entries),
        "page": page,
        "per_page": per_page,
        "total": pagination.total_count,
        "path": path,
        "column": {"id": col.id, "name": col.name},
    })
