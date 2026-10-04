# SPDX-License-Identifier: GPL-3.0-or-later
"""Classic admin saves retain scalar sort choices across display-ignore policy."""
import inspect
from types import SimpleNamespace

import flask
import pytest
from sqlalchemy.orm import sessionmaker

from tests.unit.test_custom_column_sort import sortable_library as shared_sortable_library

sortable_library = shared_sortable_library
pytestmark = pytest.mark.unit


@pytest.mark.parametrize("ignore", ["Internal.*", "("])
def test_actual_admin_save_keeps_valid_sort_configuration_when_columns_are_hidden(
        sortable_library, monkeypatch, ignore):
    from cps import admin, db, custom_column_sort
    from cps.config_sql import ConfigSQL
    actual_view = inspect.unwrap(admin.view_configuration)
    engine, _difficulty, _decoy = sortable_library
    db.CustomColumns.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(db.CustomColumns.__table__.insert(), [
            {"id": 12, "name": "Internal score", "datatype": "int", "is_multiple": False, "mark_for_delete": False},
            {"id": 13, "name": "Public pages", "datatype": "int", "is_multiple": False, "mark_for_delete": False},
            {"id": 5, "name": "Text", "datatype": "text", "is_multiple": False, "mark_for_delete": False},
        ])
    session = sessionmaker(bind=engine)()
    config = ConfigSQL.__new__(ConfigSQL)
    config.__dict__["dirty"] = []
    config.config_sortable_custom_columns = "12,13"
    config.config_columns_to_ignore = ""
    config.config_restricted_column = config.config_read_column = config.config_default_role = 0
    saved = []
    config.save = lambda: saved.append((config.config_sortable_custom_columns, config.config_columns_to_ignore))
    monkeypatch.setattr(admin, "config", config)
    monkeypatch.setattr(admin, "calibre_db", SimpleNamespace(session=session, speaking_language=lambda: []))
    monkeypatch.setattr(admin, "get_available_locale", lambda: [])
    monkeypatch.setattr(admin, "render_title_template", lambda _template, **context: context)
    monkeypatch.setattr(custom_column_sort, "calibre_db", SimpleNamespace(session=session))
    monkeypatch.setattr(admin, "before_request", lambda: None)
    monkeypatch.setattr(admin, "view_configuration", lambda: "saved configuration")
    monkeypatch.setattr(admin, "flash", lambda *_a, **_k: None)
    monkeypatch.setattr(admin, "_", lambda value, **kw: value % kw if kw else value)
    app = flask.Flask(__name__)
    app.secret_key = "fixture"
    def save(ignore_expression):
        with app.test_request_context("/admin/viewconfig", method="POST", data={
                "config_columns_to_ignore": ignore_expression,
                "config_sortable_custom_columns": ["12", "13", "5", "999"],
        }):
            assert inspect.unwrap(admin.update_view_configuration)() == "saved configuration"
    try:
        save(ignore)
        assert saved[-1] == ("12,13", ignore)
        with app.test_request_context("/admin/viewconfig"):
            assert {column.id for column in actual_view()["sortableColumns"]} == {12, 13}
        assert [column.id for column in custom_column_sort.load_configured_columns(config)] == (
            [13] if ignore != "(" else [])
        assert custom_column_sort.resolve_magic_shelf_sort("cc-12-asc", config).key == "new"
        save("")
        assert saved[-1] == ("12,13", "")
        assert {column.id for column in custom_column_sort.load_configured_columns(config)} == {12, 13}
        assert custom_column_sort.resolve_magic_shelf_sort("cc-12-asc", config).key == "cc-12-asc"
    finally:
        session.close()
