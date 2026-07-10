# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #750 (@chloeroform): every OPDS feed
rendered ``<title>`` as the bare instance name.

Every OPDS feed funnels through ``render_xml_template('feed.xml', ...)``,
which hardcoded ``instance=config.config_calibre_web_title`` and nothing
else, and ``feed.xml`` rendered ``<title>{{ instance }}</title>``. So a
reader that keys its feed list on the Atom ``<title>`` (some do) showed a
wall of identical entries — Read Books, every shelf, every author list,
search results, all named just "Instance".

The fix resolves a per-feed title from the single ``OPDS_ROOT_ENTRY_DEFS``
source of truth (keyed by Flask endpoint), lets the named dynamic feeds
(shelf, magic shelf, search) pass an explicit ``feed_title``, and renders
``<title>Instance - Feed Name</title>`` in ``feed.xml``.

These tests pin:
1. ``_opds_feed_title_for_endpoint`` maps the top-level, index, and detail
   feeds to their SSOT title and returns ``None`` for the catalog root /
   unknown endpoints (behavioural, in an app context).
2. The detail-endpoint map only references real ``OPDS_ROOT_ENTRY_DEFS``
   keys (no dangling entry that would silently resolve to ``None``).
3. ``render_xml_template`` resolves ``feed_title`` from the request
   endpoint when the caller doesn't pass one (source-pin).
4. ``feed.xml`` renders ``instance - feed_title`` when a title is present
   and falls back to the bare instance otherwise (source-pin).
5. The named dynamic feeds pass an explicit ``feed_title`` (source-pins on
   ``feed_shelf`` / ``feed_magic_shelf`` / ``feed_search``).

Every assertion below fails on ``main`` (no resolver, no template branch,
no explicit titles) and passes on the branch.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
FEED_XML = REPO_ROOT / "cps" / "templates" / "feed.xml"


@pytest.fixture(scope="module")
def opds_module():
    import cps.opds as opds  # noqa: WPS433 (import inside fixture is intentional)

    return opds


@pytest.fixture()
def babel_app_context():
    """A minimal Flask + flask_babel context so ``gettext`` resolves.

    We don't need the full Calibre-Web app — the resolver only calls
    ``gettext`` on the SSOT title strings, which returns the source string
    unchanged when no catalog is loaded.
    """
    from flask import Flask
    from flask_babel import Babel

    app = Flask(__name__)
    Babel(app)
    with app.test_request_context("/"):
        yield app


# ---------------------------------------------------------------------------
# 1. Resolver behaviour (SSOT-derived), in an app context
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("opds.feed_read_books", "Read Books"),
        ("opds.feed_unread_books", "Unread Books"),
        ("opds.feed_hot", "Hot Books"),
        ("opds.feed_new", "Recently added Books"),
        ("opds.feed_discover", "Random Books"),
        ("opds.feed_best_rated", "Top Rated Books"),
        ("opds.feed_booksindex", "Alphabetical Books"),
        # index feeds
        ("opds.feed_authorindex", "Authors"),
        ("opds.feed_seriesindex", "Series"),
        ("opds.feed_shelfindex", "Shelves"),
        ("opds.feed_magic_shelfindex", "Magic Shelves"),
        # per-entity detail feeds inherit the parent title
        ("opds.feed_author", "Authors"),
        ("opds.feed_series", "Series"),
        ("opds.feed_category", "Categories"),
        ("opds.feed_publisher", "Publishers"),
        ("opds.feed_format", "File formats"),
        ("opds.feed_languages", "Languages"),
        ("opds.feed_ratings", "Ratings"),
        ("opds.feed_letter_books", "Alphabetical Books"),
    ],
)
def test_resolver_maps_endpoint_to_ssot_title(opds_module, babel_app_context, endpoint, expected):
    assert str(opds_module._opds_feed_title_for_endpoint(endpoint)) == expected


@pytest.mark.parametrize("endpoint", ["opds.feed_index", "opds.feed_osd", None, "opds.does_not_exist"])
def test_resolver_returns_none_for_titleless_feeds(opds_module, babel_app_context, endpoint):
    assert opds_module._opds_feed_title_for_endpoint(endpoint) is None


# ---------------------------------------------------------------------------
# 2. The detail map has no dangling keys (would silently resolve to None)
# ---------------------------------------------------------------------------

def test_detail_endpoint_map_references_only_real_root_keys(opds_module):
    defs_keys = set(opds_module.OPDS_ROOT_ENTRY_DEFS.keys())
    for endpoint, root_key in opds_module._OPDS_DETAIL_ENDPOINT_ROOT_KEY.items():
        assert root_key in defs_keys, (
            f"{endpoint!r} maps to unknown root key {root_key!r}; "
            "it would resolve to a bare instance title"
        )


# ---------------------------------------------------------------------------
# 3. render_xml_template resolves feed_title from the endpoint
# ---------------------------------------------------------------------------

def test_render_xml_template_resolves_feed_title(opds_module):
    src = inspect.getsource(opds_module.render_xml_template)
    assert "feed_title=None" in src, "render_xml_template must accept an explicit feed_title override"
    assert "_opds_feed_title_for_endpoint(request.endpoint)" in src, (
        "render_xml_template must resolve the title from the request endpoint"
    )
    assert "feed_title=feed_title" in src, "the resolved title must be passed into the template"


# ---------------------------------------------------------------------------
# 4. feed.xml renders instance - feed_title with a fallback
# ---------------------------------------------------------------------------

def test_feed_xml_title_appends_feed_title():
    src = FEED_XML.read_text()
    # The <title> must be conditional on feed_title and fall back to instance.
    title_line = next(line for line in src.splitlines() if "<title>" in line)
    assert "feed_title" in title_line, "feed.xml <title> must use feed_title"
    assert "instance" in title_line, "feed.xml <title> must still fall back to the instance name"
    # The author/provider name stays the instance (it is the catalog provider).
    assert re.search(r"<name>\{\{\s*instance\s*\}\}</name>", src)


# ---------------------------------------------------------------------------
# 5. Named dynamic feeds pass an explicit feed_title
# ---------------------------------------------------------------------------

def test_shelf_feed_names_after_shelf(opds_module):
    src = inspect.getsource(opds_module.feed_shelf)
    assert "feed_title=shelf.name" in src


def test_magic_shelf_feed_names_after_shelf(opds_module):
    src = inspect.getsource(opds_module.feed_magic_shelf)
    assert "feed_title=shelf.name" in src


def test_search_feed_names_after_query(opds_module):
    src = inspect.getsource(opds_module.feed_search)
    # Reuses the already-extracted "Search" msgid, appends the term.
    assert "feed_title=" in src and "_('Search')" in src
