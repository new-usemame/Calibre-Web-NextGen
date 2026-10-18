# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Matching files on a USB Kindle back to library books, and the /kindle-usb/resolve endpoint."""
import inspect
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest

from cps.services import kindle_usb as ku


def _index(*books):
    index, authors = {}, {}
    for book_id, title, names in books:
        index.setdefault(ku.name_key(title), []).append(book_id)
        authors[book_id] = ku.book_author_keys(names)
    return index, (lambda i: authors.get(i, set()))


@pytest.mark.unit
def test_name_key_ignores_case_punctuation_and_diacritics():
    assert ku.name_key("Zwyciężca: bierze wszystko!") == ku.name_key("zwyciezca bierze wszystko")
    assert ku.name_key("") == ""


@pytest.mark.unit
def test_filename_the_app_writes_matches_its_book():
    index, authors = _index((7, "Shatter Me", ["Tahereh Mafi"]))
    assert ku.match_book("Shatter Me - Tahereh Mafi.azw3", index, authors) == 7


@pytest.mark.unit
def test_sanitized_title_still_matches():
    # get_valid_filename turns ':' into '_' and drops '?'
    index, authors = _index((3, "Dune: Messiah?", ["Frank Herbert"]))
    assert ku.match_book("Dune_ Messiah - Frank Herbert.azw3", index, authors) == 3


@pytest.mark.unit
def test_title_that_contains_a_dash_matches_on_the_whole_stem():
    index, authors = _index((4, "Saga - Volume One", ["Brian K. Vaughan"]))
    assert ku.match_book("Saga - Volume One - Brian K. Vaughan.azw3", index, authors) == 4
    assert ku.match_book("Saga - Volume One.mobi", index, authors) == 4


@pytest.mark.unit
def test_same_title_is_told_apart_by_author():
    index, authors = _index((1, "Dune", ["Frank Herbert"]), (2, "Dune", ["Brian Herbert"]))
    assert ku.match_book("Dune - Brian Herbert.azw3", index, authors) == 2


@pytest.mark.unit
def test_ambiguous_bare_title_is_not_guessed():
    index, authors = _index((1, "Dune", ["Frank Herbert"]), (2, "Dune", ["Brian Herbert"]))
    assert ku.match_book("Dune.azw3", index, authors) is None


@pytest.mark.unit
def test_unknown_file_and_duplicate_suffix():
    index, authors = _index((1, "Dune", ["Frank Herbert"]))
    assert ku.match_book("Something Else.azw3", index, authors) is None
    assert ku.match_book("Dune (1).azw3", index, authors) == 1


@pytest.mark.unit
def test_pipe_escaped_commas_in_author_names():
    assert ku.name_key("William H. Keith, Jr.") in ku.book_author_keys(["William H. Keith| Jr."])


def _call(body, *, download=True, rows=(), books=(), cover="/cover/1"):
    from cps.api import actions as mod
    db = MagicMock()
    query = db.session.query.return_value
    query.filter.return_value.all.side_effect = [list(rows), list(books)]
    with flask.Flask(__name__).test_request_context(
            "/api/v1/kindle-usb/resolve", method="POST", json=body, content_type="application/json"):
        user = SimpleNamespace(is_authenticated=True, is_anonymous=False, id=1, role_download=lambda: download)
        with patch.object(mod, "current_user", user), patch.object(mod, "calibre_db", db), \
             patch("cps.api.serializers.cover_url_for", return_value=cover):
            resp = inspect.unwrap(mod.resolve_kindle_usb_files)()
    return resp


@pytest.mark.unit
def test_resolve_requires_download_role():
    assert _call({"files": ["a.azw3"]}, download=False)[1] == 403


@pytest.mark.unit
@pytest.mark.parametrize("body", [{}, {"files": "a.azw3"}, {"files": [1, 2]}, {"files": ["x" * 600]}])
def test_resolve_rejects_malformed_bodies(body):
    assert _call(body)[1] == 400


@pytest.mark.unit
def test_resolve_returns_matched_and_unmatched_in_order():
    book = SimpleNamespace(id=7, title="Shatter Me", authors=[SimpleNamespace(name="Tahereh Mafi")])
    resp = _call({"files": ["Shatter Me - Tahereh Mafi.azw3", "Unknown.pdf"]},
                 rows=[(7, "Shatter Me"), (8, "Other Book")], books=[book])
    items = json.loads(resp.get_data())["items"]
    assert [i["file"] for i in items] == ["Shatter Me - Tahereh Mafi.azw3", "Unknown.pdf"]
    assert items[0]["book"] == {"id": 7, "title": "Shatter Me", "authors": ["Tahereh Mafi"], "cover_url": "/cover/1"}
    assert items[1]["book"] is None


@pytest.mark.unit
def test_resolve_reports_a_null_cover_for_a_book_without_one():
    """The SPA must be able to render this: it falls back to a text tile instead of a cover image."""
    book = SimpleNamespace(id=7, title="Shatter Me", authors=[SimpleNamespace(name="Tahereh Mafi")])
    resp = _call({"files": ["Shatter Me - Tahereh Mafi.azw3"]}, rows=[(7, "Shatter Me")], books=[book], cover=None)
    assert json.loads(resp.get_data())["items"][0]["book"]["cover_url"] is None
