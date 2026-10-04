# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone Atom entries follow the same owned acquisition seam as feeds."""
import importlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from sqlalchemy import MetaData, create_engine

path = Path(__file__).resolve().parents[2] / 'cps/services/acquisition'
spec = importlib.util.spec_from_file_location('_acquisition_entry_tests', path / '__init__.py', submodule_search_locations=[str(path)])
package = importlib.util.module_from_spec(spec); sys.modules[spec.name] = package; spec.loader.exec_module(package)
p = importlib.import_module(spec.name + '.opds')
c = importlib.import_module(spec.name + '.catalog')
s = importlib.import_module(spec.name + '.storage')
k = importlib.import_module(spec.name + '.secrets')
h = importlib.import_module(spec.name + '.http')
BASE = 'https://catalog.example/root.xml?key=SOURCE_SECRET'
ENTRY_TYPE = 'application/atom+xml;type=entry;profile=opds-catalog'


def entry(links=None):
    if links is None:
        links = '''<link rel="self" href="full.xml?token=DETAIL_SECRET" type="application/atom+xml;type=entry;profile=opds-catalog"/>
        <link rel="http://opds-spec.org/acquisition/open-access" xml:base="files/" href="original.epub?key=FILE_SECRET" type="application/epub+zip"/>'''
    return '''<entry xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/" xmlns:opds="http://opds-spec.org/2010/catalog" xml:base="/editions/">
    <id>urn:original:edition</id><updated>2026-10-03T00:00:00Z</updated><title>Original edition</title><author><name>Original author</name></author><dc:language>en</dc:language><dc:identifier>urn:isbn:original</dc:identifier><summary>Original description</summary>''' + links + '</entry>'


def feed(book):
    return '<feed xmlns="http://www.w3.org/2005/Atom"><id>urn:catalog</id><updated>2026-10-03T00:00:00Z</updated><title>Books</title><link rel="self" href="/root.xml" type="application/atom+xml"/><link rel="next" href="/next.xml" type="application/atom+xml"/>' + book + '</feed>'


def test_complete_entry_preserves_feed_edition_and_resolves_nested_base_from_final_url():
    ordinary = p.parse_catalog(feed(entry()), BASE, media_type='application/atom+xml')
    detail = p.parse_catalog(entry(), 'https://redirect.example/complete.xml', media_type=ENTRY_TYPE)
    assert detail.protocol == 'opds1' and detail.is_publication_document
    assert detail.title == 'Original edition' and len(detail.publications) == 1
    assert not detail.navigation and not detail.searches
    book, = detail.publications
    assert (book.identifier, book.title, book.contributors, book.languages, book.identifiers, book.description) == (
        ordinary.publications[0].identifier, ordinary.publications[0].title, ordinary.publications[0].contributors,
        ('en',), ('urn:isbn:original',), 'Original description')
    assert book.offers[0].is_direct_download and detail.capabilities.direct_download
    assert book.offers[0].link.href == 'https://redirect.example/editions/files/original.epub?key=FILE_SECRET'
    assert not ordinary.is_publication_document


@pytest.mark.parametrize('relation,indirect', [('buy',''), ('borrow',''), ('sample',''), ('subscribe',''), ('open-access','<opds:indirectAcquisition type="application/epub+zip"/>')])
def test_action_or_indirect_entry_is_metadata_without_a_download(relation, indirect):
    doc = entry(f'<link rel="http://opds-spec.org/acquisition/{relation}" href="/action" type="application/atom+xml">{indirect}</link>')
    catalog = p.parse_catalog(doc, BASE, media_type=ENTRY_TYPE)
    assert len(catalog.publications) == 1 and catalog.is_publication_document
    assert not catalog.capabilities.direct_download and not catalog.publications[0].offers[0].is_direct_download


@pytest.mark.parametrize('doc', [
    entry('<link rel="alternate" href="/other.xml" type="application/atom+xml"/>'),
    entry().replace('http://www.w3.org/2005/Atom', 'urn:wrong'),
    '<other xmlns="http://www.w3.org/2005/Atom"/>',
    '<!DOCTYPE entry [<!ENTITY secret "expanded">]>' + entry(),
])
def test_standalone_entry_refuses_missing_acquisition_wrong_roots_and_entities(doc):
    with pytest.raises(p.CatalogParseError): p.parse_catalog(doc, BASE, media_type=ENTRY_TYPE)


@pytest.mark.parametrize('limits', [p.ParseLimits(max_depth=2), p.ParseLimits(max_nodes=4), p.ParseLimits(max_links=1), p.ParseLimits(max_text=8), p.ParseLimits(max_bytes=50)])
def test_entry_cannot_bypass_existing_document_budgets(limits):
    with pytest.raises(p.CatalogParseError): p.parse_catalog(entry(), BASE, media_type=ENTRY_TYPE, limits=limits)


def test_explicit_atom_detail_is_private_owner_bound_and_not_self_recursive(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path/'app.db')); tables = s.define_tables(MetaData()); tables.jobs.metadata.create_all(engine)
    try:
        repo = s.Repository(engine, tables, k.SecretBox(b'x'*32))
        connection = repo.create_connection('OPDS1', 'opds', c.connection_config({'endpoint': BASE}), enabled=True)
        partial = entry('''<link rel="alternate" href="full.xml?token=DETAIL_SECRET" type="application/atom+xml;type=entry;profile=opds-catalog"/>
        <link rel="http://opds-spec.org/acquisition/buy" href="/buy" type="application/atom+xml"/>''')
        calls = []
        def transfer(url, policy, **kwargs):
            calls.append(url)
            payload = feed(partial) if url == BASE else entry()
            return h.FetchedDocument(payload.encode(), url, 'application/atom+xml' if url == BASE else ENTRY_TYPE)
        service = c.CatalogService(repo, transfer=transfer)
        page = service.browse(1, connection.id)
        book, = page['publications']; detail, = book['navigation']
        assert detail['title'] == book['title'] == 'Original edition' and not book['offers']
        assert calls == [BASE] and not repo.list_jobs(1)
        assert [link['relations'] for link in page['pagination']] == [['next']]
        with pytest.raises(s.NotFound): service.request(1, connection.id, detail['selection'], 'not-an-acquisition')
        with pytest.raises(s.NotFound): service.browse(2, connection.id, selection=detail['selection'])
        result = service.browse(1, connection.id, selection=detail['selection'])
        assert calls == [BASE, 'https://catalog.example/editions/full.xml?token=DETAIL_SECRET']
        full, = result['publications']; offer, = full['offers']
        assert full['navigation'] == [], full['navigation']
        for secret in ['https://', 'SOURCE_SECRET', 'DETAIL_SECRET', 'FILE_SECRET']:
            assert secret not in json.dumps([page, result])
        job = service.request(1, connection.id, offer['offer_id'], 'chosen-edition')
        assert job.state == 'awaiting_approval' and job.title == 'Original edition'
        assert service.request(1, connection.id, offer['offer_id'], 'chosen-edition').id == job.id
        assert len(repo.list_jobs(1)) == 1 and not repo.list_jobs(2)
    finally: engine.dispose()


@pytest.mark.parametrize('field', ['id', 'updated'])
@pytest.mark.parametrize('variant', ['missing', 'empty', 'duplicate'])
def test_standalone_entry_requires_one_nonempty_atom_identity_and_update(field, variant):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(entry()); node = root.find('{'+p.ATOM+'}'+field)
    if variant == 'missing': root.remove(node)
    elif variant == 'empty': node.text = ' '
    else: root.append(ET.fromstring(ET.tostring(node)))
    with pytest.raises(p.CatalogParseError): p.parse_catalog(ET.tostring(root), BASE, media_type=ENTRY_TYPE)
