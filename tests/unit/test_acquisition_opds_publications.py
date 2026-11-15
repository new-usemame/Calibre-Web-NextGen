# SPDX-License-Identifier: GPL-3.0-or-later
"""Localized OPDS2 metadata and explicit detail reads retain owned acquisition."""
import importlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from sqlalchemy import MetaData, create_engine

path = Path(__file__).resolve().parents[2] / 'cps/services/acquisition'
spec = importlib.util.spec_from_file_location('_acquisition_publication_tests', path / '__init__.py', submodule_search_locations=[str(path)])
package = importlib.util.module_from_spec(spec); sys.modules[spec.name] = package; spec.loader.exec_module(package)
p = importlib.import_module(spec.name + '.opds')
c = importlib.import_module(spec.name + '.catalog')
s = importlib.import_module(spec.name + '.storage')
k = importlib.import_module(spec.name + '.secrets')
h = importlib.import_module(spec.name + '.http')
BASE = 'https://catalog.example/root.json'
PUBLICATION = 'application/opds-publication+json'


def publication():
    return {'metadata': {'title': {'en': 'Original edition', 'fr': 'Édition originale'},
        'identifier': 'urn:original:edition', 'language': 'fr',
        'author': {'name': {'en': 'A Writer', 'fr': 'Une autrice'}}},
        'links': [{'rel': 'self', 'href': '/edition.json', 'type': PUBLICATION},
                  {'rel': 'http://opds-spec.org/acquisition/open-access', 'href': 'book.epub?key=PRIVATE', 'type': 'application/epub+zip'}]}


@pytest.mark.parametrize('locale,expected', [('fr_CA', ('Édition originale', 'Une autrice')), ('en-GB', ('Original edition', 'A Writer')), ('hu', ('Original edition', 'A Writer'))])
def test_language_maps_select_display_language_without_changing_edition_or_offer(locale, expected):
    feed = {'metadata': {'title': {'fr': 'Livres', 'en': 'Books'}}, 'publications': [publication()]}
    catalog = p.parse_catalog(json.dumps(feed), BASE, media_type='application/opds+json', preferred_language=locale)
    book, = catalog.publications
    assert (book.title, book.contributors[0].name) == expected
    assert book.identifier == 'urn:original:edition' and book.languages == ('fr',)
    assert book.offers[0].link.href == 'https://catalog.example/book.epub?key=PRIVATE'
    # JSON object order is not a display-language preference.
    feed['publications'][0]['metadata']['title'] = dict(reversed(list(publication()['metadata']['title'].items())))
    assert p.parse_catalog(json.dumps(feed), BASE, preferred_language=locale).publications[0].title == expected[0]


@pytest.mark.parametrize('bad', [{}, {'en': ''}, {'en': 'ok', 'fr': None}, {'en': 'ok', 'fr': ['bad']}, {'en_US': 'bad'}, {'en': 'one', 'EN': 'two'}, {'en-US-US': 'bad'}, {'en-x': 'bad'}])
def test_all_language_map_variants_validate_even_when_not_selected(bad):
    doc = publication(); doc['metadata']['title'] = bad
    with pytest.raises(p.CatalogParseError):
        p.parse_catalog(json.dumps({'metadata': {'title': 'Books'}, 'publications': [doc]}), BASE, preferred_language='en')


def test_unselected_language_text_retains_the_parser_budget():
    doc = publication(); doc['metadata']['title']['de'] = 'x' * 33
    with pytest.raises(p.CatalogParseError):
        p.parse_catalog(json.dumps({'metadata': {'title': 'Books'}, 'publications': [doc]}), BASE, preferred_language='en', limits=p.ParseLimits(max_text=32))


def test_explicit_publication_document_is_one_catalog_record_with_relative_direct_offer():
    catalog = p.parse_catalog(json.dumps(publication()), 'https://catalog.example/edition.json', media_type=PUBLICATION+';charset=utf-8', preferred_language='fr')
    book, = catalog.publications
    assert catalog.title == book.title == 'Édition originale' and catalog.protocol == 'opds2'
    assert catalog.is_publication_document
    assert book.offers[0].is_direct_download and catalog.capabilities.direct_download
    assert book.offers[0].link.href == 'https://catalog.example/book.epub?key=PRIVATE'
    doc = publication(); doc['links'] = [{'rel': 'self', 'href': '/edition.json', 'type': PUBLICATION}]
    with pytest.raises(p.CatalogParseError): p.parse_catalog(json.dumps(doc), BASE, media_type=PUBLICATION)
    with pytest.raises(p.CatalogParseError): p.parse_catalog(json.dumps(publication()), BASE, media_type='application/json')


@pytest.mark.parametrize('action_media', [PUBLICATION, 'application/opds+json', 'application/atom+xml'])
def test_publication_details_are_explicit_private_reads_not_loan_or_purchase_actions(tmp_path, action_media):
    engine = create_engine('sqlite:///' + str(tmp_path/'app.db')); tables = s.define_tables(MetaData()); tables.jobs.metadata.create_all(engine)
    try:
        repo = s.Repository(engine, tables, k.SecretBox(b'x'*32))
        config = c.connection_config({'endpoint': BASE, 'auth_kind': 'none'})
        connection = repo.create_connection('Catalog', 'opds', config, enabled=True)
        summary = publication(); summary['links'] = [
            {'rel': 'self', 'href': '/edition.json?token=DETAIL_SECRET', 'type': PUBLICATION},
            {'rel': 'borrow', 'href': '/loan', 'type': action_media},
            {'rel': ['self','buy'], 'href': '/buy', 'type': action_media},
            {'rel': 'alternate', 'href': '/template{?id}', 'type': PUBLICATION, 'templated': True}]
        root = {'metadata': {'title': 'Books'}, 'publications': [summary]}; seen = []
        def transfer(url, policy, **kwargs):
            seen.append(url)
            return h.FetchedDocument(json.dumps(root if url == BASE else publication()).encode(), url, 'application/opds+json' if url == BASE else PUBLICATION)
        service = c.CatalogService(repo, transfer=transfer, preferred_language='fr')
        page = service.browse(1, connection.id)
        assert seen == [BASE] and not repo.list_jobs(1)
        book, = page['publications']; assert book['title'] == 'Édition originale' and not book['offers']
        assert len(book['navigation']) == 1, [link['relations'] for link in book['navigation']]
        link, = book['navigation']; assert link['title'] == book['title']
        assert all(secret not in json.dumps(page) for secret in ['https://', 'DETAIL_SECRET', 'PRIVATE'])
        with pytest.raises(s.NotFound): service.request(1, connection.id, link['selection'], 'detail-not-job')
        with pytest.raises(s.NotFound): service.browse(2, connection.id, selection=link['selection'])
        details = service.browse(1, connection.id, selection=link['selection'])
        assert seen == [BASE, 'https://catalog.example/edition.json?token=DETAIL_SECRET']
        book, = details['publications']; offer, = book['offers']
        assert book['navigation'] == []
        job = service.request(1, connection.id, offer['offer_id'], 'chosen-book')
        assert job.state == 'awaiting_approval' and job.title == 'Édition originale'
        assert service.request(1, connection.id, offer['offer_id'], 'chosen-book').id == job.id
        assert len(repo.list_jobs(1)) == 1 and not repo.list_jobs(2)
    finally: engine.dispose()


def test_case_insensitive_script_private_and_grandfathered_language_tags():
    doc = publication(); doc['metadata']['title'] = {'I-KLINGON': 'Klingon title', 'zh-Hant-TW': 'Traditional title', 'x-original': 'Private title'}
    for locale, title in [('i-klingon', 'Klingon title'), ('zh_Hant', 'Traditional title'), ('x-original', 'Private title')]:
        catalog = p.parse_catalog(json.dumps({'metadata': {'title': 'Books'}, 'publications': [doc]}), BASE, preferred_language=locale)
        assert catalog.publications[0].title == title


def test_section_titles_can_localize_but_link_titles_keep_their_string_schema():
    group = {'metadata': {'title': {'en': 'Authors', 'fr': 'Autrices'}}, 'publications': [publication()]}
    feed = {'metadata': {'title': 'Books'}, 'groups': [group]}
    assert p.parse_catalog(json.dumps(feed), BASE, preferred_language='fr').groups[0].title == 'Autrices'
    group['navigation'] = [{'title': {'en': 'This is not a valid link title'}, 'href': '/authors', 'type': 'application/opds+json'}]
    with pytest.raises(p.CatalogParseError): p.parse_catalog(json.dumps(feed), BASE)


def test_purchase_or_loan_publication_document_is_metadata_only():
    doc = publication(); doc['links'][1] = {'rel': 'borrow', 'href': '/loan', 'type': PUBLICATION,
        'properties': {'indirectAcquisition': [{'type': 'application/vnd.readium.lcp.license.v1.0+json', 'child': [{'type': 'application/epub+zip'}]}]}}
    catalog = p.parse_catalog(json.dumps(doc), BASE, media_type=PUBLICATION)
    assert not catalog.capabilities.direct_download and not catalog.publications[0].offers[0].is_direct_download
    doc['publications'] = []
    with pytest.raises(p.CatalogParseError): p.parse_catalog(json.dumps(doc), BASE, media_type=PUBLICATION)
