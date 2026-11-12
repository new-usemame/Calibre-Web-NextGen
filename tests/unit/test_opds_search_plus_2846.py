# Calibre-Web-NextGen
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2846: searching "C++" through the advertised OPDS search template searched "C".

The catalog advertised the path form ``/opds/search/{searchTerms}``. ``+`` is a
legal path character, so a client that encodes a path segment the standard way
leaves it alone, and the handler then read it as a space (``unquote_plus``, kept
for an old reader that sends ``+`` for spaces in the path). The query form
``/opds/search?query={searchTerms}`` is where ``+`` means space and clients
encode a literal plus as ``%2B``. These tests play the client: read the
advertised template, fill it in the way that URL position is encoded, follow it,
and check which term the search ran with.
"""

import xml.etree.ElementTree as ET
from urllib.parse import quote, urlsplit

import pytest

from tests.unit.opds_feed_world import opds_route_app

pytestmark = pytest.mark.unit

ATOM = "{http://www.w3.org/2005/Atom}"
OSD = "{http://a9.com/-/spec/opensearch/1.1/}"


@pytest.fixture
def world(monkeypatch):
    from cps import opds

    searched = []

    def fake_feed_search(term):
        searched.append(term)
        return "ok"

    monkeypatch.setattr(opds, "feed_search", fake_feed_search)
    monkeypatch.setattr(opds.config, "config_calibre_web_title", "Test", raising=False)
    app = opds_route_app()
    return app, app.test_client(), searched


def _fill(template, term):
    """Fill ``{searchTerms}`` the way a standards-following client does for
    where the marker sits: a path segment keeps sub-delims like ``+``; a query
    value is form-encoded, so a literal ``+`` must become ``%2B``."""
    marker = "{searchTerms}"
    in_query = "?" in template and template.index("?") < template.index(marker)
    encoded = quote(term, safe="") if in_query else quote(term, safe="!$&'()*+,;=:@")
    return template.replace(marker, encoded)


def _advertised_templates(app, client, lang_suffix=""):
    """Every search template the catalog advertises: the Atom ``rel=search``
    link on the root and on a feed, and both ``<Url>`` entries of the OSD."""
    from flask import render_template

    templates = {}
    with app.test_request_context("/opds" + lang_suffix):
        root = ET.fromstring(render_template(
            "index.xml", entries=[], current_time="t", instance="Test"))
    with app.test_request_context("/opds/new" + lang_suffix):
        feed = ET.fromstring(render_template(
            "feed.xml", entries=[], current_time="t", pagination=None,
            instance="Test", feed_title="New", listelements=[]))
    for name, doc in (("index.xml", root), ("feed.xml", feed)):
        links = [ln.get("href") for ln in doc.iter(ATOM + "link")
                 if ln.get("rel") == "search" and ln.get("type") == "application/atom+xml"]
        assert len(links) == 1, name
        templates[name] = links[0]
    osd = ET.fromstring(client.get("/opds/osd" + lang_suffix).data)
    for url in osd.iter(OSD + "Url"):
        templates["osd.xml " + url.get("type")] = url.get("template")
    assert len(templates) == 4, templates
    return templates


@pytest.mark.parametrize("term", ["C++", "C++ primer", "a+b", "Tom & Jerry", "100%"])
def test_every_advertised_search_template_searches_the_typed_term(world, term):
    app, client, searched = world
    for where, template in _advertised_templates(app, client).items():
        searched.clear()
        resp = client.get(_fill(template, term))
        assert resp.status_code == 200, (where, template, resp.status_code)
        assert searched == [term], (
            "%s advertises %r; filled in for %r the server searched %r"
            % (where, template, term, searched))


def test_advertised_templates_keep_the_language_the_client_asked_for(world):
    app, client, searched = world
    for where, template in _advertised_templates(app, client, "?lang=hu").items():
        assert "lang=hu" in template, (where, template)
        searched.clear()
        url = _fill(template, "C++")
        assert client.get(url).status_code == 200
        assert searched == ["C++"], (where, url, searched)
        assert "lang=hu" in urlsplit(url).query, (where, url)


def test_path_form_still_reads_plus_as_space_for_readers_that_send_it(world):
    """The path route stays for readers that hardcode it and send ``+`` for
    spaces; they keep finding their book."""
    _app, client, searched = world
    assert client.get("/opds/search/harry+potter").status_code == 200
    assert client.get("/opds/search/harry+potter?lang=hu").status_code == 200
    assert client.get("/opds/search/C%2B%2B").status_code == 200
    assert searched == ["harry potter", "harry potter", "C++"]


def test_path_form_works_without_the_bundled_servers_raw_uri(world):
    """``RAW_URI``/``REQUEST_URI`` are server extensions, not WSGI. Under a
    server that sets neither, the path route used to raise ``KeyError``."""
    app, client, searched = world
    inner = app.wsgi_app

    def plain_wsgi(environ, start_response):
        environ.pop("RAW_URI", None)
        environ.pop("REQUEST_URI", None)
        return inner(environ, start_response)

    app.wsgi_app = plain_wsgi
    resp = client.get("/opds/search/harry+potter?lang=hu")
    assert resp.status_code == 200
    assert searched == ["harry potter"]
