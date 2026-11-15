# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for the PR #758 follow-up (@chloeroform): OPDS feeds
with a sub-parameter should carry it in their ``<title>``.

PR #758 gave every feed a per-feed title, but sub-parameter routes only
inherited the generic parent — ``/opds/books/letter/U`` was titled
"Alphabetical Books" (indistinguishable from every other letter) and the
author/category/series *letter* endpoints were missing from the detail map
entirely, so they fell back to the bare instance name.

Now letter feeds render "Alphabetical Books (U)", by-id feeds render
"Tags: Fantasy" / "Ratings: 4.5 Stars" / locale-aware
"Languages: German", and the three missing letter endpoints inherit their
parents. The "00" pseudo-letter (the "All" listing) keeps the bare parent
title, and an unknown entity id degrades to the parent title rather than
erroring.

Behavioural tests fail on pre-fix code (helpers absent); source-pins fail
if a route stops passing its qualified title.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def opds_module():
    import cps.opds as opds

    return opds


@pytest.fixture()
def babel_request_ctx():
    """Minimal Flask + babel request context whose endpoint can be steered
    per-test by assigning ``request.url_rule`` (Flask derives ``endpoint``
    from it)."""
    from flask import Flask
    from flask_babel import Babel

    app = Flask(__name__)
    Babel(app)
    with app.test_request_context("/"):
        yield app


def _set_endpoint(endpoint):
    from flask import request
    from werkzeug.routing import Rule

    request.url_rule = Rule("/_test", endpoint=endpoint)


# ---------------------------------------------------------------------------
# Letter qualifier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "endpoint,letter,expected",
    [
        ("opds.feed_letter_books", "U", "Alphabetical Books (U)"),
        ("opds.feed_letter_author", "V", "Authors (V)"),
        ("opds.feed_letter_category", "F", "Tags (F)"),
        ("opds.feed_letter_series", "D", "Series (D)"),
        # the "00" pseudo-letter is the unfiltered "All" listing
        ("opds.feed_letter_books", "00", "Alphabetical Books"),
        ("opds.feed_letter_author", "00", "Authors"),
    ],
)
def test_letter_feeds_carry_their_letter(opds_module, babel_request_ctx, endpoint, letter, expected):
    _set_endpoint(endpoint)
    assert str(opds_module._feed_title_with_letter(letter)) == expected


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("opds.feed_letter_author", "Authors"),
        ("opds.feed_letter_category", "Tags"),
        ("opds.feed_letter_series", "Series"),
    ],
)
def test_previously_unmapped_letter_endpoints_inherit_parent(opds_module, babel_request_ctx, endpoint, expected):
    """These three fell back to the bare instance name before the fix."""
    assert str(opds_module._opds_feed_title_for_endpoint(endpoint)) == expected


# ---------------------------------------------------------------------------
# Entity-name qualifier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "endpoint,name,expected",
    [
        ("opds.feed_category", "Fantasy", "Tags: Fantasy"),
        ("opds.feed_author", "Jane Austen", "Authors: Jane Austen"),
        ("opds.feed_format", "EPUB", "File formats: EPUB"),
        # unknown entity (deleted / foreign id) keeps the parent title
        ("opds.feed_category", None, "Tags"),
        ("opds.feed_series", "  ", "Series"),
    ],
)
def test_by_id_feeds_carry_the_entity_name(opds_module, babel_request_ctx, endpoint, name, expected):
    _set_endpoint(endpoint)
    assert str(opds_module._feed_title_with_name(name)) == expected


def test_titleless_endpoint_yields_none_not_error(opds_module, babel_request_ctx):
    _set_endpoint("opds.feed_index")
    assert opds_module._feed_title_with_letter("U") is None
    assert opds_module._feed_title_with_name(None) is None


# ---------------------------------------------------------------------------
# Source-pins: routes pass their qualified titles
# ---------------------------------------------------------------------------

def _route_source(opds_module, name):
    return inspect.getsource(getattr(opds_module, name))


@pytest.mark.parametrize(
    "route",
    ["feed_letter_books", "feed_letter_author", "feed_letter_category", "feed_letter_series"],
)
def test_letter_routes_pass_qualified_title(opds_module, route):
    src = _route_source(opds_module, route)
    assert "_feed_title_with_letter(book_id)" in src, (
        f"{route} must pass feed_title=_feed_title_with_letter(book_id) so the "
        "letter filter shows up in the reader's feed list."
    )


def test_dataset_renderer_passes_entity_name(opds_module):
    src = _route_source(opds_module, "render_xml_dataset")
    assert "_feed_title_with_name(_dataset_display_name(" in src


def test_ratings_display_name_renders_stars(opds_module):
    src = _route_source(opds_module, "_dataset_display_name")
    assert "Stars" in src and "_format_opds_rating" in src, (
        "Ratings rows have no .name — the qualified title must render "
        "'<n> Stars' like the ratings index does."
    )


def test_format_and_language_routes_pass_qualified_title(opds_module):
    assert "_feed_title_with_name(book_id.upper())" in _route_source(opds_module, "feed_format")
    assert "_language_display_name(book_id)" in _route_source(opds_module, "feed_languages")


def test_language_display_name_is_locale_aware(opds_module):
    src = _route_source(opds_module, "_language_display_name")
    assert "get_language_name" in src and "get_locale" in src
