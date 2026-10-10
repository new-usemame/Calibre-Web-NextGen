# SPDX-License-Identifier: GPL-3.0-or-later
"""Classic rendered export keys must describe their actual fallback ordering."""
import csv
import inspect
import io
import json
from html.parser import HTMLParser
from pathlib import Path

import flask
import pytest
from jinja2 import FileSystemLoader

from tests.unit.test_custom_sort_export_merge import (
    classic_library as shared_classic_library,
    sortable_library as shared_sortable_library,
    export_library as shared_export_library,
)

classic_library = shared_classic_library
sortable_library = shared_sortable_library
export_library = shared_export_library

pytestmark = pytest.mark.unit


class ExportParams(HTMLParser):
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "section" and values.get("class") == "book-list-export":
            self.params = json.loads(values["data-params"])


@pytest.mark.parametrize("availability", ["hidden", "removed", "unconfigured", "outage", "available"])
def test_stored_custom_sort_render_and_export_use_effective_key(export_library, monkeypatch, availability):
    from cps import custom_column_sort, db, search, web
    from cps.api import books

    state = export_library
    stored = {"search": "cc-12-desc"}
    monkeypatch.setattr(state.user, "get_view_property", lambda page, _property: stored.get(page))
    monkeypatch.setattr(state.user, "set_view_property", lambda page, _property, value: stored.update({page: value}))
    # Undo the shared Classic fixture's definition stub: use real SQLite schema
    # metadata for both rendering and export, with only a transient outage seam.
    monkeypatch.setattr(custom_column_sort, "load_configured_columns", web.load_configured_columns)
    if availability == "hidden":
        state.library.config.config_columns_to_ignore = "Difficulty"
    elif availability == "removed":
        state.session.query(db.CustomColumns).filter(db.CustomColumns.id == 12).delete()
        state.session.commit()
    elif availability == "unconfigured":
        state.library.config.config_sortable_custom_columns = ""
    elif availability == "outage":
        monkeypatch.setattr(custom_column_sort, "load_configured_columns", lambda _config: None)
        monkeypatch.setattr(books, "load_configured_columns", lambda _config: None)

    order = web._sort_context("stored", "search")
    context = search.render_search_results("Book", offset=0, order=order, limit=20)
    rendered_ids = [row.Books.id for row in context["entries"]]
    state.app.jinja_loader = FileSystemLoader(Path(__file__).resolve().parents[2] / "cps/templates")
    state.app.add_url_rule("/api/v1/books/export", endpoint="api_v1.export_book_list", view_func=lambda: "")
    html = flask.render_template("_book_list_export.html", **context,
        current_user=state.user, _=lambda value: value, csrf_token=lambda: "fixture")
    parser = ExportParams()
    parser.feed(html)
    with state.app.test_request_context("/api/v1/books/export", method="POST",
            json={"source": "catalog", "format": "csv", "params": parser.params}):
        response = inspect.unwrap(books.export_book_list)()
        if availability == "outage":
            assert isinstance(response, tuple) and response[1] == 503
            assert parser.params["sort"] == "cc-12-desc"
            assert rendered_ids == [7, 6, 5, 4, 3, 2, 1]
        else:
            assert not isinstance(response, tuple), response
            try:
                rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
                assert [row[0] for row in rows[1:]] == [f"Book {i}" for i in rendered_ids]
                assert parser.params["sort"] == ("cc-12-desc" if availability == "available" else "new")
                assert rendered_ids == ([6, 2, 1, 4, 7, 5, 3] if availability == "available"
                                        else [7, 6, 5, 4, 3, 2, 1])
            finally:
                response.close()
    assert stored == {"search": "cc-12-desc"}
