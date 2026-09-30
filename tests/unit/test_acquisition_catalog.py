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


@pytest.mark.parametrize('network', [
    '127.0.0.0/8',          # loopback
    '127.0.0.1/32',
    '169.254.0.0/16',       # link-local
    '169.254.169.254/32',   # cloud instance metadata
    'fd00:ec2::254/128',    # the same, over IPv6
    '0.0.0.0/0',            # catch-all: would re-admit every range above
    '::/0',
    '0.0.0.0/8',
    '::1/128',              # loopback, v6
    'fe80::/10',            # link-local, v6
    '224.0.0.0/4',          # multicast
    '8.8.8.8/32',           # public space is not a "private network"
    '100.100.100.200/32',   # Alibaba Cloud instance metadata
    '100.64.0.0/10',        # all of CGNAT: contains the address above
    'fd20:ce::254/128',     # GCP metadata over IPv6
    'fc00::/7',             # all of unique-local: contains both v6 metadata addresses
    'not-a-network',
])
def test_admin_cannot_open_a_catalog_onto_a_network_that_must_stay_denied(network):
    with pytest.raises(c.CatalogError):
        c.connection_config({'endpoint': 'http://192.168.1.5:8080/catalog',
                             'private_origins': ['http://192.168.1.5:8080'],
                             'private_networks': [network]})


@pytest.mark.parametrize('network', [
    '192.168.0.0/16', '192.168.1.0/24', '10.0.0.0/8', '172.16.0.0/12',
    '100.101.0.0/16',           # part of CGNAT, which is how Tailscale addresses look
    'fd3a:9c2b:1f4e::/48',      # a real RFC 4193 unique-local prefix
])
def test_a_home_network_catalog_is_reachable_once_the_admin_opts_in(network):
    config = c.connection_config({'endpoint': 'http://192.168.1.5:8080/catalog',
                                  'private_origins': ['http://192.168.1.5:8080'],
                                  'private_networks': [network]})
    assert config['private_networks'] == [network]
    assert config['private_origins'] == ['http://192.168.1.5:8080']


def test_the_guard_is_the_only_thing_standing_between_an_admin_and_loopback():
    """Why `home_networks` cannot be delegated to the transport.

    advocate consults its whitelist before the loopback and link-local rules,
    so by the time a request is made a whitelisted 127.0.0.1 is already
    allowed. Pin that, so nobody removes the guard believing the layer below
    would still refuse.
    """
    import ipaddress as ip
    from cps.cw_advocate.addrvalidator import AddrValidator
    permissive = AddrValidator(ip_whitelist={ip.ip_network('127.0.0.0/8')},
                               port_whitelist={8080}, allow_ipv6=True)
    assert permissive.is_ip_allowed('127.0.0.1') is True
    with pytest.raises(c.CatalogError):
        c.home_networks(['127.0.0.0/8'])


def test_the_admin_opt_in_reaches_a_home_catalog_and_nothing_else():
    """One checkbox, expanded server-side, scoped to this catalog's origin."""
    config = c.connection_config({'endpoint': 'http://192.168.1.5:8080/catalog',
                                  'allow_private_network': True})
    assert config['private_origins'] == ['http://192.168.1.5:8080/catalog']
    assert config['private_networks'] == [str(net) for net in c.DEFAULT_HOME_NETWORKS]
    policy = c.policy(config)
    # The allowance only applies to the catalog's own origin.
    assert h.origin('http://192.168.1.5:8080/other') in {
        h.origin(x) for x in policy.private_origins}
    assert h.origin('http://10.1.2.3:8080/catalog') not in {
        h.origin(x) for x in policy.private_origins}
    # And, as the transport builds its validator for this origin, it reaches
    # the home network while metadata and loopback stay denied.
    import ipaddress as ip
    from cps.cw_advocate.addrvalidator import AddrValidator
    validator = AddrValidator(ip_whitelist={ip.ip_network(x) for x in policy.private_networks},
                              port_whitelist={8080}, allow_ipv6=True)
    for reachable in ('192.168.1.5', '10.1.2.3', '100.101.2.3', '100.100.100.199'):
        assert validator.is_ip_allowed(reachable), reachable
    for denied in ('127.0.0.1', '169.254.169.254', '100.100.100.200', 'fd00:ec2::254'):
        assert not validator.is_ip_allowed(denied), denied


def test_the_opt_in_is_off_by_default_and_cannot_be_combined_with_raw_ranges():
    plain = c.connection_config({'endpoint': 'https://example.org/catalog'})
    assert plain['private_origins'] == [] and plain['private_networks'] == []
    with pytest.raises(c.CatalogError):
        c.connection_config({'endpoint': 'http://192.168.1.5:8080/catalog',
                             'allow_private_network': True,
                             'private_networks': ['10.0.0.0/8'],
                             'private_origins': ['http://192.168.1.5:8080']})
    with pytest.raises(c.CatalogError):
        c.connection_config({'endpoint': 'http://192.168.1.5:8080/catalog',
                             'allow_private_network': 'yes'})


def test_an_opted_in_catalog_can_still_be_switched_on_later():
    """Enabling a connection revalidates its stored configuration.

    Whatever the opt-in expands to therefore has to survive `connection_config`
    a second time, or a catalog could be added and then never switched on.
    """
    stored = c.connection_config({'endpoint': 'http://192.168.1.5:8080/catalog',
                                  'allow_private_network': True})
    assert 'allow_private_network' not in stored, 'the flag must not round-trip'
    assert c.connection_config(stored) == stored
    for network in stored['private_networks']:
        c.home_networks([network])
