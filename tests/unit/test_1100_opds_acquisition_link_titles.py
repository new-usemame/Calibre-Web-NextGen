# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork issue #1100: an OPDS entry for a book that has both an EPUB and a
KEPUB listed two acquisition links with the same ``type``
(``application/epub+zip``, which ``cps/__init__.py`` maps ``.kepub`` to)
and no ``title``, so clients showed both downloads as "epub".

Each acquisition link must carry a ``title`` naming its format. The tests
render the real ``feed.xml`` template and parse the resulting Atom.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

pytestmark = pytest.mark.unit

ATOM = "{http://www.w3.org/2005/Atom}"
ACQ = "http://opds-spec.org/acquisition"


@pytest.fixture()
def render():
    """Render feed.xml for a book with the given formats (opds_feed_world)."""
    from tests.unit.opds_feed_world import FeedRenderer

    return FeedRenderer().entry_with_formats


def _acquisition_links(root):
    return [l for l in root.iter(ATOM + "link") if l.get("rel") == ACQ]


def test_epub_and_kepub_links_have_distinct_titles(render):
    links = _acquisition_links(render(["EPUB", "KEPUB"]))
    assert [l.get("title") for l in links] == ["EPUB", "KEPUB"]
    # The MIME type stays shared; only the title and href tell them apart.
    assert {l.get("type") for l in links} == {"application/epub+zip"}
    assert [l.get("href").split("/")[-2] for l in links] == ["epub", "kepub"]


def test_every_format_link_is_titled_in_upper_case(render):
    links = _acquisition_links(render(["PDF", "epub"]))
    assert [l.get("title") for l in links] == ["PDF", "EPUB"]
