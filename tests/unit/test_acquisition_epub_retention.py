# SPDX-License-Identifier: GPL-3.0-or-later
"""ZIP-only equality must retain the real old artifact, without confusing receipts."""
import json
import sqlite3

import pytest
from tests.unit.test_acquisition_calibre_transaction import helper, Cache
from tests.unit.test_acquisition_epub_resources import package, RESOURCES
from tests.unit.test_acquisition_ingest import i


def existing(cache, book_id, path):
    cache.connection.execute('INSERT INTO books VALUES (?)', (book_id,))
    cache.paths[book_id, 'epub'] = str(path)


def test_repacked_edition_retains_old_bytes_and_replays_both_hashes(helper, monkeypatch, tmp_path):
    cache = Cache(tmp_path)
    old = package(tmp_path/'old.epub'); incoming = package(tmp_path/'incoming.epub', repack=True)
    original_bytes = old.read_bytes(); old_digest = helper.content_digest(old)
    source_digest = helper.content_digest(incoming)
    assert old_digest != source_digest
    existing(cache, 17, old)
    monkeypatch.setattr(helper, 'find_identical_books', lambda *args: {17})
    with cache.connection:
        result = helper.add_acquisition(cache, object(), 'epub', str(incoming), source_digest)
    assert result['book_ids'] == [17] and result['disposition'] == 'existing_retained'
    assert result['source_sha256'] == source_digest and result['imported_sha256'] == old_digest
    assert result['artifact_identity_version'] == 2
    assert cache.add_calls == 0 and old.read_bytes() == original_bytes
    assert helper.acquisition_result(cache, source_digest) == dict(result, status='already_imported')
    with cache.connection:
        old.write_bytes(b'changed old artifact')
        assert helper.acquisition_result(cache, source_digest) is None
        replacement = helper.add_acquisition(cache, object(), 'epub', str(incoming), source_digest)
    assert replacement['book_ids'] != [17] and replacement['imported_sha256'] == source_digest
    assert old.read_bytes() == b'changed old artifact'
    cache.connection.close()


def test_exact_archive_match_is_preferred_to_earlier_repackaged_candidate(helper, monkeypatch, tmp_path):
    cache = Cache(tmp_path)
    old = package(tmp_path/'old.epub'); incoming = package(tmp_path/'incoming.epub', repack=True)
    exact = tmp_path/'exact.epub'; exact.write_bytes(incoming.read_bytes())
    existing(cache, 17, old); existing(cache, 18, exact)
    monkeypatch.setattr(helper, 'find_identical_books', lambda *args: {18, 17})
    monkeypatch.setattr(helper, 'epub_resource_digest', lambda *args, **kwargs: pytest.fail('exact match must not expand ZIP'))
    with cache.connection:
        result = helper.add_acquisition(cache, object(), 'epub', str(incoming), helper.content_digest(incoming))
    assert result['book_ids'] == [18] and cache.add_calls == 0
    cache.connection.close()


def test_resource_mismatch_and_external_candidate_never_substitute_old_edition(helper, monkeypatch, tmp_path):
    library = tmp_path/'library'; library.mkdir(); cache = Cache(library)
    old = package(library/'old.epub')
    outside = package(tmp_path/'outside.epub', repack=True)
    linked = library/'linked.epub'; linked.symlink_to(outside)
    incoming_resources = dict(RESOURCES); incoming_resources['OPS/chapter.xhtml'] += b'new edition'
    incoming = package(tmp_path/'incoming.epub', incoming_resources, repack=True)
    # External archive matches incoming exactly; containment must still exclude it.
    outside.write_bytes(incoming.read_bytes())
    original_bytes = old.read_bytes()
    existing(cache, 17, old); existing(cache, 18, linked)
    monkeypatch.setattr(helper, 'find_identical_books', lambda *args: {17, 18})
    with cache.connection:
        result = helper.add_acquisition(cache, object(), 'epub', str(incoming), helper.content_digest(incoming))
    assert result['disposition'] == 'imported' and result['book_ids'] == [19]
    assert old.read_bytes() == original_bytes and linked.is_symlink()
    cache.connection.close()


@pytest.mark.parametrize('version,accepted', [(0,False),(1,True),(2,True),(3,True),(4,False)])
def test_receipt_recovery_accepts_only_proven_policies_and_actual_retained_bytes(tmp_path, version, accepted):
    old = package(tmp_path/'old.epub'); incoming = package(tmp_path/'incoming.epub', repack=True)
    import hashlib
    source = hashlib.sha256(incoming.read_bytes()).hexdigest()
    old_digest = hashlib.sha256(old.read_bytes()).hexdigest()
    result = dict(source_sha256=source, imported_sha256=old_digest, book_ids=[17],
                  disposition='existing_retained', format='epub', artifact_identity_version=version)
    database = tmp_path/'metadata.db'
    with sqlite3.connect(database) as connection:
        connection.executescript('CREATE TABLE books (id INTEGER PRIMARY KEY,path TEXT); CREATE TABLE data (book INTEGER,format TEXT,name TEXT); CREATE TABLE cwng_acquisition_ingest_result(source_sha256 TEXT PRIMARY KEY,result_json TEXT); INSERT INTO books VALUES(17,"."); INSERT INTO data VALUES(17,"EPUB","old");')
        connection.execute('INSERT INTO cwng_acquisition_ingest_result VALUES (?,?)', (source,json.dumps(result)))
    recovered = i.read_result(database, source, tmp_path)
    assert (recovered is not None) == accepted
    if accepted:
        assert recovered.source_sha256 == source and recovered.imported_sha256 == old_digest
        old.write_bytes(incoming.read_bytes())
        assert i.read_result(database, source, tmp_path) is None


@pytest.mark.parametrize('candidate_position', [31, 32])
def test_additional_resource_scans_are_capped_and_exhaustion_stays_separate(helper, monkeypatch, tmp_path, candidate_position):
    cache = Cache(tmp_path); incoming = package(tmp_path/'incoming.epub', repack=True)
    for position in range(33):
        path = tmp_path/(str(position)+'.epub')
        if position == candidate_position: package(path)
        else: path.write_bytes(b'unsupported candidate '+str(position).encode())
        existing(cache, 17+position, path)
    monkeypatch.setattr(helper, 'find_identical_books', lambda *args: set(range(17,50)))
    with cache.connection:
        result = helper.add_acquisition(cache, object(), 'epub', str(incoming), helper.content_digest(incoming))
    expected = candidate_position < 32
    assert result['disposition'] == ('existing_retained' if expected else 'imported')
    assert result['book_ids'] == ([17+candidate_position] if expected else [50])
    cache.connection.close()


def test_non_epub_format_never_uses_zip_resource_equivalence(helper, monkeypatch, tmp_path):
    cache = Cache(tmp_path)
    old = package(tmp_path/'old.pdf'); incoming = package(tmp_path/'incoming.pdf', repack=True)
    cache.connection.execute('INSERT INTO books VALUES (17)'); cache.paths[17,'pdf'] = str(old)
    monkeypatch.setattr(helper, 'find_identical_books', lambda *args: {17})
    monkeypatch.setattr(helper, 'epub_resource_digest', lambda *args, **kwargs: pytest.fail('resource identity crossed format boundary'))
    with cache.connection:
        result = helper.add_acquisition(cache, object(), 'pdf', str(incoming), helper.content_digest(incoming))
    assert result['disposition'] == 'imported' and result['book_ids'] == [18]
    cache.connection.close()
