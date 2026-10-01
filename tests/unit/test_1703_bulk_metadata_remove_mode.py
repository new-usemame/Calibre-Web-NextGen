# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for removing values in a bulk metadata edit (#1703).

The bulk SPA sends the same ``list_mode: remove`` request to every selected
book. Each book must lose only the named values, keep the rest in order, and be
left completely untouched when it carries none of them.
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
        authors=[_row("Ursula K. Le Guin"), _row("Octavia E. Butler")],
        series=[],
        series_index=1.0,
        tags=[_row("Science Fiction"), _row("To Read"), _row("Classic")],
        publishers=[_row("Ace")],
        languages=[SimpleNamespace(lang_code="eng")],
        comments=[],
        ratings=[],
        pubdate=None,
        identifiers=[],
    )


def _run(body, book=None):
    from cps.api import edit as mod

    app = flask.Flask(__name__)
    book = book or _book()
    database = SimpleNamespace(
        get_filtered_book=lambda *args, **kwargs: book,
        get_cc_columns=lambda *args, **kwargs: [],
    )
    editor = SimpleNamespace(is_authenticated=True, is_anonymous=False, role_edit=lambda: True)
    core = MagicMock(return_value=_SUCCESS)
    with app.test_request_context(
        "/api/v1/books/5/metadata", method="POST", json=body,
        content_type="application/json",
    ):
        with patch.object(mod, "current_user", editor), \
             patch.object(mod, "calibre_db", database), \
             patch.object(mod, "edit_book_param", core), \
             patch.object(mod, "get_locale", return_value="en"), \
             patch.object(
                 mod.isoLanguages,
                 "get_language_name",
                 side_effect=lambda _locale, code: {"eng": "English"}[code],
             ):
            response = inspect.unwrap(mod.update_metadata)(5)
    if isinstance(response, tuple):
        response, status = response
    else:
        status = response.status_code
    return status, response.get_json(), core


def _value(core, field):
    matching = [call for call in core.call_args_list if call.args[0] == field]
    assert len(matching) == 1
    return matching[0].args[1]["value"]


def test_remove_drops_only_the_named_tag_and_keeps_the_rest_in_order():
    _status, _body, core = _run({"list_mode": "remove", "tags": "to read"})

    assert _value(core, "tags") == "Science Fiction, Classic"
    assert [call.args[0] for call in core.call_args_list] == ["tags"]


def test_remove_accepts_several_tags_and_ignores_ones_the_book_lacks():
    _status, _body, core = _run({"list_mode": "remove", "tags": "CLASSIC, Horror,  science fiction "})

    assert _value(core, "tags") == "To Read"


def test_removing_every_tag_clears_the_field():
    _status, _body, core = _run({"list_mode": "remove", "tags": "Science Fiction, To Read, Classic"})

    assert _value(core, "tags") == ""


def test_remove_of_a_tag_the_book_lacks_does_not_rewrite_the_book():
    _status, _body, core = _run({"list_mode": "remove", "tags": "Horror", "publishers": "Orbit"})

    core.assert_not_called()


def test_remove_honours_the_author_ampersand_separator():
    _status, _body, core = _run({"list_mode": "remove", "authors": "octavia e. butler"})

    assert _value(core, "authors") == "Ursula K. Le Guin"


def test_remove_never_leaves_a_book_without_an_author():
    _status, body, core = _run({
        "list_mode": "remove",
        "authors": "Ursula K. Le Guin & Octavia E. Butler",
    })

    core.assert_not_called()
    assert "authors" in body["errors"]


def test_remove_rejects_fields_it_cannot_apply_instead_of_replacing_them():
    status, body, core = _run({"list_mode": "remove", "tags": "Classic", "series": "Earthsea"})

    assert status == 400
    assert body["error"]["code"] == "invalid_request"
    core.assert_not_called()
