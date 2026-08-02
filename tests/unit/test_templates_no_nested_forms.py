# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Repo-wide regression test: no Jinja template may nest a <form>.

HTML5 forbids nested <form> elements. The parser does not "fix" this by
closing the outer form -- it DROPS the inner <form> start tag entirely and
re-parents its children (submit button, CSRF token, inputs) onto the OUTER
form. The user then clicks a button that silently POSTs to the wrong route,
with no error anywhere.

This class of bug has now shipped twice in this codebase:

* fork #109  -- user_edit.html: the v4.0.31 app-passwords block was added
  inside the profile-save form. Clicking "create" reloaded /me with no
  token and no flash.
* CWA #1444  -- config_db.html: the "Restore Calibre Database (Last Resort)"
  form was added inside the DB-config form. Clicking the button silently
  re-saved the DB configuration instead of restoring the library. Verified
  in Chromium before the fix: document.forms contained no
  /admin/restore_calibre_db entry at all, and the restore button's
  .form.action was "/admin/dbconfig".

Both were invisible to unit tests and to a casual look at the source, because
the source LOOKS correctly nested -- only a real HTML5 tree construction
reveals the dropped tag. So the guard belongs at the template-source level,
across every template, not one file at a time.

tests/unit/test_user_edit_template_no_nested_forms.py keeps the #109-specific
semantic pins (that the app-password form is bound to the right endpoint).
This file owns the general invariant for the whole template tree.

Stand-alone parser -- no Flask runtime, no Jinja render.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest


TEMPLATE_DIR = (Path(__file__).resolve().parent.parent.parent /
                "cps" / "templates")


def _strip_jinja(src: str) -> str:
    """Remove Jinja directives so HTMLParser sees only HTML.

    `{# ... #}` comments  -> empty
    `{% ... %}` blocks    -> empty (control flow, includes, blocks)
    `{{ ... }}` values    -> placeholder text

    HTML comments are NOT stripped here: HTMLParser already skips their
    contents, so a `<!--form ...-->` (shelf.html has one) correctly does
    not register as a tag.
    """
    src = re.sub(r"\{#.*?#\}", "", src, flags=re.DOTALL)
    src = re.sub(r"\{%.*?%\}", "", src, flags=re.DOTALL)
    src = re.sub(r"\{\{.*?\}\}", "X", src, flags=re.DOTALL)
    return src


class _FormDepthTracker(HTMLParser):
    """Walks template HTML tracking <form> depth, recording every position
    where a <form> opens while another <form> is still open."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.violations = []  # (line, col)

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            if self.depth >= 1:
                self.violations.append(self.getpos())
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "form" and self.depth > 0:
            self.depth -= 1


def _nested_form_violations(path: Path):
    tracker = _FormDepthTracker()
    tracker.feed(_strip_jinja(path.read_text(encoding="utf-8", errors="replace")))
    return tracker.violations


def _all_templates():
    return sorted(TEMPLATE_DIR.rglob("*.html"))


@pytest.mark.unit
class TestNoNestedFormsAnywhere:

    def test_template_dir_is_present(self):
        assert TEMPLATE_DIR.is_dir(), f"template dir missing: {TEMPLATE_DIR}"
        assert _all_templates(), "no templates found -- path is probably wrong"

    @pytest.mark.parametrize(
        "template", _all_templates(), ids=lambda p: p.name
    )
    def test_template_has_no_nested_form(self, template):
        violations = _nested_form_violations(template)
        assert not violations, (
            f"{template.relative_to(TEMPLATE_DIR)} opens a <form> while "
            f"another <form> is still open, at: " +
            ", ".join(f"line {l} col {c}" for l, c in violations) +
            "\n\nHTML5 forbids nested <form>. The browser DROPS the inner "
            "<form> tag and re-parents its button/inputs onto the outer "
            "form, so the button silently submits to the outer form's "
            "action. See fork #109 (user_edit.html) and CWA #1444 "
            "(config_db.html) for two shipped instances of this exact bug."
        )

    def test_restore_calibre_db_form_is_not_inside_db_config_form(self):
        """Semantic pin for CWA #1444 specifically.

        Generic nesting detection would still pass if someone "fixed" this
        by deleting the restore form outright. Pin that the restore form
        exists AND opens at form-nesting depth 0.
        """
        path = TEMPLATE_DIR / "config_db.html"
        assert path.is_file(), "config_db.html missing"
        src = path.read_text(encoding="utf-8")

        # Keep {{ ... }} intact so the url_for marker survives; drop only
        # HTML comments and Jinja control flow / comments.
        clean = re.sub(r"<!--.*?-->", "", src, flags=re.DOTALL)
        clean = re.sub(r"\{#.*?#\}", "", clean, flags=re.DOTALL)
        clean = re.sub(r"\{%.*?%\}", "", clean, flags=re.DOTALL)

        restore = re.search(
            r"<form[^>]*url_for\('admin\.restore_calibre_db'\)[^>]*>", clean)
        assert restore is not None, (
            "the restore_calibre_db <form> is gone from config_db.html -- "
            "the Last Resort restore button must keep its own form")

        prefix = clean[:restore.start()]
        depth = (len(re.findall(r"<form\b", prefix)) -
                 len(re.findall(r"</form>", prefix)))
        assert depth == 0, (
            f"the restore_calibre_db <form> opens at <form>-nesting depth "
            f"{depth} (expected 0). It must be a SIBLING of the "
            f"db_configuration form, not a child. Nested, the browser drops "
            f"it and the Restore button POSTs to /admin/dbconfig -- "
            f"silently re-saving config instead of restoring the library. "
            f"See CWA #1444.")

    def test_db_config_save_button_still_inside_db_config_form(self):
        """Blast-radius pin: main.js does $('#db_submit').closest('form')
        .submit(). If a fix for #1444 moved the form boundary above the
        Save row, Save would silently stop working. Pin that #db_submit
        stays inside the db_configuration form."""
        path = TEMPLATE_DIR / "config_db.html"
        src = path.read_text(encoding="utf-8")
        clean = re.sub(r"<!--.*?-->", "", src, flags=re.DOTALL)
        clean = re.sub(r"\{#.*?#\}", "", clean, flags=re.DOTALL)
        clean = re.sub(r"\{%.*?%\}", "", clean, flags=re.DOTALL)

        submit = re.search(r'id="db_submit"', clean)
        assert submit is not None, "#db_submit (Save) not found in config_db.html"

        prefix = clean[:submit.start()]
        depth = (len(re.findall(r"<form\b", prefix)) -
                 len(re.findall(r"</form>", prefix)))
        assert depth == 1, (
            f"#db_submit sits at <form>-nesting depth {depth} (expected 1). "
            f"The Save button is submitted via "
            f"$('#db_submit').closest('form').submit() in main.js, so it "
            f"must remain INSIDE the db_configuration form.")
