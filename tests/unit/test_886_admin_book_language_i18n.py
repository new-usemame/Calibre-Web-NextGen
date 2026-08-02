# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for #886: the admin settings form left the "all"
option of the default-book-language select untranslated.

Reported by @iroQuai on a Dutch session — every label on the admin settings
form was translated except that one option, which still read "Show All". The
account settings form, which offers the identical select, had it right. Two
copies of the same list, built independently, drifted.

The tests below pin the behaviour (the sentinel is translated, the dynamic
language names are not re-translated, the list survives an unreadable library)
and pin the structure that keeps the two forms from drifting apart again.
"""
import ast
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "cps" / "api"

# flask_babel is not initialised on the bare test app, so the module's gettext
# is patched — the same convention test_api_v1_account.py uses.
_DUTCH = {"Show All": "Alle talen"}


def _dutch(text):
    return _DUTCH.get(text, text)


def _library(*langs):
    """A stand-in for calibre_db whose speaking_language() returns languages
    whose .name is already localised (db.py sets it via get_language_name)."""
    rows = [SimpleNamespace(lang_code=code, name=name) for code, name in langs]
    return SimpleNamespace(speaking_language=lambda: rows)


class _Locale:
    """Stand-in for a babel Locale: str() is the tag, display_name is native."""

    def __init__(self, tag, display_name):
        self.tag = tag
        self.display_name = display_name

    def __str__(self):
        return self.tag


def _source(name):
    return (API / name).read_text(encoding="utf-8")


# ── behaviour ────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_all_languages_sentinel_is_translated():
    """The #886 symptom: on a Dutch session the sentinel must not read English."""
    from cps.api import options as mod
    with patch.object(mod, "_", _dutch), \
         patch.object(mod, "calibre_db", _library(("nld", "Nederlands"))):
        opts = mod.book_language_options()
    assert opts[0] == {"id": "all", "name": "Alle talen"}


@pytest.mark.unit
def test_dynamic_language_names_are_not_re_translated():
    """speaking_language() hands back names already localised for the request.

    Guards the tempting wrong fix — running gettext over the whole list, which
    would treat every language name as a msgid and mangle the ones that happen
    to collide with a catalog entry.
    """
    from cps.api import options as mod
    sentinel = {"Nederlands": "WRONG", "Show All": "Alle talen"}
    with patch.object(mod, "_", lambda s: sentinel.get(s, s)), \
         patch.object(mod, "calibre_db", _library(("nld", "Nederlands"))):
        opts = mod.book_language_options()
    assert opts[1] == {"id": "nld", "name": "Nederlands"}


@pytest.mark.unit
def test_every_library_language_is_offered_after_the_sentinel():
    from cps.api import options as mod
    with patch.object(mod, "_", _dutch), \
         patch.object(mod, "calibre_db",
                      _library(("nld", "Nederlands"), ("eng", "Engels"))):
        opts = mod.book_language_options()
    assert [o["id"] for o in opts] == ["all", "nld", "eng"]


@pytest.mark.unit
def test_language_options_fail_soft_when_the_library_is_unreadable():
    """A locked or missing Calibre DB must not 500 an otherwise-working form.

    The admin payload already degraded this way; the account payload did not.
    Sharing one builder gives both the resilient behaviour.
    """
    from cps.api import options as mod
    broken = SimpleNamespace(
        speaking_language=lambda: (_ for _ in ()).throw(RuntimeError("db locked")))
    with patch.object(mod, "_", _dutch), patch.object(mod, "calibre_db", broken):
        opts = mod.book_language_options()
    assert opts == [{"id": "all", "name": "Alle talen"}]


@pytest.mark.unit
def test_locale_options_expose_each_locale_under_its_own_name():
    from cps.api import options as mod
    nl = _Locale("nl", "Nederlands")
    with patch.object(mod, "get_available_locale", lambda: [nl]):
        assert mod.locale_options() == [{"id": "nl", "name": "Nederlands"}]


@pytest.mark.unit
def test_admin_ui_config_serves_the_translated_options():
    """End of the reported path: the payload the admin settings page renders."""
    from cps.api import admin as admin_mod
    from cps.api import options as opt_mod
    nl = _Locale("nl", "Nederlands")
    cfg = SimpleNamespace(
        config_calibre_web_title="t", config_books_per_page=10,
        config_random_books=4, config_authors_max=5, config_theme=0,
        config_default_language="all", config_default_locale="nl",
        config_server_announcement="")
    with patch.object(opt_mod, "_", _dutch), \
         patch.object(opt_mod, "calibre_db", _library(("nld", "Nederlands"))), \
         patch.object(opt_mod, "get_available_locale", lambda: [nl]), \
         patch.object(admin_mod, "config", cfg), \
         patch.object(admin_mod, "config_theme_slug", lambda v: "dark"):
        payload = admin_mod._ui_config_payload()
    assert payload["languages"][0]["name"] == "Alle talen"
    assert payload["locales"] == [{"id": "nl", "name": "Nederlands"}]


@pytest.mark.unit
def test_admin_config_route_serves_the_translated_options_over_http():
    """The whole reported path, at the HTTP boundary.

    Goes through the real blueprint, the real API auth gate and the real JSON
    encoder rather than calling the payload builder directly, so a regression in
    routing or serialization surfaces here too. The catalog itself is stubbed —
    the real compiled .mo is verified at release time (see the i18n steps in the
    verification standard), not in a unit lane that ships no .mo files.
    """
    import flask
    from cps.api import api_v1
    from cps.api import admin as admin_mod
    from cps.api import options as opt_mod

    admin_user = SimpleNamespace(is_authenticated=True, is_anonymous=False,
                                 role_admin=lambda: True, id=1)
    cfg = SimpleNamespace(
        config_calibre_web_title="t", config_books_per_page=10,
        config_random_books=4, config_authors_max=5, config_theme=0,
        config_default_language="all", config_default_locale="nl",
        config_server_announcement="",
        config_allow_reverse_proxy_header_login=False, config_anonbrowse=0)

    app = flask.Flask(__name__)
    app.testing = True
    app.config.update(WTF_CSRF_ENABLED=False, SECRET_KEY="test",
                      RATELIMIT_ENABLED=False)
    app.register_blueprint(api_v1)

    @app.before_request
    def _sign_in():  # cw_login resolves current_user from g._login_user
        flask.g._login_user = admin_user

    with patch.object(opt_mod, "_", _dutch), \
         patch.object(opt_mod, "calibre_db", _library(("nld", "Nederlands"))), \
         patch.object(opt_mod, "get_available_locale", lambda: [_Locale("nl", "Nederlands")]), \
         patch.object(admin_mod, "config", cfg), \
         patch.object(admin_mod, "current_user", admin_user), \
         patch.object(admin_mod, "config_theme_slug", lambda v: "dark"), \
         patch("cps.api.config", cfg), \
         patch("cps.usermanagement.config", cfg):
        resp = app.test_client().get("/api/v1/admin/config")

    assert resp.status_code == 200
    assert resp.get_json()["languages"][0] == {"id": "all", "name": "Alle talen"}


@pytest.mark.unit
def test_account_payload_serves_the_translated_options():
    """The other form that renders this select.

    The structural tests below prove _serialize_account() *calls* the shared
    builders; this proves the result actually reaches the payload. A refactor
    could satisfy the former while assigning something else to "languages".
    """
    from cps.api import account as mod
    from cps.api import options as opt_mod

    user = SimpleNamespace(
        name="alice", email="a@x.com", kindle_mail="", kindle_mail_subject="",
        kobo_only_shelves_sync=False, opds_only_shelves_sync=False,
        locale="nl", default_language="all", theme=0,
        ui_font_body="", ui_font_display="",
        role_admin=lambda: False, role_upload=lambda: False,
        role_edit=lambda: False, role_download=lambda: True,
        role_delete_books=lambda: False, role_edit_shelfs=lambda: False,
        role_viewer=lambda: True, role_passwd=lambda: True)

    with patch.object(opt_mod, "_", _dutch), \
         patch.object(opt_mod, "calibre_db", _library(("nld", "Nederlands"))), \
         patch.object(opt_mod, "get_available_locale", lambda: [_Locale("nl", "Nederlands")]), \
         patch.object(mod, "current_user", user), \
         patch.object(mod, "_app_passwords", lambda: []):
        payload = mod._serialize_account()

    assert payload["languages"][0] == {"id": "all", "name": "Alle talen"}
    assert payload["locales"] == [{"id": "nl", "name": "Nederlands"}]


# ── structure: the two forms cannot drift apart again ────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("module", ["admin.py", "account.py"])
def test_neither_form_builds_its_own_language_list(module):
    """The sentinel string lives in exactly one module.

    This is the drift guard. #886 happened because the same list existed twice
    and only one copy was marked for translation; a second copy reappearing is
    the regression, whether or not that copy remembers gettext.
    """
    assert "Show All" not in _source(module)


@pytest.mark.unit
@pytest.mark.parametrize("module,func", [("admin.py", "_ui_config_payload"),
                                         ("account.py", "_serialize_account")])
def test_both_forms_call_the_shared_builders(module, func):
    tree = ast.parse(_source(module))
    target = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == func)
    called = {n.func.id for n in ast.walk(target)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert {"locale_options", "book_language_options"} <= called


@pytest.mark.unit
def test_the_sentinel_is_marked_for_translation_in_the_shared_builder():
    """Mutation guard: a bare "Show All" literal in options.py is the bug."""
    tree = ast.parse(_source("options.py"))
    wrapped = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in ("_", "N_") and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert "Show All" in wrapped


@pytest.mark.unit
@pytest.mark.parametrize("page,field", [("Admin.tsx", "cfg.languages"),
                                        ("Account.tsx", "account.languages")])
def test_spa_renders_these_names_verbatim(page, field):
    """Server-side translation is load-bearing for these two selects.

    The names arrive already localised, so the SPA must NOT put them through
    t(). If this pin ever fails, the contract moved and options.py has to move
    with it — see the module docstring on the two patterns.
    """
    src = (ROOT / "frontend" / "src" / "pages" / page).read_text(encoding="utf-8")
    render = re.search(re.escape(field) + r"\.map\(\((\w+)\) =>.*?</option>", src,
                       re.DOTALL)
    assert render, f"{page}: could not find the {field} select"
    var = render.group(1)
    assert f"{{{var}.name}}" in render.group(0)
    assert f"t({var}.name)" not in render.group(0)
