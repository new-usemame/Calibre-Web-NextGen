# SPDX-License-Identifier: GPL-3.0-or-later
"""#1322: classic admin flows keep Boolean restriction values explicit and safe."""
from types import SimpleNamespace
import inspect

import pytest


def _config(allowed=(), denied=()):
    class Config:
        config_allowed_column_value = ",".join(allowed)
        config_denied_column_value = ",".join(denied)

        def list_allowed_column_values(self):
            return self.config_allowed_column_value.split(",") if self.config_allowed_column_value else []

        def list_denied_column_values(self):
            return self.config_denied_column_value.split(",") if self.config_denied_column_value else []

        def save(self):
            self.saved = True

    return Config()


@pytest.mark.unit
def test_global_boolean_modal_adds_canonical_state_and_rejects_invalid_without_saving(monkeypatch):
    from cps import admin, app

    conf = _config()
    conf.config_restricted_column = 6
    monkeypatch.setattr(admin, "config", conf)
    monkeypatch.setattr(admin, "restricted_column_datatype", lambda _column: "bool")

    with app.test_request_context("/ajax/addrestriction/1", method="POST",
                                  data={"add_element": "Yes", "submit_allow": ""}):
        response = inspect.unwrap(admin.add_restriction)(1, 0)
    assert response == ""
    assert conf.config_allowed_column_value == "true"
    assert conf.saved is True

    conf.saved = False
    with app.test_request_context("/ajax/addrestriction/1", method="POST",
                                  data={"add_element": "sometimes", "submit_deny": ""}):
        response, status = inspect.unwrap(admin.add_restriction)(1, 0)
    assert status == 400
    assert conf.config_denied_column_value == ""
    assert conf.saved is False

    conf.config_allowed_column_value = "true"
    with app.test_request_context("/ajax/editrestriction/1", method="POST",
                                  data={"id": "a0", "Element": "maybe"}):
        response, status = inspect.unwrap(admin.edit_restriction)(1, 0)
    assert status == 400
    assert conf.config_allowed_column_value == "true"
    assert conf.saved is False

    with app.test_request_context("/ajax/editrestriction/1", method="POST",
                                  data={"id": "a0", "Element": "Undefined"}):
        response = inspect.unwrap(admin.edit_restriction)(1, 0)
    assert response == ""
    assert conf.config_allowed_column_value == "undefined"


@pytest.mark.unit
def test_boolean_bulk_restriction_resolves_undefined_without_existing_column_rows(monkeypatch):
    from cps import admin

    monkeypatch.setattr(admin, "config", SimpleNamespace(config_restricted_column=6))
    monkeypatch.setattr(admin, "restricted_column_datatype", lambda _column: "bool")
    user = SimpleNamespace(allowed_column_value="true")
    updated = admin.prepare_tags(user, "add", "allowed_column_value", ["undefined"])
    assert updated == "true,undefined"
    user.allowed_column_value = updated
    assert admin.prepare_tags(user, "remove", "allowed_column_value", ["undefined"]) == "true"

    with pytest.raises(ValueError, match="Yes, No, or Undefined"):
        admin.prepare_tags(user, "add", "allowed_column_value", ["missing"])


@pytest.mark.unit
def test_bulk_boolean_restriction_rejects_invalid_batch_without_partial_user_updates(monkeypatch):
    from cps import admin, app

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *_args, **_kwargs):
            return self

        def all(self):
            return self.rows

    class Session:
        def query(self, *_args, **_kwargs):
            return Query([first, second])

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

    first = SimpleNamespace(id=1, allowed_column_value="true")
    second = SimpleNamespace(id=2, allowed_column_value="false")
    session = Session()
    monkeypatch.setattr(admin, "config", SimpleNamespace(
        config_restricted_column=6, config_anonbrowse=True))
    monkeypatch.setattr(admin, "restricted_column_datatype", lambda _column: "bool")
    monkeypatch.setattr(admin.ub, "session", session)
    monkeypatch.setattr(admin, "_", lambda value, **kwargs: value % kwargs if kwargs else value)

    with app.test_request_context("/ajax/editlistusers/allowed_column_value", method="POST",
                                  data={"pk[]": ["1", "2"], "value[]": ["undefined", "bogus"],
                                        "action": "add"}):
        response, status = inspect.unwrap(admin.edit_list_user)("allowed_column_value")

    assert status == 400
    assert first.allowed_column_value == "true"
    assert second.allowed_column_value == "false"
    assert not getattr(session, "committed", False)


@pytest.mark.unit
def test_switch_to_boolean_column_is_rejected_before_other_settings_change(monkeypatch):
    from cps import admin, app

    conf = _config(("catalogue",), ())
    conf.config_restricted_column = 4
    conf.config_calibre_web_title = "Existing title"
    conf.config_default_role = 0
    rendered = {}
    monkeypatch.setattr(admin, "config", conf)
    monkeypatch.setattr(admin, "restricted_column_datatype", lambda _column: "bool")
    monkeypatch.setattr(admin, "boolean_restrictions_compatible", lambda: False)
    def render(**kwargs):
        rendered.update(kwargs)
        return "rendered config"

    monkeypatch.setattr(admin, "view_configuration", render)
    monkeypatch.setattr(admin, "flash", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(admin, "_", lambda value, **kwargs: value % kwargs if kwargs else value)

    with app.test_request_context("/admin/viewconfig", method="POST", data={
        "config_restricted_column": "9",
        "config_calibre_web_title": "Should not be saved",
    }):
        response = inspect.unwrap(admin.update_view_configuration)()

    assert response == "rendered config"
    assert conf.config_calibre_web_title == "Existing title"
    assert conf.config_allowed_column_value == "catalogue"
    assert rendered["draft_config"].config_calibre_web_title == "Should not be saved"
    assert rendered["draft_config"].config_restricted_column == 9


@pytest.mark.unit
def test_boolean_column_switch_checks_every_global_and_user_restriction(monkeypatch):
    from cps import admin

    class Query:
        def all(self):
            return [("Yes,0", "undefined"), ("false", "No")]

    conf = _config(("1",), ("0",))
    monkeypatch.setattr(admin, "config", conf)
    monkeypatch.setattr(admin, "ub", SimpleNamespace(
        User=SimpleNamespace(allowed_column_value=object(), denied_column_value=object()),
        session=SimpleNamespace(query=lambda *_args: Query())))
    assert admin.boolean_restrictions_compatible() is True

    monkeypatch.setattr(admin, "ub", SimpleNamespace(
        User=SimpleNamespace(allowed_column_value=object(), denied_column_value=object()),
        session=SimpleNamespace(query=lambda *_args: SimpleNamespace(
            all=lambda: [("true", "catalogue")]))))
    assert admin.boolean_restrictions_compatible() is False


@pytest.mark.unit
def test_classic_restriction_modal_has_all_three_selectable_boolean_states():
    from cps import app

    with app.test_request_context("/admin/viewconfig"):
        old_gettext = app.jinja_env.globals.get("_")
        app.jinja_env.globals["_"] = lambda value: value
        try:
            html = app.jinja_env.from_string(
                "{% from 'modal_dialogs.html' import restrict_modal %}{{ restrict_modal(true) }}"
            ).render(restriction_is_bool=True)
        finally:
            if old_gettext is None:
                app.jinja_env.globals.pop("_", None)
            else:
                app.jinja_env.globals["_"] = old_gettext

    assert 'data-bool-mode="true"' in html
    assert 'value="true">Yes<' in html
    assert 'value="false">No<' in html
    assert 'value="undefined">Undefined<' in html


@pytest.mark.unit
def test_user_bulk_editor_offers_all_boolean_states_independent_of_existing_rows(monkeypatch):
    from cps import admin

    class Query:
        def join(self, *_args, **_kwargs): return self
        def filter(self, *_args, **_kwargs): return self
        def group_by(self, *_args, **_kwargs): return self
        def order_by(self, *_args, **_kwargs): return self
        def all(self): return []

    class Session:
        def query(self, *_args, **_kwargs): return Query()

    conf = SimpleNamespace(config_restricted_column=6, config_anonbrowse=True,
                           config_kobo_sync=False)
    monkeypatch.setattr(admin, "config", conf)
    monkeypatch.setattr(admin, "restricted_column_datatype", lambda _column: "bool")
    monkeypatch.setattr(admin, "calibre_db", SimpleNamespace(
        session=Session(), speaking_language=lambda: [], common_filters=lambda: True))
    monkeypatch.setattr(admin, "ub", SimpleNamespace(session=Session(), User=object))
    monkeypatch.setattr(admin, "current_user", SimpleNamespace(view_settings={}))
    monkeypatch.setattr(admin, "get_available_locale", lambda: [])
    monkeypatch.setattr(admin, "render_title_template", lambda _name, **context: context)

    context = inspect.unwrap(admin.edit_user_table)()
    assert [(choice.id, choice.name) for choice in context["custom_values"]] == [
        ("true", "Yes"), ("false", "No"), ("undefined", "Undefined"),
    ]
    assert context["restriction_is_bool"] is True
