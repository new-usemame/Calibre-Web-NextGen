# SPDX-License-Identifier: GPL-3.0-or-later
"""Preserve an unchanged edition; only its ordinary OCF locator may be serialized differently."""
import json
from pathlib import Path

import pytest

from tests.unit.test_acquisition_calibre_transaction import Cache, helper
from tests.unit.test_acquisition_epub_resources import RESOURCES, package
from tests.unit.test_acquisition_ingest import i, published
from tests.unit.test_acquisition_storage import store

NS = 'urn:oasis:names:tc:opendocument:xmlns:container'
ORDINARY = (f'<container version="1.0" xmlns="{NS}"><rootfiles>'
            '<rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/>'
            '</rootfiles></container>').encode()


def resources(container=ORDINARY):
    return dict(RESOURCES, **{'META-INF/container.xml': container})


def serialize(kind):
    text = ORDINARY.decode()
    if kind == 'quotes': text = text.replace('"', "'")
    elif kind == 'whitespace': text = text.replace('><', '>\n  <')
    elif kind == 'attributes':
        text = text.replace('full-path="OPS/book.opf" media-type="application/oebps-package+xml"',
                            'media-type="application/oebps-package+xml" full-path="OPS/book.opf"')
    elif kind == 'prefix':
        text = text.replace('xmlns=', 'xmlns:c=')
        for tag in ('container', 'rootfiles', 'rootfile'):
            text = text.replace('<' + tag, '<c:' + tag).replace('</' + tag, '</c:' + tag)
    elif kind == 'comments': text = '<!--original legal fixture-->' + text.replace('<rootfiles>', '<rootfiles><!--locator-->')
    elif kind == 'character-reference': text = text.replace('OPS/book.opf', 'OPS/boo&#107;.opf')
    elif kind == 'utf16': return ('<?xml version="1.0" encoding="UTF-16"?>' + text).encode('utf-16')
    return text.encode()


def select(helper, monkeypatch, tmp_path, incoming_resources, candidates=None):
    cache = Cache(tmp_path)
    candidates = candidates or {17: resources()}
    for bid, members in candidates.items():
        cache.connection.execute('INSERT INTO books VALUES (?)', (bid,))
        stored = package(tmp_path / f'stored-{bid}.epub', members, repack=True)
        cache.paths[bid, 'epub'] = str(stored)
    monkeypatch.setattr(helper, 'find_identical_books', lambda *args: set(candidates))
    incoming = package(tmp_path / 'incoming.epub', incoming_resources)
    before = {bid: Path(cache.paths[bid, 'epub']).read_bytes() for bid in candidates}
    with cache.connection:
        result = helper.add_acquisition(cache, object(), 'epub', str(incoming), helper.content_digest(incoming))
    assert {bid: Path(cache.paths[bid, 'epub']).read_bytes() for bid in candidates} == before
    return cache, result, incoming


@pytest.mark.parametrize('kind', ['quotes', 'whitespace', 'attributes', 'prefix', 'comments', 'character-reference', 'utf16'])
def test_ordinary_locator_serialization_retains_existing_record_and_truthful_hashes(helper, monkeypatch, tmp_path, kind):
    cache, result, incoming = select(helper, monkeypatch, tmp_path, resources(serialize(kind)))
    assert result['book_ids'] == [17] and cache.add_calls == 0
    assert result['disposition'] == 'existing_retained' and result['artifact_identity_version'] == 3
    assert result['source_sha256'] == helper.content_digest(incoming)
    assert result['imported_sha256'] == helper.content_digest(cache.paths[17, 'epub']) != result['source_sha256']
    assert helper.acquisition_result(cache, result['source_sha256']) == dict(result, status='already_imported')
    cache.connection.close()


@pytest.mark.parametrize('fault', ['version', 'missing-version', 'unknown-attribute', 'foreign-element', 'xml-base',
                                  'links', 'multiple-rootfiles', 'text', 'pi-before', 'pi-inside', 'signatures',
                                  'body', 'opf-language', 'rights', 'font'])
def test_ambiguous_locator_or_changed_publication_keeps_separate_record(helper, monkeypatch, tmp_path, fault):
    text = serialize('quotes').decode(); members = resources()
    if fault == 'version': text = text.replace("version='1.0'", "version='2.0'")
    elif fault == 'missing-version': text = text.replace("version='1.0' ", '')
    elif fault == 'unknown-attribute': text = text.replace('<rootfiles>', '<rootfiles choice="changed">')
    elif fault == 'foreign-element': text = text.replace('</container>', '<extra xmlns="urn:other"/></container>')
    elif fault == 'xml-base': text = text.replace('<rootfiles>', '<rootfiles xml:base="other/">')
    elif fault == 'links': text = text.replace('</container>', '<links><link href="other"/></links></container>')
    elif fault == 'multiple-rootfiles': text = text.replace('</rootfiles>', '<rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles>')
    elif fault == 'text': text = text.replace('<rootfiles>', '<rootfiles>changed')
    elif fault == 'pi-before': text = '<?fixture changes="identity"?>' + text
    elif fault == 'pi-inside': text = text.replace('<rootfiles>', '<rootfiles><?fixture changes="identity"?>')
    elif fault == 'signatures': members['META-INF/signatures.xml'] = b'<signature>opaque bytes</signature>'
    elif fault == 'body': members['OPS/chapter.xhtml'] += b'changed edition'
    elif fault == 'opf-language': members['OPS/book.opf'] = members['OPS/book.opf'].replace(b'en', b'fr')
    elif fault == 'rights': members['META-INF/rights.xml'] = b'different rights'
    elif fault == 'font': members['OPS/font.bin'] = b'different font'
    members['META-INF/container.xml'] = text.encode()
    original = resources()
    if fault == 'signatures': original['META-INF/signatures.xml'] = members['META-INF/signatures.xml']
    cache, result, _ = select(helper, monkeypatch, tmp_path, members, {17: original})
    assert result['book_ids'] == [18] and cache.add_calls == 1 and result['disposition'] == 'imported'
    assert result['artifact_identity_version'] == 1
    cache.connection.close()


def test_exact_resource_candidate_takes_priority_over_earlier_locator_match(helper, monkeypatch, tmp_path):
    incoming = resources(serialize('quotes'))
    cache, result, _ = select(helper, monkeypatch, tmp_path, incoming, {17: resources(), 18: incoming})
    assert result['book_ids'] == [18] and result['artifact_identity_version'] == 2 and cache.add_calls == 0
    cache.connection.close()


def test_both_identities_share_one_expanded_work_budget_and_preserve_exact_digest(helper, tmp_path):
    original = package(tmp_path / 'ordinary.epub', resources())
    old_exact = helper.epub_resource_digest(original)
    budget = helper.EpubScanBudget()
    before = budget.remaining_expanded_bytes
    pair = helper.epub_resource_identities(original, budget=budget)
    assert pair[2] == old_exact and pair[3] != pair[2]
    assert before - budget.remaining_expanded_bytes == sum(map(len, resources().values()))
    changed = package(tmp_path / 'serialized.epub', resources(serialize('prefix')))
    other = helper.epub_resource_identities(changed)
    assert other[2] != pair[2] and other[3] == pair[3]


def test_new_recovery_version_still_requires_exact_retained_bytes(helper, tmp_path):
    cache = Cache(tmp_path); cache.connection.execute('INSERT INTO books VALUES (17)')
    stored = package(tmp_path / 'stored.epub', resources()); cache.paths[17, 'epub'] = str(stored)
    result = dict(source_sha256='a' * 64, imported_sha256=helper.content_digest(stored), book_ids=[17],
                  disposition='existing_retained', format='epub', artifact_identity_version=3)
    cache.connection.execute('INSERT INTO cwng_acquisition_ingest_result VALUES (?,?)', ('a' * 64, json.dumps(result)))
    assert helper.acquisition_result(cache, 'a' * 64) == dict(result, status='already_imported')
    stored.write_bytes(b'replaced edition')
    assert helper.acquisition_result(cache, 'a' * 64) is None
    cache.connection.close()


def test_application_finalizes_locator_retention_only_with_current_stored_bytes(store, tmp_path):
    repo, job, source, manifest, intent, metadata, result = published(store, tmp_path)
    result.update(disposition='existing_retained', artifact_identity_version=3)
    import sqlite3
    with sqlite3.connect(metadata) as connection:
        connection.execute('UPDATE cwng_acquisition_ingest_result SET result_json=?', (json.dumps(result),))
    recovered = i.read_result(metadata, intent.source_sha256, tmp_path)
    assert recovered is not None and recovered.book_ids == (17,)
    i.finalize(repo, intent, result, metadata, tmp_path)
    assert repo.get_job(1, job.id).state == 'imported' and source.exists()
    (tmp_path / 'library/stored.epub').write_bytes(b'replaced retained edition')
    assert i.read_result(metadata, intent.source_sha256, tmp_path) is None
