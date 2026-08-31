# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Structural regression coverage for Basic Configuration's AJAX submits.

These unit tests pin the native Save control, the intercepted form submit
event used by Save and Enter in single-line fields, and the backfill button's
explicit payload append. They do not execute a browser engine or jQuery.
"""

from html.parser import HTMLParser
from pathlib import Path
import re

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "cps" / "templates" / "config_edit.html"
MAIN_JS = REPO_ROOT / "cps" / "static" / "js" / "main.js"


class _ConfigForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form = None
        self.controls = []
        self.form_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form" and attributes.get("id") == "config_form":
            assert self.form is None, "config_form must be unique"
            self.form = attributes
            self.form_depth = 1
        elif tag == "form" and self.form_depth:
            self.form_depth += 1
        elif self.form_depth and tag in {"button", "input"}:
            self.controls.append((tag, attributes))

    def handle_endtag(self, tag):
        if tag == "form" and self.form_depth:
            self.form_depth -= 1


def _callback_body(source, selector, event):
    pattern = re.compile(
        r"\$\(\"" + re.escape(selector) + r"\"\)\." + re.escape(event)
        + r"\(function\([^)]*\)\s*\{"
    )
    match = pattern.search(source)
    assert match, f"missing {event} handler for {selector}"

    start = match.end()
    depth = 1
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index]
    raise AssertionError(f"unterminated {event} handler for {selector}")


@pytest.mark.unit
def test_config_form_uses_save_as_its_only_native_submit_control():
    parser = _ConfigForm()
    parser.feed(TEMPLATE.read_text(encoding="utf-8"))

    assert parser.form is not None
    assert parser.form.get("id") == "config_form"
    assert "action" not in parser.form, (
        "the intercepted AJAX form must not point ordinary Save/Enter submits "
        "at an HTML error or the AJAX endpoint's JSON response"
    )

    csrf = [
        attrs for tag, attrs in parser.controls
        if tag == "input" and attrs.get("name") == "csrf_token"
    ]
    assert len(csrf) == 1
    assert csrf[0].get("type") == "hidden"
    assert "disabled" not in csrf[0]

    backfill = [
        attrs for _tag, attrs in parser.controls
        if attrs.get("id") == "kobo_kepub_backfill"
    ]
    assert backfill == [{
        "class": "btn btn-default",
        "type": "button",
        "id": "kobo_kepub_backfill",
        "name": "kobo_kepub_backfill",
        "value": "on",
    }]

    save = [
        attrs for _tag, attrs in parser.controls
        if attrs.get("id") == "config_submit"
    ]
    assert save == [{
        "type": "submit",
        "name": "submit",
        "id": "config_submit",
        "class": "btn btn-default",
    }]

    native_submits = [
        attrs for tag, attrs in parser.controls
        if (tag == "button" and attrs.get("type", "submit") == "submit")
        or (tag == "input" and attrs.get("type", "text") in {"submit", "image"})
    ]
    assert native_submits == save


@pytest.mark.unit
def test_native_form_submit_and_backfill_click_use_ajax_with_required_payload():
    source = MAIN_JS.read_text(encoding="utf-8")

    form_submit = _callback_body(source, "#config_form", "submit")
    assert "preventDefault()" in form_submit
    assert "submitConfigForm($(this))" in form_submit
    assert form_submit.count("submitConfigForm(") == 1

    backfill_click = _callback_body(source, "#kobo_kepub_backfill", "click")
    assert "submitConfigForm($(this).closest(\"form\"), this)" in backfill_click
    assert backfill_click.count("submitConfigForm(") == 1
    assert 'trigger("submit")' not in backfill_click

    submit_function = re.search(
        r"function submitConfigForm\(\$form, submitter\)\s*\{(?P<body>.*?)\n    \}",
        source,
        re.DOTALL,
    )
    assert submit_function, "missing shared AJAX config submission function"
    body = submit_function.group("body")
    assert "$form.serialize()" in body
    assert "submitter.name" in body
    assert "submitter.value" in body
    assert re.search(
        r'formData\s*\+=\s*'
        r'\(formData\s*\?\s*"&"\s*:\s*""\)\s*\+\s*'
        r'\$\.param\(submitterData\)\s*;',
        body,
    ), "the encoded submitter must be appended to the POST body"
    assert 'request_path = "/admin/ajaxconfig"' in body
    assert "$.post(getPath() + request_path" in body

    handler_source = (REPO_ROOT / "cps" / "admin.py").read_text(encoding="utf-8")
    assert 'elif "kobo_kepub_backfill" in to_save:' in handler_source
