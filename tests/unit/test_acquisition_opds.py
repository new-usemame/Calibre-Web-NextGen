# SPDX-License-Identifier: GPL-3.0-or-later
"""Catalog wire examples protect protocol meaning, resource bounds and safe offers."""
import json
import importlib.util
import importlib
import sys
from pathlib import Path
from dataclasses import FrozenInstanceError

import pytest

# Load the real pure package under a private name: cps.services.__init__ eagerly
# imports optional Gmail/OpenSSL integrations unrelated to catalog parsing.
_package_path = Path(__file__).resolve().parents[2] / "cps/services/acquisition"
_spec = importlib.util.spec_from_file_location(
    "_cwng_acquisition_parser_tests", _package_path / "__init__.py",
    submodule_search_locations=[str(_package_path)])
_package = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _package
_spec.loader.exec_module(_package)
_parser = importlib.import_module(_spec.name + ".opds")
CatalogParseError = _parser.CatalogParseError
ParseLimits = _parser.ParseLimits
parse_catalog = _parser.parse_catalog
parse_search_description = _parser.parse_search_description

BASE = "https://catalog.example/library/index.xml"
# Independent wire layouts: an Atom acquisition feed and a prefixed navigation
# feed with different namespace prefixes, media parameters and xml:base scope.
ACQUISITIONS = b'''<feed xmlns="http://www.w3.org/2005/Atom"
 xmlns:d="http://purl.org/dc/terms/" xml:base="../">
<title>Public books</title>
<link rel="search" type="application/opensearchdescription+xml" href="search.xml"/>
<link rel="next" href="?page=2"/>
<entry xml:base="editions/"><id>urn:book:1</id><title>Example edition</title>
<author><name>A Writer</name></author><d:language>en</d:language><d:identifier>urn:isbn:123</d:identifier>
<summary type="html">&lt;p&gt;An &lt;b&gt;illustrated&lt;/b&gt; edition.&lt;/p&gt;</summary>
<link rel="http://opds-spec.org/acquisition/open-access" type="application/epub+zip" href="one.epub"/>
<link rel="http://opds-spec.org/acquisition/buy" type="text/html" href="buy"/>
</entry></feed>'''
NAVIGATION = b'''<a:feed xmlns:a="http://www.w3.org/2005/Atom"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xml:base="https://other.example/opds/">
<a:title>Browse</a:title><a:entry xml:base="authors/">
<a:id>urn:authors</a:id><a:title>Authors</a:title>
<a:link href="all" type='application/atom+xml;profile=opds-catalog;kind=navigation'/>
</a:entry><a:entry><a:id>urn:sample</a:id><a:title>Sample</a:title><dc:language>fr</dc:language>
<a:link rel="http://opds-spec.org/acquisition/sample" type="application/epub+zip" href="sample.epub"/>
</a:entry></a:feed>'''


def opds2():
    return {"metadata": {"title": "Example catalog"},
            "links": [{"rel": ["self"], "href": "./", "type": "application/opds+json"},
                      {"rel": "search", "href": "search{?query,title}", "templated": True,
                       "type": "application/opds+json"}, {"rel": "next", "href": "?page=2"}],
            "publications": [{"metadata": {"title": "An edition", "identifier": "urn:edition:1",
                "author": [{"name": "A Writer", "identifier": "urn:person:1"}],
                "translator": "B Translator", "language": ["en", "fr"],
                "description": "<p>Readable <b>text</b>.</p><script>alert(1)</script>"},
                "links": [{"href": "book.pdf", "rel": "download", "type": "application/pdf"},
                          {"href": "loan", "rel": "borrow", "type": "text/html",
                           "properties": {"indirectAcquisition": [{"type": "application/vnd.readium.lcp.license.v1.0+json",
                              "child": [{"type": "application/epub+zip"}]}]}}]}],
            "groups": [{"metadata": {"title": "Browse authors"}, "navigation": [{"title": "All", "href": "authors"}]}],
            "facets": [{"metadata": {"title": "Language"}, "links": [{"title": "French", "href": "?lang=fr"}]}]}


def test_atom_acquisitions_resolve_scoped_bases_without_making_purchase_downloadable():
    catalog = parse_catalog(ACQUISITIONS, BASE)
    book, = catalog.publications
    assert (catalog.title, book.identifier, book.title) == ("Public books", "urn:book:1", "Example edition")
    assert book.contributors[0].name == "A Writer"
    assert book.languages == ("en",)
    assert book.identifiers == ("urn:isbn:123",)
    assert book.description == "An illustrated edition."
    assert book.offers[0].link.href == "https://catalog.example/editions/one.epub"
    assert [offer.is_direct_download for offer in book.offers] == [True, False]
    assert catalog.searches[0].syntax == "description"
    assert catalog.searches[0].link.href == "https://catalog.example/search.xml"
    assert next(link.href for link in catalog.links if "next" in link.relations) == "https://catalog.example/?page=2"


def test_prefixed_navigation_feed_does_not_turn_navigation_or_samples_into_full_books():
    catalog = parse_catalog(NAVIGATION, BASE)
    assert [(link.title, link.href) for link in catalog.navigation] == [("Authors", "https://other.example/opds/authors/all")]
    book, = catalog.publications
    assert book.languages == ("fr",)
    assert book.offers[0].relation == "preview"
    assert not catalog.capabilities.direct_download
    assert catalog.capabilities.browse and not catalog.capabilities.search


def test_json_preserves_edition_contributors_groups_facets_and_indirect_acquisition():
    catalog = parse_catalog(json.dumps(opds2()), BASE)
    book, = catalog.publications
    assert book.identifier == "urn:edition:1"
    assert [(p.name, p.role) for p in book.contributors] == [("A Writer", "author"), ("B Translator", "translator")]
    assert book.languages == ("en", "fr")
    assert book.description == "Readable text."
    assert book.offers[0].is_direct_download
    assert book.offers[1].relation == "borrow" and not book.offers[1].is_direct_download
    assert book.offers[1].indirect_types[-1] == "application/epub+zip"
    assert catalog.groups[0].navigation[0].href == "https://catalog.example/library/authors"
    assert catalog.facets[0].links[0].title == "French"
    assert catalog.searches[0].syntax == "uri-template"
    assert catalog.searches[0].link.href.endswith("search{?query,title}")
    with pytest.raises(FrozenInstanceError):
        book.title = "Changed"
    assert isinstance(catalog.publications, tuple)


def test_opensearch_metadata_is_preserved_without_guessing_an_expansion():
    description = b'''<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/" xml:base="/catalog/">
    <Url type="application/atom+xml;profile=opds-catalog" template="search?q={searchTerms}&amp;page={startPage?}"/>
    <Url type="text/html" template="https://example.com/search?q={searchTerms}"/>
    </OpenSearchDescription>'''
    search, = parse_search_description(description, BASE)
    assert search.syntax == "opensearch"
    assert search.link.href == "https://catalog.example/catalog/search?q={searchTerms}&page={startPage?}"
    data = opds2()
    data["links"][1].pop("templated")
    assert parse_catalog(json.dumps(data), BASE).searches[0].syntax == "unsupported"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_dtd_entities_are_rejected_even_when_not_utf8(encoding):
    document = '''<?xml version="1.0" encoding="ENC"?>
    <!DOCTYPE feed [<!ENTITY name "expanded">]>
    <feed xmlns="http://www.w3.org/2005/Atom"><title>&name;</title></feed>'''.replace("ENC", encoding)
    with pytest.raises(CatalogParseError, match="DTD"):
        parse_catalog(document.encode(encoding), BASE)


@pytest.mark.parametrize("payload, limits, message", [
    (ACQUISITIONS, ParseLimits(max_bytes=30), "byte"),
    (ACQUISITIONS, ParseLimits(max_depth=2), "structure"),
    (ACQUISITIONS, ParseLimits(max_nodes=2), "structure"),
    (ACQUISITIONS, ParseLimits(max_links=1), "links"),
    (NAVIGATION, ParseLimits(max_publications=1), "publications"),
    (ACQUISITIONS, ParseLimits(max_text=5), "text"),
    (json.dumps(opds2()), ParseLimits(max_depth=3), "structure"),
    (json.dumps(opds2()), ParseLimits(max_nodes=3), "structure"),
])
def test_remote_resource_limits_fail_explicitly_instead_of_truncating(payload, limits, message):
    with pytest.raises(CatalogParseError, match=message):
        parse_catalog(payload, BASE, limits=limits)


@pytest.mark.parametrize("payload", [
    b'{"metadata":{},"metadata":{}}', b'{"x":NaN}', b'{"metadata":[],"publications":[]}',
    b'{"metadata":{"title":"Broken"},"publications":"oops"}', b'<html><title>Login</title></html>',
])
def test_malformed_or_login_documents_are_not_empty_successful_catalogs(payload):
    with pytest.raises(CatalogParseError):
        parse_catalog(payload, BASE)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "data:application/epub+zip;base64,aGVsbG8=", "javascript:alert(1)", "https://user:secret@example.com/x", "https://example.com:broken/", "https://example.com/\\path"])
def test_unsafe_url_syntax_is_rejected_without_echoing_provider_secrets(url):
    data = opds2()
    data["publications"][0]["links"][0]["href"] = url
    with pytest.raises(CatalogParseError) as error:
        parse_catalog(json.dumps(data), BASE)
    assert url not in str(error.value)


@pytest.mark.parametrize("relation", ["buy", "borrow", "preview", "subscribe"])
@pytest.mark.parametrize("media_type", ["application/epub+zip", "application/pdf", "text/html"])
def test_non_download_relations_never_become_automatic_downloads(relation, media_type):
    data = opds2()
    data["publications"][0]["links"] = [{"href": "item", "rel": relation, "type": media_type}]
    assert not parse_catalog(json.dumps(data), BASE).capabilities.direct_download


def test_empty_xml_base_inherits_parent_and_xhtml_description_is_plain_visible_text():
    document = b'''<feed xmlns="http://www.w3.org/2005/Atom" xml:base="/books/">
    <title>Books</title><entry xml:base=""><id>urn:xhtml</id><title>Example</title>
    <summary type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><p>A <em>visible</em> summary.</p><script>hidden()</script></div></summary>
    <link rel="http://opds-spec.org/acquisition/open-access" href="item.epub" type="application/epub+zip"/>
    </entry></feed>'''
    book, = parse_catalog(document, BASE).publications
    assert book.offers[0].link.href == "https://catalog.example/books/item.epub"
    assert book.description == "A visible summary."


def test_indirect_or_templated_download_link_never_becomes_a_direct_file():
    data = opds2()
    links = data["publications"][0]["links"]
    links[0]["templated"] = True
    links[1]["rel"] = "download"
    links[1]["type"] = "application/epub+zip"
    assert not parse_catalog(json.dumps(data), BASE).capabilities.direct_download


def test_generic_acquisition_is_a_complete_file_candidate_without_promising_open_access():
    # OPDS1.2 section5.2.1 and acquisition-feed example use the generic relation
    # with an EPUB representation. Authentication belongs to the connection.
    wire = ACQUISITIONS.replace(b"acquisition/open-access", b"acquisition")
    catalog = parse_catalog(wire, BASE)
    offer = catalog.publications[0].offers[0]
    assert offer.relation == "acquisition"
    assert offer.is_direct_download
    assert catalog.capabilities.direct_download
    data = opds2()
    data["publications"][0]["links"][0]["rel"] = "acquisition"
    assert parse_catalog(json.dumps(data), BASE).publications[0].offers[0].is_direct_download


def test_internal_url_tokens_do_not_appear_in_dataclass_representations():
    data = opds2()
    data["publications"][0]["links"][0]["href"] = "https://files.example/book.epub?key=DOWNLOAD_SECRET"
    catalog = parse_catalog(json.dumps(data), BASE + "?key=CATALOG_SECRET")
    assert "DOWNLOAD_SECRET" not in repr(catalog)
    assert "CATALOG_SECRET" not in repr(catalog)
    assert "DOWNLOAD_SECRET" in catalog.publications[0].offers[0].link.href
    assert "CATALOG_SECRET" in catalog.source_url


@pytest.mark.parametrize("payload", [ACQUISITIONS, b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Empty</title></feed>', json.dumps({"metadata": {"title": "Empty"}, "publications": []}), json.dumps(dict(opds2(), navigation=[], groups=[]))], ids=["atom-books", "atom-empty", "json-empty", "json-books"])
def test_valid_catalog_can_be_browsed_without_navigation_links(payload):
    assert parse_catalog(payload, BASE).capabilities.browse


def test_malformed_url_token_is_absent_from_formatted_exception_chain():
    import traceback
    data = opds2()
    data["publications"][0]["links"][0]["href"] = "https://example.com\uff0fsecret-token-XYZ/path"
    try:
        parse_catalog(json.dumps(data), BASE)
    except CatalogParseError:
        assert "secret-token-XYZ" not in traceback.format_exc()
    else:
        pytest.fail("Malformed NFKC hostname unexpectedly accepted")


def test_json_parser_recursion_failure_becomes_bounded_catalog_error():
    payload = b'{"nested":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}'
    with pytest.raises(CatalogParseError):
        parse_catalog(payload, BASE)


@pytest.mark.parametrize('protocol', ['xml', 'json'])
def test_inline_thumbnail_does_not_disable_catalog_or_become_a_download(protocol):
    image = 'data:image/png;base64,aGVsbG8='
    if protocol == 'xml':
        payload = '<feed xmlns="http://www.w3.org/2005/Atom"><title>Books</title><entry><title>One</title><link rel="http://opds-spec.org/image/thumbnail" type="image/png" href="'+image+'"/><link rel="http://opds-spec.org/acquisition/open-access" type="application/epub+zip" href="one.epub"/></entry></feed>'
    else:
        payload = json.dumps({'metadata':{'title':'Books'},'publications':[{'metadata':{'title':'One'},'images':[{'href':image,'type':'image/png'}],'links':[{'href':'one.epub','type':'application/epub+zip','rel':'http://opds-spec.org/acquisition/open-access'}]}]})
    publication = parse_catalog(payload, BASE).publications[0]
    assert publication.title == 'One' and publication.images == ()
    assert len(publication.offers) == 1 and publication.offers[0].is_direct_download
    assert not any(link.href.startswith('data:') for link in publication.links)
