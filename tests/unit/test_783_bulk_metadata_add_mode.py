# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for non-destructive bulk metadata updates (#783).

The bulk SPA fans the same request out to the existing per-book metadata
endpoint, so these tests pin the endpoint's dispatched editor values. They fail
on the pre-fix endpoint because it forwards only the incoming value and rewrites
even when an add contains nothing new.
"""
import inspect
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest


pytestmark = pytest.mark.unit

_SUCCESS = flask.Response(json.dumps({"success": True}), mimetype="application/json")


def _row(name):
    return SimpleNamespace(name=name)


def _book():
    return SimpleNamespace(
        id=5,
        title="Original title",
        authors=[_row("Ursula K. Le Guin")],
        series=[],
        series_index=1.0,
        tags=[_row("Science Fiction"), _row("Classic")],
        publishers=[_row("Ace")],
        languages=[SimpleNamespace(lang_code="eng")],
        comments=[],
        ratings=[],
        pubdate=None,
        identifiers=[],
    )


def _editor():
    return SimpleNamespace(
        is_authenticated=True,
        is_anonymous=False,
        role_edit=lambda: True,
    )


def _run(body):
    from cps.api import edit as mod

    app = flask.Flask(__name__)
    book = _book()
    database = SimpleNamespace(
        get_filtered_book=lambda *args, **kwargs: book,
        get_cc_columns=lambda *args, **kwargs: [],
    )
    core = MagicMock(return_value=_SUCCESS)
    with app.test_request_context(
        "/api/v1/books/5/metadata", method="POST", json=body,
        content_type="application/json",
    ):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db", database), \
             patch.object(mod, "edit_book_param", core), \
             patch.object(mod, "get_locale", return_value="en"), \
             patch.object(
                 mod.isoLanguages,
                 "get_language_name",
                 side_effect=lambda _locale, code: {"eng": "English"}[code],
             ):
            response = inspect.unwrap(mod.update_metadata)(5)
    return response, core


def _value(core, field):
    matching = [call for call in core.call_args_list if call.args[0] == field]
    assert len(matching) == 1
    return matching[0].args[1]["value"]


def test_add_mode_merges_all_relationship_fields_in_existing_order():
    _response, core = _run({
        "list_mode": "add",
        "tags": "New tag",
        "publishers": "Orbit",
        "languages": "French",
    })

    assert _value(core, "tags") == "Science Fiction, Classic, New tag"
    assert _value(core, "publishers") == "Ace, Orbit"
    assert _value(core, "languages") == "English, French"


def test_omitted_mode_preserves_today_replace_payload_byte_for_byte():
    _response, core = _run({"tags": "  replacement, Value  "})

    assert _value(core, "tags") == "  replacement, Value  "


def test_explicit_replace_mode_is_the_same_replace_path():
    _response, core = _run({
        "list_mode": "replace",
        "authors": "Octavia Butler & N. K. Jemisin",
    })

    assert _value(core, "authors") == "Octavia Butler & N. K. Jemisin"


def test_add_mode_deduplicates_existing_and_incoming_values_case_insensitively():
    _response, core = _run({
        "list_mode": "add",
        "tags": "science fiction, Mystery, MYSTERY, classic",
    })

    assert _value(core, "tags") == "Science Fiction, Classic, Mystery"


def test_add_mode_honours_the_author_ampersand_separator():
    _response, core = _run({
        "list_mode": "add",
        "authors": "ursula k. le guin & Octavia E. Butler & OCTAVIA E. BUTLER",
    })

    assert _value(core, "authors") == "Ursula K. Le Guin & Octavia E. Butler"


def test_noop_add_does_not_rewrite_the_book():
    _response, core = _run({
        "list_mode": "add",
        "tags": "science fiction, CLASSIC, Science Fiction",
        "authors": "URSULA K. LE GUIN",
        "publishers": "ace",
        "languages": "ENGLISH",
    })

    core.assert_not_called()


def test_add_mode_does_not_change_single_value_field_semantics():
    _response, core = _run({
        "list_mode": "add",
        "title": "Replacement title",
        "series": "Replacement series",
    })

    assert _value(core, "title") == "Replacement title"
    assert _value(core, "series") == "Replacement series"
