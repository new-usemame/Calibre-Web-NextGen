# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Hardcover review page's buttons must work in every language.

Flask-Babel installs Jinja's new-style gettext, which returns translations as
``Markup``: autoescaping leaves them untouched. The page used to write them
into single-quoted JS literals ('{{_("Failed to apply match")}}'), so the
French "Échec de l'application de la correspondance" (and the Italian
catalogue) closed the literal early. The browser rejected the whole script
("SyntaxError: unexpected token: identifier"), no click handler was bound,
and Select / Reject / Skip did nothing, with no request reaching the server.
"""

from __future__ import annotations

import glob
import json
import re
from pathlib import Path

import polib
import pytest
from jinja2 import Environment

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "cps" / "templates" / "hardcover_review_matches.html"


def _script_block():
    src = TEMPLATE.read_text(encoding="utf-8")
    return src[src.index("{% block js %}") + len("{% block js %}"):src.rindex("{% endblock %}")]


def _render(translate):
    # The same wiring Flask-Babel applies to the app's Jinja environment.
    env = Environment(autoescape=True, extensions=["jinja2.ext.i18n"])
    env.install_gettext_callables(translate, lambda s, p, n: translate(s), newstyle=True)
    env.globals.update(url_for=lambda *a, **k: "/x")
    return env.from_string(_script_block()).render()


def test_no_translation_is_written_inside_a_quoted_js_literal():
    assert not re.search(r"""['"]\{\{\s*_\(""", _script_block())


def test_hostile_translation_stays_inside_its_string():
    hostile = "l'x \"y\" </script>"
    rendered = _render(lambda s: hostile)
    assert "l'x" not in rendered
    assert rendered.count("</script>") == 1  # only the block's own closing tag
    calls = len(re.findall(r"\{\{\s*_\(", _script_block()))
    assert calls > 0
    escaped = '"l\\u0027x \\"y\\" \\u003c/script\\u003e"'
    assert rendered.count(escaped) == calls
    assert json.loads(escaped) == hostile


@pytest.mark.parametrize("po_path", sorted(glob.glob(
    str(ROOT / "cps" / "translations" / "*" / "LC_MESSAGES" / "messages.po"))))
def test_shipped_translations_never_leave_a_raw_apostrophe(po_path):
    catalog = {e.msgid: e.msgstr for e in polib.pofile(po_path) if e.msgstr}
    rendered = _render(lambda s: catalog.get(s, s))
    for msgid in re.findall(r"""_\("([^"]*)"\)""", _script_block()):
        text = catalog.get(msgid, msgid)
        if "'" in text:
            assert text not in rendered, f"{msgid!r} rendered raw in {po_path}"
