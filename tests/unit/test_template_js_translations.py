# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A translation must never be able to end the JavaScript string it sits in.

Flask-Babel installs Jinja's new-style gettext, which returns translations as
``Markup``, so autoescaping leaves their quotes alone. Templates that wrote
them into quoted JS literals (``'{{ _("Failed to apply match") }}'``) broke as
soon as a catalog used an apostrophe: the French "Échec de l'application…"
closed the literal early, the browser dropped the whole script, and the
page's buttons did nothing. Shipped catalogs broke the Hardcover review page
(fr, it), the EPUB fixer (fr, it), Tasks (fr) and shelf reordering (it).

Every inline script and inline event handler is rendered here with a
translation containing both quote characters. It must only ever appear
JSON-encoded (``|tojson``), never raw.

Contributed in part by @lguerard (#2503).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest
from jinja2 import ChainableUndefined, Environment

pytestmark = pytest.mark.unit

TEMPLATES = Path(__file__).resolve().parents[2] / "cps" / "templates"
HOSTILE = "l'x \"y\""

# Inline scripts that are JavaScript (not <script type="text/template"> HTML
# fragments, where a quote in the text is harmless) and inline handlers.
SCRIPT = re.compile(
    r"<script(?![^>]*\b(?:src=|type=\"text/template\"))[^>]*>(.*?)</script>", re.S
)
HANDLER = re.compile(r"""\son[a-z]+="([^"]*)\"""")


class _Anything:
    """Stands in for request-time objects (current_user, config) in a fragment."""

    def __getattr__(self, name):
        return _Anything()

    def __call__(self, *args, **kwargs):
        return _Anything()

    def __getitem__(self, key):
        return _Anything()

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __str__(self):
        return ""


def _env():
    env = Environment(
        autoescape=True,
        extensions=["jinja2.ext.i18n", "jinja2.ext.do", "jinja2.ext.loopcontrols"],
        undefined=ChainableUndefined,
    )
    # The wiring Flask-Babel applies to the app's environment.
    env.install_gettext_callables(
        lambda s, **kw: HOSTILE, lambda s, p, n, **kw: HOSTILE, newstyle=True
    )
    env.globals.update(
        url_for=lambda *a, **k: "/x",
        csrf_token=lambda: "t",
        current_user=_Anything(),
        config=_Anything(),
    )
    env.policies["json.dumps_function"] = lambda o, **kw: json.dumps(
        o, default=lambda v: None, **kw
    )
    return env


def _fragments():
    for path in sorted(TEMPLATES.glob("*.html")):
        source = path.read_text(encoding="utf-8")
        for kind, pattern in (("script", SCRIPT), ("handler", HANDLER)):
            for match in pattern.finditer(source):
                if "_(" in match.group(1):
                    line = source.count("\n", 0, match.start()) + 1
                    yield pytest.param(kind, match.group(1), id=f"{path.name}:{line}:{kind}")


FRAGMENTS = list(_fragments())


def test_the_sweep_sees_the_pages_that_broke():
    seen = {p.id.split(":")[0] for p in FRAGMENTS}
    assert {
        "hardcover_review_matches.html",
        "cwa_epub_fixer.html",
        "tasks.html",
        "shelf_reorder.html",
        "user_edit.html",
        "layout.html",
    } <= seen


@pytest.mark.parametrize("kind,fragment", FRAGMENTS)
def test_a_quote_in_a_translation_stays_inside_its_string(kind, fragment):
    rendered = _env().from_string(fragment).render()
    # A handler is HTML-decoded before the browser compiles it, and a raw
    # double quote would end the attribute itself.
    if kind == "handler":
        assert '"y"' not in rendered
        rendered = html.unescape(rendered)
    assert json.dumps(HOSTILE) in rendered.replace("\\u0027", "'")
    assert HOSTILE not in rendered
