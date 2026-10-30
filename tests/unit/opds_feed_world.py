# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""A shared app for rendering the real OPDS ``feed.xml`` template.

It renders the way the app does: Flask's environment (autoescape on for .xml)
with the real ``cps.jinjia`` filters, including the real ``mimetype`` mapping.
Only the route-level helpers the template calls are stubbed. Build it here
rather than in a test file, so OPDS template tests share one app
(tests/fitness/test_no_new_flask_apps.py).
"""

from __future__ import annotations

import datetime
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

TEMPLATES = Path(__file__).resolve().parents[2] / "cps" / "templates"


def _url_for(endpoint, **kw):
    if endpoint == "opds.opds_download_link":
        return "/opds/download/{}/{}/".format(kw.get("book_id"), kw.get("book_format"))
    return "/x"


class FeedRenderer:
    def __init__(self):
        from flask import Flask
        from flask_babel import Babel
        from jinja2 import FileSystemLoader
        from cps.jinjia import jinjia

        self.app = Flask(__name__)
        Babel(self.app)
        self.app.register_blueprint(jinjia)
        self.app.jinja_loader = FileSystemLoader(str(TEMPLATES))
        self.app.jinja_env.globals.update(
            url_for=_url_for,
            _is_real_pubdate=lambda d: False,
            opds_paged_url=lambda o: "/x",
            opds_search_url_path=lambda: "/x",
        )

    def render(self, entries):
        """Render feed.xml with ``entries`` and return the parsed Atom root."""
        from flask import render_template

        with self.app.test_request_context("/opds/new"):
            xml = render_template(
                "feed.xml", entries=entries, current_time="2026-10-29T00:00:00+00:00",
                pagination=None, instance="Test", feed_title="New", listelements=[],
            )
        return ET.fromstring(xml)

    def entry_with_formats(self, formats):
        """Render one book whose files are ``formats`` (e.g. ["EPUB", "KEPUB"])."""
        book = SimpleNamespace(
            id=1, uuid="u-1", title="Life of Pi", atom_timestamp=datetime.datetime(2026, 10, 27).isoformat(),
            authors=[], publishers=[], languages=[], tags=[], series=[], comments=[], has_cover=False,
            data=[SimpleNamespace(format=f, uncompressed_size=100) for f in formats],
        )
        return self.render([SimpleNamespace(Books=book)])
