# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork issue #1100: an OPDS entry for a book that has both an EPUB and a
KEPUB listed two acquisition links with the same ``type``
(``application/epub+zip``, deliberately, see ``cps/__init__.py``) and no
``title``, so clients showed both downloads as "epub".

Each acquisition link must carry a ``title`` naming its format. The tests
render the real ``feed.xml`` template and parse the resulting Atom.
"""

from __future__ import annotations

import datetime
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

pytestmark = pytest.mark.unit

TEMPLATES = Path(__file__).resolve().parents[2] / "cps" / "templates"
ATOM = "{http://www.w3.org/2005/Atom}"
ACQ = "http://opds-spec.org/acquisition"


def _render(formats):
    # Flask autoescapes .xml templates; render the same way.
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.filters.update(
        mimetype=lambda v: "application/epub+zip" if v in ("epub", "kepub") else "application/pdf",
        formatfloat=lambda v, n=2: str(v),
        first_sentence=lambda v: v,
    )
    env.globals.update(
        url_for=lambda endpoint, **kw: "/opds/download/{}/{}/".format(kw.get("book_id"), kw.get("book_format"))
        if endpoint == "opds.opds_download_link" else "/x",
        _=lambda s: s,
        _is_real_pubdate=lambda d: False,
        opds_paged_url=lambda o: "/x",
        opds_search_url_path=lambda: "/x",
    )
    book = SimpleNamespace(
        id=1, uuid="u-1", title="Life of Pi", atom_timestamp=datetime.datetime(2026, 10, 27).isoformat(),
        authors=[], publishers=[], languages=[], tags=[], series=[], comments=[], has_cover=False,
        data=[SimpleNamespace(format=f, uncompressed_size=100) for f in formats],
    )
    xml = env.get_template("feed.xml").render(
        entries=[SimpleNamespace(Books=book)], current_time="2026-10-29T00:00:00+00:00",
        request=SimpleNamespace(script_root="", full_path="/opds/new?"),
        pagination=None, instance="Test", feed_title="New", listelements=[],
    )
    return ET.fromstring(xml)


def _acquisition_links(root):
    return [l for l in root.iter(ATOM + "link") if l.get("rel") == ACQ]


def test_epub_and_kepub_links_have_distinct_titles():
    links = _acquisition_links(_render(["EPUB", "KEPUB"]))
    assert [l.get("title") for l in links] == ["EPUB", "KEPUB"]
    # The MIME type stays shared; only the title and href tell them apart.
    assert {l.get("type") for l in links} == {"application/epub+zip"}
    assert [l.get("href").split("/")[-2] for l in links] == ["epub", "kepub"]


def test_every_format_link_is_titled_in_upper_case():
    links = _acquisition_links(_render(["PDF", "epub"]))
    assert [l.get("title") for l in links] == ["PDF", "EPUB"]
