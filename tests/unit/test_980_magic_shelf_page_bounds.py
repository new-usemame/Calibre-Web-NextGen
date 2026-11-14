# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#980: the smart shelf books endpoint bounds page and per_page.

The SPA now sends per_page (rows x measured columns) to /api/v1/magicshelf/<id>.
The endpoint took the value as given, so a hand-made request with a huge or a
negative per_page loaded the whole matching library in one response (SQLite
reads LIMIT -1 as "no limit"). It now uses the same 1..200 bound as the
author/series book lists, while select_all keeps its own explicit cap.
"""
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest


def _call(query_string, *, config_per_page=20):
    from cps.api import magicshelves as mod

    shelf = SimpleNamespace(id=17, name="Smart", icon="", is_public=0, is_system=False,
                            user_id=7, kobo_sync=False,
                            rules={"condition": "AND", "rules": []})
    user = SimpleNamespace(id=7, is_authenticated=True, is_anonymous=False,
                           role_admin=lambda: False, role_edit_shelfs=lambda: True,
                           role_share_shelfs=lambda: True, opds_only_shelves_sync=False)
    seen = {}

    def fill_indexpage(page, per_page, *_args, **kwargs):
        seen["page"], seen["per_page"] = page, per_page
        seen["ids_only"] = kwargs.get("ids_only")
        return [], None, SimpleNamespace(page=page, per_page=per_page, total_count=0)

    class _Query:
        def get(self, shelf_id):
            return shelf if shelf_id == shelf.id else None

    app = flask.Flask(__name__)
    app.add_url_rule("/api/v1/magicshelf/<int:shelf_id>", "magic-shelf-detail",
                     inspect.unwrap(mod.magic_shelf_books))
    with patch.object(mod, "current_user", user), \
         patch.object(mod, "ub", SimpleNamespace(
             session=SimpleNamespace(query=lambda _model: _Query()), MagicShelf=object)), \
         patch.object(mod, "config", SimpleNamespace(config_books_per_page=config_per_page,
                                                     config_read_column=0)), \
         patch.object(mod, "load_configured_columns", return_value=[]), \
         patch.object(mod, "resolve_magic_shelf_sort", return_value=SimpleNamespace(
             key="new", persistable=True, order_by=(), join=())), \
         patch.object(mod, "custom_sort_options", return_value=[]), \
         patch.object(mod.magic_shelf, "build_query_from_rules", return_value=True), \
         patch.object(mod.magic_shelf, "system_magic_shelf_display_name",
                      lambda value: value.name), \
         patch.object(mod.calibre_db, "fill_indexpage", side_effect=fill_indexpage), \
         patch.object(mod, "_rows_to_items", return_value=[]), \
         patch.object(mod, "_selection_response",
                      side_effect=lambda ids, total: flask.jsonify({"ids": ids})), \
         patch.object(mod, "_list_custom_column_data", return_value=({}, {})):
        response = app.test_client().get(f"/api/v1/magicshelf/{shelf.id}?{query_string}")
    assert response.status_code == 200, response.get_data(as_text=True)
    return seen, response.get_json()


@pytest.mark.unit
@pytest.mark.parametrize(("query", "expected_per_page"), [
    ("per_page=100000", 200),   # a hand-made huge page
    ("per_page=-1", 1),         # SQLite LIMIT -1 would mean "every match"
    ("per_page=0", 1),
    ("per_page=18", 18),        # what the SPA sends: rows x measured columns
    ("", 20),                   # no per_page: the server's configured size
])
def test_per_page_is_bounded(query, expected_per_page):
    seen, body = _call(query)
    assert seen["per_page"] == expected_per_page
    assert body["per_page"] == expected_per_page


@pytest.mark.unit
def test_page_below_one_reads_the_first_page():
    seen, _body = _call("page=-3&per_page=12")
    assert seen["page"] == 1


@pytest.mark.unit
def test_select_all_keeps_its_own_cap():
    from cps.api.books import MAX_SELECT_ALL_BOOKS

    seen, _body = _call("select_all=1&per_page=5")
    assert seen["per_page"] == MAX_SELECT_ALL_BOOKS + 1
    assert seen["ids_only"] is True
