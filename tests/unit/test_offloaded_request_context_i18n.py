# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Work offloaded off the request greenlet must keep its request context.

Fork #1111 moved two inline blocking calls onto worker threads so they stop
freezing the gevent hub. That fix has a trap: ``flask_babel.gettext`` does NOT
raise when it runs without a request context — ``get_translations()`` returns a
null fallback and gettext hands back the untranslated **English msgid**. So an
offloaded callable that builds a user-facing string looks fine in tests written
in English and silently ships English to every other locale.

``cps/helper.py::save_cover_from_url`` returns ``_("...")`` on all eight of its
error paths, which is exactly that shape. These tests measure the translated
output against a real compiled ``.mo`` rather than pinning the wrapper's name,
so they fail on the behaviour a user would notice.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

flask = pytest.importorskip("flask", reason="flask is a runtime dependency")
flask_babel = pytest.importorskip("flask_babel", reason="flask_babel is a runtime dependency")

from flask import Flask, copy_current_request_context  # noqa: E402
from flask_babel import Babel, gettext  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
TRANSLATIONS = REPO_ROOT / "cps" / "translations"

# A real string from save_cover_from_url's error paths, with a real German
# translation shipped in-tree. Using a genuinely translated msgid is the whole
# point: an English msgid comes back identical whether the context survived or
# not, so it cannot tell the two apart.
MSGID = "Error Downloading Cover"
GERMAN = "Fehler beim Herunterladen des Covers"


def _app():
    app = Flask(__name__)
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = str(TRANSLATIONS)
    app.config["BABEL_DEFAULT_LOCALE"] = "de"
    Babel(app, locale_selector=lambda: "de")
    return app


def _run_on_worker_thread(fn):
    """Run ``fn`` on a real OS thread, like parallel.run_blocking does."""
    box = {}

    def target():
        box["value"] = fn()

    t = threading.Thread(target=target)
    t.start()
    t.join()
    return box["value"]


@pytest.mark.skipif(
    not (TRANSLATIONS / "de" / "LC_MESSAGES" / "messages.mo").exists(),
    reason="compiled de catalog absent; run scripts/compile_translations.sh",
)
class TestOffloadedWorkKeepsItsTranslations:
    def test_the_string_is_actually_translated_in_context(self):
        """Guards the guard: if this msgid ever loses its German translation
        the two tests below would both return English and agree for the wrong
        reason."""
        with _app().test_request_context("/"):
            assert gettext(MSGID) == GERMAN

    def test_unwrapped_offload_silently_falls_back_to_english(self):
        """The failure mode being defended against. gettext does not raise off
        the request context — it returns the msgid, so the regression is
        invisible unless a test looks at a non-English locale."""
        with _app().test_request_context("/"):
            got = _run_on_worker_thread(lambda: gettext(MSGID))
        assert got == MSGID, (
            "off-context gettext no longer falls back silently; if flask_babel "
            "started raising instead, the wrapper below can be simplified"
        )

    def test_wrapped_offload_keeps_the_users_language(self):
        with _app().test_request_context("/"):
            wrapped = copy_current_request_context(lambda: gettext(MSGID))
            got = _run_on_worker_thread(wrapped)
        assert got == GERMAN, (
            "offloaded work lost its request context, so flask_babel fell back to "
            "the English msgid — every non-English user sees English (fork #1111)"
        )


class TestEditbooksCoverDownloadCarriesItsContext:
    """``save_cover_from_url`` returns ``_(...)`` on all eight error paths, so
    the offloaded call must carry the request context or those messages ship
    untranslated."""

    def test_run_blocking_call_is_wrapped_in_copy_current_request_context(self):
        tree = ast.parse((REPO_ROOT / "cps/editbooks.py").read_text(encoding="utf-8"))

        checked = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "run_blocking"):
                continue
            checked += 1
            arg = node.args[0] if node.args else None
            assert (
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Name)
                and arg.func.id == "copy_current_request_context"
            ), (
                f"cps/editbooks.py line {node.lineno}: the offloaded callable is not wrapped "
                "in copy_current_request_context. It runs on a worker thread, and flask_babel "
                "returns the English msgid instead of raising when the request context is "
                "missing — so every non-English user silently gets English error messages "
                "(fork #1111)."
            )

        assert checked, "no parallel.run_blocking call found in cps/editbooks.py"
