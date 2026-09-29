# SPDX-License-Identifier: GPL-3.0-or-later
"""Catalog protocol → persisted owner selections, not source-text assertions."""
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import MetaData, create_engine

path = Path(__file__).resolve().parents[2] / 'cps/services/acquisition'
spec = importlib.util.spec_from_file_location('_acquisition_catalog_tests', path / '__init__.py', submodule_search_locations=[str(path)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
c = importlib.import_module(spec.name + '.catalog')
s = importlib.import_module(spec.name + '.storage')
k = importlib.import_module(spec.name + '.secrets')
h = importlib.import_module(spec.name + '.http')


@pytest.mark.parametrize('syntax,template', [
    ('uri-template', 'https://example.org/search{?query,title,author}'),
    ('uri-template', 'https://example.org/search?key=SOURCE_SECRET{&query}'),
    ('opensearch', 'https://example.org/search?query={searchTerms}&count={count}&unknown={extra?}'),
])
def test_keyword_punctuation_and_unicode_cannot_add_query_parameters(syntax, template):
    query = 'café &admin=true#chapter / ?'
    result = c.expand_search(c.Search(c.Link(template, templated=True), syntax), query)
    parsed = urlsplit(result)
    assert parsed.hostname == 'example.org' and not parsed.fragment
    params = parse_qs(parsed.query)
    assert params['query'] == [query] and 'admin' not in params
    assert 'title' not in params and 'author' not in params


@pytest.mark.parametrize('syntax,template', [
    ('uri-template', 'https://{query}.example.org/search'),
    ('uri-template', 'https://example.org/{+query}'),
    ('uri-template', 'https://example.org/search{?author}'),
    ('opensearch', 'https://example.org/?q={searchTerms}&page={startPage}'),
    ('opensearch', 'https://example.org/?q={searchTerms}&required={unknown}'),
])
def test_unsupported_search_does_not_guess_required_parameters_or_host(syntax, template):
    with pytest.raises((c.CatalogError, h.TransportError)):
        c.expand_search(c.Search(c.Link(template, templated=True), syntax), 'book')


def test_catalog_selections_are_private_expiring_and_cannot_be_used_as_downloads(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'app.db'))
    metadata = MetaData(); tables = s.define_tables(metadata); metadata.create_all(engine)
    now = [1000.0]
    repo = s.Repository(engine, tables, k.SecretBox(b'x' * 32), clock=lambda: now[0])
    config = c.connection_config({'endpoint': 'https://example.org/catalog?key=SOURCE_SECRET',
                                  'auth_kind': 'bearer', 'secret': 'AUTH_SECRET'})
    connection = repo.create_connection('Catalog', 'opds', config, enabled=True)
    root = {'metadata': {'title': 'Books'}, 'links': [
        {'rel': 'search', 'href': '/search{?query}', 'type': 'application/opds+json', 'templated': True},
        {'rel': 'next', 'href': '/page2?token=PAGE_SECRET', 'type': 'application/opds+json'}],
        'publications': [{'metadata': {'title': 'Example', 'author': 'Writer'}, 'links': [
            {'rel': 'download', 'href': '/book.epub?key=DOWNLOAD_SECRET', 'type': 'application/epub+zip'},
            {'rel': 'preview', 'href': '/sample.epub', 'type': 'application/epub+zip'},
            {'rel': 'buy', 'href': '/purchase', 'type': 'application/epub+zip'}]}]}
    requested = []
    def transfer(url, policy, **kwargs):
        requested.append(url)
        assert policy.authorization == 'Bearer AUTH_SECRET'
        return h.FetchedDocument(json.dumps(root).encode(), url, 'application/opds+json')
    service = c.CatalogService(repo, transfer=transfer)
    page = service.browse(1, connection.id)
    serialized = json.dumps(page)
    assert all(secret not in serialized for secret in ('SOURCE_SECRET', 'PAGE_SECRET', 'DOWNLOAD_SECRET', 'AUTH_SECRET', 'https://'))
    assert page['publications'][0]['authors'] == ['Writer']
    assert len(page['publications'][0]['offers']) == 1
    nav = page['pagination'][0]['selection']; search = page['searches'][0]['selection']
    offer = page['publications'][0]['offers'][0]['offer_id']
    with pytest.raises(s.NotFound): service.browse(2, connection.id, selection=nav)
    with pytest.raises(s.NotFound): service.request(2, connection.id, offer, 'stolen')
    with pytest.raises(s.NotFound): service.request(1, connection.id, nav, 'not-a-book')
    with pytest.raises(s.NotFound): service.browse(1, connection.id, selection=offer)
    service.browse(1, connection.id, selection=nav)
    assert requested[-1] == 'https://example.org/page2?token=PAGE_SECRET'
    service.browse(1, connection.id, selection=search, query='Words & more')
    assert parse_qs(urlsplit(requested[-1]).query)['query'] == ['Words & more']
    job = service.request(1, connection.id, offer, 'same-click')
    assert job.state == 'awaiting_approval' and job.title == 'Example' and repo.claim() is None
    assert service.request(1, connection.id, offer, 'same-click').id == job.id
    now[0] += 901
    assert service.request(1, connection.id, offer, 'same-click').id == job.id
    with pytest.raises(s.NotFound): service.browse(1, connection.id, selection=nav)
    assert repo.get_job(1, job.id).id == job.id
    engine.dispose()


def test_opensearch_description_is_fetched_but_acquisition_links_are_never_probed(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'app.db'))
    metadata = MetaData(); tables = s.define_tables(metadata); metadata.create_all(engine)
    repo = s.Repository(engine, tables, k.SecretBox(b'x' * 32))
    config = c.connection_config({'endpoint': 'https://example.org/catalog'})
    connection = repo.create_connection('Other OPDS', 'opds', config, enabled=True)
    feed = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Books</title>
    <link rel="search" type="application/opensearchdescription+xml" href="/search.xml"/>
    <entry><title>Book</title><link rel="http://opds-spec.org/acquisition" href="/book.epub" type="application/epub+zip"/></entry></feed>'''
    description = b'''<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
    <Url type="application/atom+xml" template="https://{searchTerms}/search"/>
    <Url type="application/atom+xml" template="https://example.org/results?q={searchTerms}"/></OpenSearchDescription>'''
    calls = []
    def transfer(url, policy, **kwargs):
        calls.append(url)
        if url.endswith('.xml'):
            return h.FetchedDocument(description, url, 'application/opensearchdescription+xml')
        return h.FetchedDocument(feed, url, 'application/atom+xml')
    service = c.CatalogService(repo, transfer=transfer)
    assert service.probe(config)['direct_download_advertised'] is True
    page = service.browse(1, connection.id)
    service.browse(1, connection.id, selection=page['searches'][0]['selection'], query='A book')
    assert calls[-2:] == ['https://example.org/search.xml', 'https://example.org/results?q=A%20book']
    assert not any('book.epub' in url for url in calls)
    engine.dispose()


from tests.unit.test_acquisition_storage import store, s as store_module


def test_same_format_variants_remain_distinct_and_described(store):
    repo = store[0]
    connection = repo.create_connection('Variants', 'opds', c.connection_config({'endpoint':'https://example.org/feed'}), enabled=True)
    document = {'metadata':{'title':'Catalog'},'publications':[{'metadata':{'title':'Book'},'links':[
        {'rel':'download','href':'/images.epub','type':'application/epub+zip','title':'With illustrations'},
        {'rel':'download','href':'/text.epub','type':'application/epub+zip','title':'Text only'}]}]}
    service = c.CatalogService(repo, transfer=lambda url,policy,**kwargs: h.FetchedDocument(json.dumps(document).encode(),url,'application/opds+json'))
    offers = service.browse(1,connection.id)['publications'][0]['offers']
    assert [offer['label'] for offer in offers] == ['With illustrations','Text only']
    assert len({offer['offer_id'] for offer in offers}) == 2
    assert [repo.offer_payload(1,offer['offer_id'],connection.id).offer['href'] for offer in offers] == ['https://example.org/images.epub','https://example.org/text.epub']


def test_catalog_identity_survives_offer_rotation_but_is_scoped_to_account(store):
    repo = store[0]
    connection = repo.create_connection('Catalog','opds',c.connection_config({'endpoint':'https://example.org/feed'}),enabled=True)
    document = {'metadata':{'title':'Catalog'},'publications':[{'metadata':{'title':'Book'},'links':[{'rel':'download','href':'/book.epub?key=SECRET','type':'application/epub+zip'}]}]}
    service = c.CatalogService(repo,transfer=lambda url,policy,**kwargs:h.FetchedDocument(json.dumps(document).encode(),url,'application/opds+json'))
    first,second,other = [service.browse(owner,connection.id)['publications'][0] for owner in (1,1,2)]
    assert first['identity'] == second['identity'] != other['identity']
    a,b,z = [p['offers'][0] for p in (first,second,other)]
    assert a['identity'] == b['identity'] != z['identity']
    assert a['offer_id'] != b['offer_id']
    assert 'SECRET' not in json.dumps(first)
    with pytest.raises(store_module.NotFound): service.request(1,connection.id,a['identity'],'identity-is-not-authority')
