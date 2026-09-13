# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Custom-column browse endpoints for /api/v1 (SPA parity with the classic UI).

The classic Jinja UI exposes tag-like custom columns (datatype text/enumeration)
as sidebar entries rendering a tree for hierarchical columns and a plain list
for flat ones (cps/web.py::cc_category_list). The React SPA had no equivalent —
see react_spa.md. This module is the JSON twin:

    GET /api/v1/columns                          -> browsable column list
    GET /api/v1/columns/<id>/tree                -> hierarchy tree (JSON nodes)
    GET /api/v1/columns/<id>/books?path=...      -> books under one node

Per-user visibility mirrors the classic sidebar: a column the user disabled via
the profile page's "Show <column> Section" checkbox (User.view_settings,
'cc_sidebar' page, key 'show_cc_<id>') is omitted here too, so the SPA and the
classic sidebar can never disagree on what is browsable.
"""
from flask import jsonify, request

from . import api_v1
from .books import _rows_to_items
from .. import calibre_db, config, db, hierarchy
from ..sort_orders import book_sort_order
from ..usermanagement import login_required_if_no_ano

# The datatypes that render as browsable tag-like lists/trees, mirroring
# cps/render_template.py::get_custom_column_sidebar_entries.
_BROWSABLE_DATATYPES = ('text', 'enumeration')


def _cc_disabled(col_id):
    """Whether the caller hid this column's section on their profile page."""
    from ..cw_login import current_user
    try:
        return current_user.get_view_property('cc_sidebar', 'show_cc_%d' % col_id) is False
    except Exception:
        return False


def _visible_columns():
    """The browsable custom columns for the current user, degrading to [].

    Mirrors get_custom_column_sidebar_entries: text/enumeration columns,
    honouring the per-user 'show_cc_<id>' toggle. The read-column and
    config_columns_to_ignore filtering come from get_cc_columns.
    """
    items = []
    try:
        if not db.cc_classes:
            return items
        for col in calibre_db.get_cc_columns(config):
            if col.datatype not in _BROWSABLE_DATATYPES:
                continue
            if _cc_disabled(col.id):
                continue
            items.append(col)
    except Exception:
        # Same degrade rule as get_cc_columns: an unreadable definitions table
        # must not take the browse surface down with a 500.
        return []
    return items


def _get_column_or_abort(col_id):
    """The CustomColumns row for col_id when the caller may browse it, else None."""
    for col in _visible_columns():
        if col.id == col_id:
            return col
    return None


def _not_found(message):
    return jsonify({"error": {"code": "not_found", "message": message}}), 404


@api_v1.route("/columns")
@login_required_if_no_ano
def list_columns():
    """Browsable custom columns with their hierarchy status."""
    hierarchical_ids = calibre_db.get_hierarchical_column_ids()
    items = [{
        "id": col.id,
        "name": col.name,
        "datatype": col.datatype,
        "hierarchical": col.id in hierarchical_ids,
    } for col in _visible_columns()]
    return jsonify({"items": items})


def _node_to_json(node):
    """One hierarchy tree node as the SPA's wire shape (recursive)."""
    return {
        "name": node["name"],
        "path": node["path"],
        "count": node["count"],
        "total_count": node["total_count"],
        "children": [_node_to_json(child) for child in node["children"]],
    }


@api_v1.route("/columns/<int:col_id>/tree")
@login_required_if_no_ano
def column_tree(col_id):
    """The full hierarchy tree for one column, honouring visibility filters."""
    col = _get_column_or_abort(col_id)
    if col is None:
        return _not_found("Column not found")
    tree = calibre_db.get_hierarchical_tree(col_id)
    return jsonify({
        "column": {
            "id": col.id,
            "name": col.name,
            "datatype": col.datatype,
            "hierarchical": True,
        },
        "nodes": [_node_to_json(node) for node in tree],
    })


@api_v1.route("/columns/<int:col_id>/books")
@login_required_if_no_ano
def column_books(col_id):
    """Books under one node of a custom column, paginated.

    ``?path=`` selects the node (dotted, e.g. ``Computers.DB``); a parent node
    matches itself plus all descendants via hierarchical_cc_filter. Without a
    path the endpoint lists every book carrying any value in the column.
    """
    col = _get_column_or_abort(col_id)
    if col is None or col_id not in db.cc_classes:
        return _not_found("Column not found")

    page = max(1, request.args.get("page", 1, type=int))
    per_page = max(1, min(200, request.args.get(
        "per_page", config.config_books_per_page, type=int)))
    raw_path = (request.args.get("path") or "").strip()
    # Normalise the same way web.py does: slashes are tolerated as separators,
    # empty segments collapse, and the canonical dotted path comes back.
    path = hierarchy.join_path([raw_path.replace('/', hierarchy.SEPARATOR)]) if raw_path else ''

    cc_rel = getattr(db.Books, 'custom_column_' + str(col_id))
    if path:
        node = hierarchy.get_node_by_path(
            calibre_db.get_hierarchical_tree(col_id), path)
        if node is None:
            return _not_found("Category not found")
        db_filter = cc_rel.any(calibre_db.hierarchical_cc_filter(col_id, path))
    else:
        # No path: every book carrying any value in this column.
        node = None
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