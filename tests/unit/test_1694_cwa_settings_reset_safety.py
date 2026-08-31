# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for the destructive CWA Settings reset (#1694)."""

import inspect
import re
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import flask
import jinja2
import pytest
from lxml import html


REPO_ROOT = Path(__file__).resolve().parents[2]
CWA_SETTINGS_TEMPLATE = REPO_ROOT / "cps" / "templates" / "cwa_settings.html"
CWA_CSS = REPO_ROOT / "cps" / "static" / "css" / "cwa.css"
LAYOUT_TEMPLATE = REPO_ROOT / "cps" / "templates" / "layout.html"
TEMPLATE_DIR = REPO_ROOT / "cps" / "templates"


class _SettingsDB:
    defaults = {"auto_convert_target_format": "epub"}
    stored = {"auto_convert_target_format": "mobi"}
    reset_calls = []
    update_calls = []

    def __init__(self):
        self.cwa_default_settings = dict(self.defaults)
        self.cwa_settings = dict(self.stored)

    def get_cwa_settings(self):
        return dict(self.stored)

    def update_cwa_settings(self, settings):
        self.__class__.stored.update(settings)
        self.__class__.update_calls.append(dict(settings))

    def set_default_settings(self, force=False):
        self.__class__.stored = dict(self.defaults)
        self.__class__.reset_calls.append(force)

    def execute_write(self, _query, _params=()):
        return None


@pytest.fixture
def settings_client(monkeypatch):
    from cps import cwa_functions, schedule

    _SettingsDB.stored = {"auto_convert_target_format": "mobi"}
    _SettingsDB.reset_calls = []
    _SettingsDB.update_calls = []

    monkeypatch.setattr(cwa_functions, "CWA_DB", _SettingsDB)
    monkeypatch.setattr(cwa_functions, "INTEGER_SETTINGS", ())
    monkeypatch.setattr(cwa_functions, "FLOAT_SETTINGS", ())
    monkeypatch.setattr(cwa_functions, "JSON_SETTINGS", ())
    monkeypatch.setattr(cwa_functions, "_", lambda text, **_kwargs: text)
    monkeypatch.setattr(cwa_functions.config, "config_kobo_sync_magic_shelves", False, raising=False)
    monkeypatch.setattr(cwa_functions.config, "config_hardcover_sync", False, raising=False)
    monkeypatch.setattr(cwa_functions.config, "save", lambda: None)
    monkeypatch.setattr(cwa_functions.config, "resolved_hardcover_token", lambda: None)
    monkeypatch.setattr(schedule, "refresh_hardcover_auto_fetch", lambda: None)
    monkeypatch.setattr(cwa_functions, "get_next_duplicate_scan_run", lambda _settings: None)
    monkeypatch.setattr(
        cwa_functions,
        "render_title_template",
        lambda _template, **context: {"settings": context["cwa_settings"]},
    )

    app = flask.Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(cwa_functions.cwa_settings)
    app.view_functions["cwa_settings.set_cwa_settings"] = inspect.unwrap(
        cwa_functions.set_cwa_settings
    )
    return app.test_client()


def test_route_dispatches_stable_actions_without_english_labels(settings_client):
    reset_response = settings_client.post(
        "/cwa-settings",
        data={"settings_action": "reset"},
    )

    assert reset_response.status_code == 200
    assert _SettingsDB.reset_calls == [True]
    assert _SettingsDB.stored == _SettingsDB.defaults

    save_response = settings_client.post(
        "/cwa-settings",
        data={
            "settings_action": "save",
            "auto_convert_target_format": "azw3",
        },
    )

    assert save_response.status_code == 200
    assert _SettingsDB.update_calls[-1]["auto_convert_target_format"] == "azw3"
    assert _SettingsDB.stored["auto_convert_target_format"] == "azw3"


@pytest.mark.parametrize(
    ("legacy_label", "expected_target", "expected_reset_calls"),
    [
        ("Submit", "azw3", []),
        ("Apply Default Settings", "epub", [True]),
    ],
)
def test_route_keeps_cached_english_forms_working(
    settings_client, legacy_label, expected_target, expected_reset_calls
):
    response = settings_client.post(
        "/cwa-settings",
        data={
            "submit_button": legacy_label,
            "auto_convert_target_format": "azw3",
        },
    )

    assert response.status_code == 200
    assert _SettingsDB.stored["auto_convert_target_format"] == expected_target
    assert _SettingsDB.reset_calls == expected_reset_calls


def _css_declarations(source, selector):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", source):
        selectors = {item.strip() for item in match.group(1).split(",")}
        if selector not in selectors:
            continue
        return {
            name.strip(): value.strip()
            for declaration in match.group(2).split(";")
            if ":" in declaration
            for name, value in [declaration.split(":", 1)]
        }
    raise AssertionError(f"Missing CSS selector: {selector}")


def _rgb_chroma(color):
    if color.startswith("#"):
        value = color.removeprefix("#")
        assert len(value) == 6, f"Unsupported hex color: {color}"
        channels = tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))
    else:
        match = re.fullmatch(
            r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*[\d.]+)?\s*\)",
            color,
        )
        assert match, f"Unsupported CSS color: {color}"
        channels = tuple(int(channel) for channel in match.groups())
    return max(channels) - min(channels)


def _render_cwa_settings_template():
    def translate(message, **values):
        return message % values if values else message

    environment = jinja2.Environment(
        loader=jinja2.ChoiceLoader([
            jinja2.DictLoader({
                "layout.html": (
                    "{% block flash %}{% endblock %}"
                    "{% block header %}{% endblock %}"
                    "{% block body %}{% endblock %}"
                ),
            }),
            jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        ]),
        autoescape=True,
    )
    return environment.get_template("cwa_settings.html").render(
        _=translate,
        autoingest_options=(),
        config=SimpleNamespace(
            config_timezone="UTC",
            hardcover_sync_enabled=lambda: False,
        ),
        cwa_settings=defaultdict(lambda: False),
        hardcover_token_available=False,
        ignorable_formats=(),
        next_duplicate_scan_run=None,
        target_formats=(),
        title="CWA Settings",
        url_for=lambda endpoint: f"/{endpoint}",
    )


def _inline_declarations(element):
    return {
        name.strip(): value.strip()
        for declaration in element.get("style", "").split(";")
        if ":" in declaration
        for name, value in [declaration.split(":", 1)]
    }


def test_rendered_template_makes_save_default_and_keeps_it_rightmost():
    source = CWA_SETTINGS_TEMPLATE.read_text(encoding="utf-8")
    document = html.fromstring(_render_cwa_settings_template())
    form = document.xpath("//form")[0]
    action_row = form.xpath(
        './/div[contains(concat(" ", normalize-space(@class), " "), '
        '" cwa-settings-actions ")]'
    )[0]
    action_buttons = action_row.xpath('./button[@type="submit"]')
    dom_actions = [button.get("value") for button in action_buttons]

    assert dom_actions == ["save", "reset"], (
        "Save must be the first submit control in DOM order so implicit "
        "form submission cannot target Reset"
    )

    all_submit_controls = form.xpath(
        './/button[not(@type) or @type="submit"] | .//input[@type="submit"]'
    )
    assert all_submit_controls[0].get("value") == "save"

    row_style = _inline_declarations(action_row)
    assert row_style["display"] == "flex"
    css = CWA_CSS.read_text(encoding="utf-8")

    def flex_order(button):
        action_class = next(
            class_name
            for class_name in button.get("class", "").split()
            if class_name.startswith("cwa-settings-") and class_name.endswith("-action")
        )
        selector = f".cwa-settings-actions .{action_class}"
        return int(_css_declarations(css, selector)["order"])

    visual_actions = [
        button.get("value") for button in sorted(action_buttons, key=flex_order)
    ]
    assert visual_actions == ["reset", "save"], (
        "Reset must render left of the rightmost Save action"
    )

    assert "Reset every CWA setting to its default?" in source
    assert "All of your current CWA settings will be permanently lost." in source
    assert 'onclick="return confirm(this.dataset.confirm);"' in source
    assert "{{ _('Reset All CWA Settings') }}" in source
    assert "{{ _('Save') }}" in source


@pytest.mark.parametrize(
    ("theme_selector", "state_suffix"),
    [
        (None, ""),
        (None, "-interactive"),
        ("body.blur .cwa-settings-actions", ""),
        ("body.blur .cwa-settings-actions", "-interactive"),
    ],
)
def test_save_is_the_color_accent_in_both_themes(theme_selector, state_suffix):
    template = CWA_SETTINGS_TEMPLATE.read_text(encoding="utf-8")
    css = CWA_CSS.read_text(encoding="utf-8")
    layout = LAYOUT_TEMPLATE.read_text(encoding="utf-8")

    reset_tag = re.search(r'<button[^>]+value="reset"[^>]*>', template).group(0)
    save_tag = re.search(r'<button[^>]+value="save"[^>]*>', template).group(0)
    assert "cwa-settings-actions" in template
    assert "cwa-settings-reset-action" in reset_tag
    assert "cwa-settings-save-action" in save_tag
    assert "btn-default" not in reset_tag and "btn-primary" not in reset_tag
    assert "btn-default" not in save_tag and "btn-primary" not in save_tag

    # cwa.css is the final theme stylesheet, so these action-specific rules
    # are the declarations a browser resolves after caliBlur's global button
    # inversion. Compare the actual resolved colors, not Bootstrap class names.
    assert layout.index("css/caliBlur.css") < layout.index("css/cwa.css")
    variables = _css_declarations(css, ".cwa-settings-actions")
    if theme_selector:
        variables.update(_css_declarations(css, theme_selector))

    if state_suffix:
        reset_selector = ".cwa-settings-actions .cwa-settings-reset-action:hover"
        save_selector = ".cwa-settings-actions .cwa-settings-save-action:hover"
    else:
        reset_selector = ".cwa-settings-actions .cwa-settings-reset-action"
        save_selector = ".cwa-settings-actions .cwa-settings-save-action"

    reset_variable = f"--cwa-settings-reset-bg{state_suffix}"
    save_variable = f"--cwa-settings-save-bg{state_suffix}"
    assert _css_declarations(css, reset_selector)["background-color"] == f"var({reset_variable})"
    assert _css_declarations(css, save_selector)["background-color"] == f"var({save_variable})"

    reset_color = variables[reset_variable]
    save_color = variables[save_variable]
    assert _rgb_chroma(save_color) > _rgb_chroma(reset_color), (
        f"Save must remain the color accent: reset={reset_color}, save={save_color}"
    )
