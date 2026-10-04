# SPDX-License-Identifier: GPL-3.0-or-later
"""Catalog opt-in, private selection and attributable MOBI handoff seams."""
import json
from pathlib import Path
import secrets
from types import SimpleNamespace

import pytest
from sqlalchemy import MetaData, create_engine

from tests.unit.test_acquisition_catalog import c, s, k, h
from tests.unit.test_acquisition_staging import s as stage

MOBI = 'application/x-mobipocket-ebook'
ROOT = Path(__file__).resolve().parents[1]


def catalog_repo(tmp_path, config):
    engine = create_engine('sqlite:///' + str(tmp_path / 'catalog.db'))
    metadata = MetaData(); tables = s.define_tables(metadata); metadata.create_all(engine)
    repo = s.Repository(engine, tables, k.SecretBox(b'x' * 32))
    connection = repo.create_connection('Catalog', 'opds', config, enabled=True)
    root = {'metadata': {'title': 'Original catalog'}, 'publications': [{
        'metadata': {'title': 'Original legal edition'}, 'links': [
            {'rel': 'download', 'href': '/original.mobi?token=PRIVATE', 'type': MOBI},
            {'rel': 'download', 'href': '/control.epub', 'type': 'application/epub+zip'},
            {'rel': 'preview', 'href': '/sample.mobi', 'type': MOBI},
            {'rel': 'buy', 'href': '/purchase.mobi', 'type': MOBI}]}]}
    seen = []
    def transfer(url, policy, **kwargs):
        seen.append(url)
        return h.FetchedDocument(json.dumps(root).encode(), url, 'application/opds+json')
    return repo, connection, c.CatalogService(repo, transfer=transfer), seen


@pytest.mark.parametrize('choice', [None, False, True])
def test_catalog_mobi_choice_is_explicit_and_does_not_fetch_a_book(tmp_path, choice):
    raw = {'endpoint': 'https://example.org/catalog'}
    if choice is not None: raw['allow_mobi'] = choice
    config = c.connection_config(raw)
    repo, connection, service, seen = catalog_repo(tmp_path, config)
    page = service.browse(1, connection.id)
    offers = page['publications'][0]['offers']
    assert [offer['format'] for offer in offers] == (['MOBI', 'EPUB'] if choice is True else ['EPUB'])
    assert seen == ['https://example.org/catalog']
    assert 'PRIVATE' not in json.dumps(page) and 'example.org' not in json.dumps(page)
    if choice is True:
        job = service.request(1, connection.id, offers[0]['offer_id'], 'mobi-original', requires_approval=False)
        assert job.state == 'queued'
        with pytest.raises((s.NotFound, s.Conflict)):
            service.request(2, connection.id, offers[0]['offer_id'], 'other-owner')


@pytest.mark.parametrize('choice', [1, 'true', None, [], {}])
def test_connection_mobi_opt_in_requires_a_boolean(choice):
    with pytest.raises(c.CatalogError, match='invalid_connection'):
        c.connection_config({'endpoint': 'https://example.org/catalog', 'allow_mobi': choice})


def test_mobi_opt_in_does_not_change_credentials_or_origin_policy():
    raw = {'endpoint': 'https://example.org/catalog', 'auth_kind': 'bearer', 'secret': 'TOKEN'}
    ordinary = c.connection_config(raw); enabled = c.connection_config(dict(raw, allow_mobi=True))
    assert c.policy(ordinary) == c.policy(enabled)
    assert enabled['secret'] == 'TOKEN' and enabled['allow_mobi'] is True


def test_supported_mobi_bytes_get_a_correct_private_handoff_and_owned_cleanup(tmp_path):
    source = tmp_path / 'source.part'
    original = (ROOT / 'fixtures/sample_books/test_original_direct.mobi').read_bytes()
    source.write_bytes(original)
    assert stage.validate_book(source, MOBI) == 'mobi'
    for wrong_type in ['application/pdf', 'application/epub+zip']:
        with pytest.raises(stage.StagingError): stage.validate_book(source, wrong_type)
    directory = tmp_path / 'ingest'; directory.mkdir()
    permit = SimpleNamespace(job_id='request', staging_key='owned-mobi', token=secrets.token_urlsafe(32), source_sha256=stage.digest(source))
    result = stage.publish(source, directory, permit, 'mobi')
    assert result.name == 'cwng-acquisition-owned-mobi.mobi'
    assert result.read_bytes() == source.read_bytes() == original
    assert result.stat().st_mode & 0o777 == 0o600
    manifest = json.loads(Path(str(result) + '.cwa.json').read_text())
    assert manifest['job_id'] == 'request' and manifest['staging_key'] == 'owned-mobi'
    assert stage.publication_state(directory, 'owned-mobi')[0] == result
    assert stage.publish(source, directory, permit, 'mobi') == result
    stage.discard_publication(directory, 'owned-mobi')
    assert stage.publication_state(directory, 'owned-mobi') == (None, None, None)
    assert source.read_bytes() == original
