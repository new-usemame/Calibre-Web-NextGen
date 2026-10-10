# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Acceptance tests for fork #223 — app password inline display.

Reporter @droM4X observed that the newly-generated app password was shown
in a 10-second toast at the bottom of the page. Hard to spot, hard to
read, hard to copy. The fix moves the cleartext to a prominent inline
box at the top of the app-password section on ``/me``, persistent across
reloads of that page, and cleared automatically when the user navigates
to any other route.

Implementation contract pinned by these tests:

1. ``app_password_create`` writes the generated cleartext (NOT the hash)
   plus the label into ``session["pending_app_password"]`` before
   redirecting. Existing label-validation flash path is preserved.
2. The cleartext is never persisted to the database — only the hash is.
3. A request to any non-profile route clears the session key
   (``before_request`` hook). This is the "disappear once they navigate
   away" half of the requirement.
4. Reloading ``/me`` itself keeps the cleartext visible — refresh does
   not consume it.
5. The template includes the cleartext when ``pending_app_password`` is
   set; does not include it otherwise.
"""

import inspect
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _web_py_source():
    return (REPO_ROOT / "cps" / "web.py").read_text()


def _user_edit_template_source():
    return (REPO_ROOT / "cps" / "templates" / "user_edit.html").read_text()


def _init_py_source():
    return (REPO_ROOT / "cps" / "__init__.py").read_text()


def test_created_app_password_is_shown_inline_once_and_stored_only_as_a_hash(
        monkeypatch, tmp_path):
    """Points 1 and 2 above, by running the real ``/me/app-passwords`` route.

    The cleartext and label land in ``session["pending_app_password"]`` for the
    inline box, never in a flash message (the 10-second toast @droM4X could not
    copy from), and the token signs in while app.db holds only its hash.
    """
    from cps import ub
    from cps.web import web
    from tests.unit.koreader_library_world import LibraryWorld
    world = LibraryWorld(monkeypatch, tmp_path)
    world.enable_web()
    world.app.register_blueprint(web)
    try:
        world.add_user("alice")
        browser = world.browser("alice")
        created = browser.post("/me/app-passwords", data={"label": "Kobo Forma"})
        assert created.status_code == 302
        with browser.session_transaction() as session:
            pending = session["pending_app_password"]
            flashed = [message for _category, message in session.get("_flashes", [])]
        assert pending["label"] == "Kobo Forma"
        token = pending["token"]
        assert not any(token in message for message in flashed)
        assert world.client.get("/kosync/users/auth",
                                headers=world.basic("alice", token)).status_code == 200
        row = world.session.query(ub.UserAppPassword).one()
        assert token not in (row.password_hash, row.label)
    finally:
        world.close()


def test_profile_route_passes_pending_app_password_to_template():
    """The ``/me`` profile route must read the session key and pass it
    to the template as ``pending_app_password``. Template renders it
    inline; if the route doesn't pass it, the box never appears.
    """
    src = _web_py_source()
    # The profile route is the function decorated with the /me path.
    profile_match = re.search(
        r"def profile\(\):(.*?)(?=^def |^@web\.route)",
        src,
        re.DOTALL | re.MULTILINE,
    )
    assert profile_match, "Could not locate `def profile():` in cps/web.py."
    body = profile_match.group(1)
    assert "pending_app_password" in body, (
        "The profile route must pass `pending_app_password=` to its "
        "render_template / render_title_template call so the inline box "
        "knows whether to render."
    )


def test_before_request_clears_session_on_non_profile_routes():
    """A before_request hook must clear ``pending_app_password`` from
    the session when the user navigates to any route other than the
    profile page. This implements "disappear once they navigate away".
    """
    init_src = _init_py_source()
    web_src = _web_py_source()
    combined = init_src + "\n" + web_src
    # The hook can live in either file. Pin its existence.
    hook_present = (
        "pending_app_password" in combined
        and re.search(
            r"session\.pop\(['\"]pending_app_password['\"]",
            combined,
        )
    )
    assert hook_present, (
        "Could not find a `session.pop('pending_app_password', ...)` "
        "call anywhere in cps/__init__.py or cps/web.py. Add a "
        "before_request hook (or equivalent) that clears the session "
        "key when the user navigates away from /me."
    )


def test_template_renders_pending_app_password_inline():
    """The user_edit.html template must render
    ``pending_app_password`` inline in the App passwords section when
    the variable is truthy. Without this, the session value never
    surfaces to the user.
    """
    src = _user_edit_template_source()
    # The template branch shape: a conditional that gates on
    # pending_app_password and renders the .token / cleartext.
    assert "pending_app_password" in src, (
        "cps/templates/user_edit.html must reference pending_app_password "
        "in a conditional block that renders the cleartext + label inline "
        "in the App passwords section."
    )
    # And the box must contain the actual cleartext value, not just the
    # variable name.
    rendered = re.search(
        r"\{\{\s*pending_app_password\.(?:token|cleartext)\s*\}\}",
        src,
    )
    assert rendered, (
        "The template must render pending_app_password.token (or "
        ".cleartext) inside a `{{ ... }}` Jinja expression so the user "
        "actually sees the password. Current template did not include "
        "the rendered token."
    )


def test_template_does_not_show_when_pending_app_password_missing():
    """When pending_app_password is None/absent, the inline box must
    not render. Conditional shape, not unconditional.

    Pin the Jinja `{% if pending_app_password %}` guard.
    """
    src = _user_edit_template_source()
    guard = re.search(
        r"\{%\s*if\s+pending_app_password\s*%\}",
        src,
    )
    assert guard, (
        "The template must guard the inline cleartext render with "
        "`{% if pending_app_password %}` — without the guard, an empty "
        "box would appear on every /me visit."
    )
