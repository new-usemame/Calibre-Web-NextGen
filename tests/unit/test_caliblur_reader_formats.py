# -*- coding: utf-8 -*-
# Calibre-Web Automated - fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
"""The caliBlur grid read action must only offer formats the reader renders.

caliBlur's `handleDirectReading()` builds its own `/read/<id>/<format>` URL
instead of reusing `entry.reader_list`, which is what `detail.html` guards its
read button on. Its format list was maintained by hand and had drifted: it
offered `html`, `mobi`, `azw3` and `fb2`, and fell back to the book's first
format when none of those matched.

`read_book()` in cps/web.py renders epub, kepub, pdf, txt, djvu/djv, the audio
extensions and cbr/cbt/cbz. Anything else falls through its final `else` and
redirects to the index with "Oops! Selected book is unavailable. File does not
exist or is not accessible" - a message about file access for what is purely a
format-support problem. A MOBI-only book showed a read action in the grid that
could never work, while its detail page correctly showed no read button.

Two-pronged pin:

1. The JS list equals `check_read_formats()`'s `extensions_reader` set in
   cps/helper.py. The two live in different languages with no shared
   definition, so nothing but a test stops them drifting apart again.
2. The first-format fallback stays gone. Re-adding it would reintroduce the
   bug for every format, not just the four that were listed.
"""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CALIBLUR_JS = REPO_ROOT / "cps/static/js/caliBlur.js"
HELPER_PY = REPO_ROOT / "cps/helper.py"


def _handle_direct_reading_body():
    """Body of handleDirectReading(), which is the only reader entry point here.

    Scoped deliberately: handleSendToEReader() has its own first-format
    fallback, which is correct there because the send path converts.
    """
    source = CALIBLUR_JS.read_text(encoding="utf-8")
    start = source.index("function handleDirectReading(")
    end = source.index("function handleReadStatusToggle(", start)
    return source[start:end]


def _js_readable_formats():
    """The readableFormats array literal from handleDirectReading()."""
    source = CALIBLUR_JS.read_text(encoding="utf-8")
    match = re.search(r"var\s+readableFormats\s*=\s*\[([^\]]*)\]", source)
    assert match, "readableFormats array not found in caliBlur.js"
    return {item.strip().strip("'\"").lower()
            for item in match.group(1).split(",") if item.strip()}


def _python_reader_extensions():
    """The extensions_reader set literal from check_read_formats()."""
    tree = ast.parse(HELPER_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "check_read_formats"):
            continue
        for stmt in ast.walk(node):
            if (isinstance(stmt, ast.Assign)
                    and any(getattr(t, "id", None) == "extensions_reader" for t in stmt.targets)
                    and isinstance(stmt.value, ast.Set)):
                return {elt.value.lower() for elt in stmt.value.elts
                        if isinstance(elt, ast.Constant)}
    raise AssertionError("extensions_reader set literal not found in check_read_formats()")


def test_caliblur_offers_exactly_the_reader_supported_formats():
    js_formats = _js_readable_formats()
    python_formats = _python_reader_extensions()

    unrenderable = js_formats - python_formats
    assert not unrenderable, (
        "caliBlur offers formats read_book() cannot render, so the grid read "
        "action redirects to the index with 'Selected book is unavailable': "
        "%s" % sorted(unrenderable))

    missing = python_formats - js_formats
    assert not missing, (
        "caliBlur will not offer the reader for formats it supports: %s"
        % sorted(missing))


def test_caliblur_does_not_fall_back_to_an_arbitrary_format():
    body = _handle_direct_reading_body()
    assert "selectedFormat = formats[0]" not in body, (
        "the first-format fallback is back: it hands read_book() whatever "
        "format happens to be first, which reintroduces the unavailable-book "
        "redirect for any non-renderable book")
