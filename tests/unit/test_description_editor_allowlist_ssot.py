# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork #919 — the New UI description editor may only offer formatting the
server keeps.

Descriptions are sanitized on the way out by cps/clean_html.py's clean_string.
bleach ESCAPES a tag outside its allowlist instead of dropping it, so a toolbar
button emitting a tag the server rejects does not fail quietly — it prints
"&lt;u&gt;text&lt;/u&gt;" into the reader's description. That makes the
correspondence between the editor's tag set and the sanitizer's a correctness
invariant, not a style preference, and it is duplicated across a TS file and a
Python one, so it needs pinning rather than trusting.

These tests read the real allowlist out of frontend/src/lib/richText.ts and
round-trip every tag through the real sanitizer.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RICH_TEXT_TS = REPO / "frontend" / "src" / "lib" / "richText.ts"
EDITOR_TSX = REPO / "frontend" / "src" / "components" / "RichTextEditor.tsx"
EDIT_BOOK_TSX = REPO / "frontend" / "src" / "pages" / "EditBook.tsx"

# One valid sample per tag the editor is allowed to emit. Every entry in
# EDITOR_ALLOWED_TAGS must have one (asserted below), so adding a tag to the
# toolbar forces whoever adds it to show that it survives the server.
SAMPLES = {
    "p": "<p>x</p>",
    "br": "a<br>b",
    "strong": "<strong>x</strong>",
    "em": "<em>x</em>",
    "h2": "<h2>x</h2>",
    "h3": "<h3>x</h3>",
    "h4": "<h4>x</h4>",
    "ul": "<ul><li>x</li></ul>",
    "ol": "<ol><li>x</li></ol>",
    "li": "<ul><li>x</li></ul>",
    "blockquote": "<blockquote>x</blockquote>",
    "code": "<code>x</code>",
    "pre": "<pre>x</pre>",
    "a": '<a href="https://example.com">x</a>',
}

# Formatting the editor deliberately does NOT offer. If a bleach bump ever
# starts keeping these, this test turns red and that is the signal the buttons
# could be added, not a defect to paper over.
DELIBERATELY_ABSENT = ["u", "s", "strike", "del", "font", "img", "table"]


def editor_allowed_tags():
    """The editor's tag set, or an empty set when the module is absent.

    Absent is a real state (it is what main looked like before #919), and it
    has to collect rather than error, or the assertions that carry the actual
    regression never get to run and the red is meaningless.
    """
    if not RICH_TEXT_TS.exists():
        return set()
    src = RICH_TEXT_TS.read_text(encoding="utf-8")
    match = re.search(r"EDITOR_ALLOWED_TAGS[^=]*=\s*new Set\(\[(.*?)\]\)", src, re.S)
    assert match, "EDITOR_ALLOWED_TAGS not found in %s" % RICH_TEXT_TS
    tags = re.findall(r"'([a-zA-Z0-9]+)'", match.group(1))
    assert tags, "EDITOR_ALLOWED_TAGS parsed empty — the regex is stale"
    return set(tags)


def test_rich_text_module_ships():
    assert RICH_TEXT_TS.exists(), (
        "%s is missing: the New UI has no shared description-HTML allowlist, so "
        "nothing constrains the editor to what the server keeps (#919)" % RICH_TEXT_TS
    )
    assert EDITOR_TSX.exists(), "%s is missing" % EDITOR_TSX


@pytest.fixture(scope="module")
def clean_string():
    from cps.clean_html import clean_string as fn
    return fn


def test_every_editor_tag_has_a_round_trip_sample():
    missing = editor_allowed_tags() - set(SAMPLES)
    assert not missing, (
        "richText.ts allows %s with no round-trip sample here. Add one and prove "
        "the server keeps the tag before shipping a button for it." % sorted(missing)
    )


@pytest.mark.parametrize("tag", sorted(editor_allowed_tags() or SAMPLES))
def test_editor_tag_survives_the_server_sanitizer(clean_string, tag):
    cleaned = clean_string(SAMPLES[tag])
    assert "<%s" % tag in cleaned, (
        "the editor can emit <%s> but clean_string strips or escapes it: %r. A "
        "button for it would put literal markup in the description." % (tag, cleaned)
    )
    assert "&lt;%s" % tag not in cleaned


@pytest.mark.parametrize("tag", DELIBERATELY_ABSENT)
def test_absent_formatting_really_is_rejected_by_the_server(clean_string, tag):
    assert tag not in editor_allowed_tags(), (
        "<%s> is in EDITOR_ALLOWED_TAGS but listed as deliberately absent — one "
        "of the two is now wrong." % tag
    )
    cleaned = clean_string("<{t}>x</{t}>".format(t=tag))
    assert "<%s" % tag not in cleaned


def test_server_allowlist_is_a_superset_of_the_editor_allowlist():
    from cps.clean_html import DESCRIPTION_ALLOWED_TAGS
    if DESCRIPTION_ALLOWED_TAGS is None:
        pytest.skip("nh3 backend applies its own built-in allowlist")
    extra = editor_allowed_tags() - set(DESCRIPTION_ALLOWED_TAGS)
    assert not extra, "editor emits tags the server does not allow: %s" % sorted(extra)


def test_editor_forces_style_with_css_off():
    """execCommand('bold') emits <span style="font-weight:bold"> when
    styleWithCSS is on. bleach strips style attributes, so the bold would look
    right while editing and be gone on the book page — the exact silent-loss
    failure this whole file exists to prevent."""
    src = EDITOR_TSX.read_text(encoding="utf-8")
    assert re.search(r"execCommand\(\s*'styleWithCSS'\s*,\s*false\s*,\s*'false'\s*\)", src), (
        "RichTextEditor no longer forces styleWithCSS off before formatting"
    )


def test_editor_offers_no_underline_or_strikethrough_command():
    src = EDITOR_TSX.read_text(encoding="utf-8")
    for command in ("'underline'", "'strikeThrough'"):
        assert command not in src, (
            "RichTextEditor calls execCommand(%s); the server escapes that tag "
            "into visible markup." % command
        )


def test_edit_book_uses_the_editor_not_a_bare_textarea_for_the_description():
    """Red on main: the New UI shipped a plain <textarea> bound to
    form.comments, which is what #919 (and the #1038 switch-back report) is."""
    src = EDIT_BOOK_TSX.read_text(encoding="utf-8")
    assert "<RichTextEditor" in src, "EditBook no longer renders the description editor"
    assert not re.search(r"<textarea[^>]*value=\{form\.comments\}", src, re.S), (
        "the description is bound to a bare <textarea> again"
    )
