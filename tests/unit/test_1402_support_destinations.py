"""Behavioral checks for configurable support destinations (#1402)."""

from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path


pytestmark = pytest.mark.unit


def test_support_policy_preserves_defaults_and_substitutes_host_link():
    from cps.services.support_policy import support_policy

    defaults = SimpleNamespace(
        config_show_project_support=True,
        config_support_url="",
        config_support_label="",
    )
    assert support_policy(defaults, is_admin=False, contact_support_label="Contact support") == {
        "show_project_links": True,
        "url": None,
        "label": None,
    }
    assert support_policy(defaults, is_admin=True, contact_support_label="Contact support") == {
        "show_project_links": True,
        "url": None,
        "label": None,
    }

    hosted = SimpleNamespace(
        config_show_project_support=False,
        config_support_url="https://club.example/support",
        config_support_label="Book club help",
    )
    assert support_policy(hosted, is_admin=False, contact_support_label="Contact support") == {
        "show_project_links": False,
        "url": "https://club.example/support",
        "label": "Book club help",
    }
    assert support_policy(hosted, is_admin=True, contact_support_label="Contact support")["show_project_links"]


def test_invalid_preexisting_support_destination_is_never_returned_as_a_link():
    from cps.services.support_policy import support_policy

    policy = support_policy(SimpleNamespace(
        config_show_project_support=False,
        config_support_url="javascript:alert(1)",
        config_support_label="Unsafe",
    ))
    assert policy == {"show_project_links": False, "url": None, "label": None}


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "//example.com/help", "https://user:pass@example.com/help",
    "https://example.com/\nhelp", "https://example.com/" + "x" * 2048,
])
def test_support_destination_rejects_unsafe_or_oversized_urls(url):
    from cps.services.support_policy import validate_support_settings

    with pytest.raises(ValueError):
        validate_support_settings(url, "Contact support")


@pytest.mark.parametrize("label", ["x" * 81, "Help\nnow"])
def test_support_label_rejects_overlong_or_control_text(label):
    from cps.services.support_policy import validate_support_settings

    with pytest.raises(ValueError):
        validate_support_settings("https://club.example/help", label)


@pytest.mark.parametrize(("url", "label"), [
    ("https://club.example/\u0080help", "Help"),
    ("\nhttps://club.example/help", "Help"),
    ("https://club.example/help", "Help\u009fdesk"),
])
def test_support_settings_reject_unicode_and_boundary_control_characters(url, label):
    from cps.services.support_policy import validate_support_settings

    with pytest.raises(ValueError):
        validate_support_settings(url, label)


def test_support_settings_allow_internationalized_and_rtl_labels():
    from cps.services.support_policy import validate_support_settings

    assert validate_support_settings("https://club.example/support", "الدعم متاح") == (
        "https://club.example/support", "الدعم متاح",
    )


def _prepare_admin_save(monkeypatch):
    import cps.admin as admin

    saved = []
    settings = SimpleNamespace(
        config_calibre_web_title="Existing title",
        config_default_role=0,
        config_default_show=0,
        config_show_project_support=True,
        config_support_url="",
        config_support_label="",
        save=lambda: saved.append(True),
    )
    monkeypatch.setattr(admin, "config", settings)
    monkeypatch.setattr(admin, "_config_string", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(admin, "_config_int", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(admin, "persist_configured_columns", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(admin, "load_eligible_columns", lambda: ())
    monkeypatch.setattr(admin, "check_valid_read_column", lambda *_args: True)
    monkeypatch.setattr(admin, "check_valid_restricted_column", lambda *_args: True)
    monkeypatch.setattr(admin, "flash", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(admin, "_", lambda value, **kwargs: value % kwargs if kwargs else value)
    monkeypatch.setattr(admin, "before_request", lambda: None)
    monkeypatch.setattr(admin, "view_configuration", lambda **kwargs: "rendered-config")
    return admin, settings, saved


def test_invalid_support_form_is_rejected_before_any_other_setting_mutates(monkeypatch):
    import inspect
    from cps import app

    admin, settings, saved = _prepare_admin_save(monkeypatch)
    changed = []
    monkeypatch.setattr(admin, "_config_string", lambda data, key: changed.append(key))
    handler = inspect.unwrap(admin.update_view_configuration)
    with app.test_request_context("/admin/viewconfig", method="POST", data={
        "support_settings_present": "1",
        "config_support_url": "javascript:alert(1)",
        "config_support_label": "Help",
        "config_calibre_web_title": "Should not save",
    }):
        assert handler() == "rendered-config"

    assert changed == []
    assert saved == []
    assert settings.config_calibre_web_title == "Existing title"
    assert settings.config_support_url == ""


def test_partial_classic_post_preserves_support_settings_and_full_form_can_hide_links(monkeypatch):
    import inspect
    from cps import app

    admin, settings, saved = _prepare_admin_save(monkeypatch)
    settings.config_show_project_support = False
    settings.config_support_url = "https://club.example/help"
    settings.config_support_label = "Club help"
    handler = inspect.unwrap(admin.update_view_configuration)
    with app.test_request_context("/admin/viewconfig", method="POST", data={
        "config_calibre_web_title": "Partial save",
    }):
        assert handler() == "rendered-config"
    assert (settings.config_show_project_support, settings.config_support_url, settings.config_support_label) == (
        False, "https://club.example/help", "Club help",
    )

    with app.test_request_context("/admin/viewconfig", method="POST", data={
        "support_settings_present": "1",
        "config_support_url": "https://club.example/support",
        "config_support_label": "",
    }):
        assert handler() == "rendered-config"
    assert settings.config_show_project_support is False
    assert settings.config_support_url == "https://club.example/support"
    assert settings.config_support_label == ""
    assert len(saved) == 2


def test_auth_me_payload_exposes_policy_for_reader_and_keeps_project_links_for_admin(monkeypatch):
    import cps.api.auth as auth
    import cps.user_library as user_library
    from cps import services

    settings = SimpleNamespace(
        config_show_project_support=False,
        config_support_url="https://club.example/help",
        config_support_label="Club support",
        config_books_per_page=60,
        config_random_books=4,
    )
    monkeypatch.setattr(auth, "config", settings)
    monkeypatch.setattr(auth, "serialize_user", lambda _user: {"name": "reader"})
    monkeypatch.setattr(auth, "_server_features", lambda: {})
    monkeypatch.setattr(auth, "_instance_name", lambda: "Books")
    monkeypatch.setattr(auth, "_user_avatar", lambda _name: None)
    monkeypatch.setattr(user_library, "mark_response_user_specific", lambda: None)
    monkeypatch.setattr(services.acquisition.admission, "instance_enabled", lambda _path: False)
    monkeypatch.setattr(services.acquisition.admission, "account_allowed", lambda _path, _id: False)
    reader = SimpleNamespace(
        id=7, name="reader", view_settings={}, is_authenticated=True, is_anonymous=False,
        role_admin=lambda: False,
    )
    admin = SimpleNamespace(
        id=1, name="admin", view_settings={}, is_authenticated=True, is_anonymous=False,
        role_admin=lambda: True,
    )
    assert auth._me_payload(reader)["support"] == {
        "show_project_links": False,
        "url": "https://club.example/help",
        "label": "Club support",
    }
    assert auth._me_payload(admin)["support"] == {
        "show_project_links": True,
        "url": None,
        "label": None,
    }


@pytest.mark.parametrize("compact", [False, True])
def test_classic_support_link_renders_project_or_host_destination(compact):
    from cps.services.support_policy import support_policy

    template_dir = Path(__file__).resolve().parents[2] / "cps" / "templates"
    environment = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
    )
    environment.globals["_"] = lambda value: value
    template = environment.from_string(
        "{% from 'support_links.html' import support_link %}"
        "{{ support_link(support_destinations, compact=compact) }}"
    )
    hosted = support_policy(SimpleNamespace(
        config_show_project_support=False,
        config_support_url="https://club.example/help?a=1&b=2",
        config_support_label="Club <Help>",
    ), contact_support_label="Contact support")
    rendered = template.render(support_destinations=hosted, compact=compact)
    assert 'href="https://club.example/help?a=1&amp;b=2"' in rendered
    assert "Club &lt;Help&gt;" in rendered
    assert "ko-fi.com/calibrewebnextgen" not in rendered
    assert ('data-text="Support"' in rendered) is compact

    no_replacement = support_policy(SimpleNamespace(
        config_show_project_support=False, config_support_url="", config_support_label=""
    ))
    assert template.render(support_destinations=no_replacement, compact=compact).strip() == ""

    defaults = support_policy(SimpleNamespace(
        config_show_project_support=True,
        config_support_url="https://club.example/hidden-while-defaults-are-on",
        config_support_label="Host",
    ))
    project_link = template.render(support_destinations=defaults, compact=compact)
    assert "href=\"https://ko-fi.com/calibrewebnextgen\"" in project_link
    assert "hidden-while-defaults-are-on" not in project_link


def test_support_configuration_migration_is_idempotent_and_preserves_legacy_row():
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import Session
    from cps.ub import migrate_config_table

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE settings (id INTEGER PRIMARY KEY, config_calibre_web_title VARCHAR)"))
        connection.execute(text("INSERT INTO settings (id, config_calibre_web_title) VALUES (1, 'Club library')"))

    # The same upgrade entry point runs during startup. Re-running must leave
    # the legacy row readable and all three new settings installed exactly once.
    with Session(engine) as session:
        migrate_config_table(engine, session)
        migrate_config_table(engine, session)
    columns = {column["name"] for column in inspect(engine).get_columns("settings")}
    assert {"config_show_project_support", "config_support_url", "config_support_label"} <= columns
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT config_calibre_web_title, config_show_project_support, config_support_url, config_support_label "
            "FROM settings WHERE id = 1"
        )).one()
    assert row == ("Club library", 1, "", "")
