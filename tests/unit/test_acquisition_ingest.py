# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import importlib
import json
import secrets
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.unit.test_acquisition_storage import store, connection_offer, s

i = importlib.import_module(s.__package__ + '.ingest')


def published(store, tmp_path):
    repo, tables, now, app_path = store
    _, offer = connection_offer(repo)
    job = repo.create_job(1, offer, 'request', requires_approval=False)
    claim = repo.claim()
    source = tmp_path / 'cwng-acquisition-owned.epub'
    source.write_bytes(b'original downloaded edition')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    repo.advance(job.id, claim.token, 'queued', 'resolving')
    repo.advance(job.id, claim.token, 'resolving', 'downloading')
    repo.advance(job.id, claim.token, 'downloading', 'staged', source_sha256=digest, staging_key='owned')
    permit = repo.prepare_publication(job.id, claim.token, secrets.token_urlsafe(32))
    manifest = dict(action='acquisition_import', job_id=job.id, publication_token=permit.token, staging_key='owned', uploader_user_id=2)
    intent = i.load_intent(repo, source, tmp_path, manifest)
    metadata = tmp_path / 'metadata.db'
    library=tmp_path/'library'; library.mkdir()
    (library/'stored.epub').write_bytes(b'actual stored edition')
    result = dict(source_sha256=digest, imported_sha256=hashlib.sha256(b'actual stored edition').hexdigest(), book_ids=[17], disposition='imported', format='epub')
    with sqlite3.connect(metadata) as con:
        con.executescript('CREATE TABLE books (id INTEGER PRIMARY KEY, path TEXT); CREATE TABLE data (book INTEGER, format TEXT, name TEXT); CREATE TABLE identifiers (book INTEGER,type TEXT,val TEXT); CREATE TABLE cwng_acquisition_ingest_result (source_sha256 TEXT PRIMARY KEY,result_json TEXT); INSERT INTO books VALUES (17,"library"); INSERT INTO data VALUES (17,"EPUB","stored");')
        con.execute('INSERT INTO cwng_acquisition_ingest_result VALUES (?,?)', (digest,json.dumps(result)))
    with repo.engine.begin() as con:
        con.execute(text('CREATE TABLE user (id INTEGER PRIMARY KEY, has_own_library INTEGER)'))
        con.execute(text('INSERT INTO user VALUES (1,1),(2,1)'))
        con.execute(text('CREATE TABLE user_library_book (user_id INTEGER, book_id INTEGER, added_at DATETIME, UNIQUE(user_id,book_id))'))
    return repo, job, source, manifest, intent, metadata, result


def test_receipt_uses_trusted_owner_and_actual_ids_in_same_transaction(store, tmp_path):
    repo, job, source, manifest, intent, metadata, result = published(store, tmp_path)
    i.finalize(repo, intent, result, metadata, tmp_path)
    i.finalize(repo, intent, result, metadata, tmp_path)
    with repo.engine.connect() as con:
        assert con.execute(text('SELECT user_id,book_id FROM user_library_book')).fetchall() == [(1,17)]
    assert repo.get_job(1,job.id).state == 'imported'
    assert source.read_bytes() == b'original downloaded edition'
    assert intent.publication_token not in repr(intent)


@pytest.mark.parametrize('disposition,verified,accepted', [
    ('existing_retained',False,False),('existing_retained',True,True),('imported',False,True)])
def test_recovery_reinspects_legacy_retention_but_keeps_verified_and_imported_results(store,tmp_path,disposition,verified,accepted):
    """Both recovery entry points must require byte-identity proof for retention."""
    repo,job,source,manifest,intent,metadata,result=published(store,tmp_path)
    result['disposition']=disposition
    if verified:result['artifact_identity_version']=1
    with sqlite3.connect(metadata) as con:
        con.execute('UPDATE cwng_acquisition_ingest_result SET result_json=?',(json.dumps(result),))
    recovered=i.read_result(metadata,intent.source_sha256,tmp_path)
    assert (recovered is not None)==accepted
    assert source.read_bytes()==b'original downloaded edition'
    assert repo.get_job(1,job.id).state=='publishing'


@pytest.mark.parametrize('change', ['token','path','bytes','symlink','action'])
def test_forged_or_moved_manifest_cannot_claim_acquisition(store, tmp_path, change):
    repo, job, source, manifest, intent, metadata, result = published(store, tmp_path)
    if change == 'token': manifest['publication_token'] = secrets.token_urlsafe(32)
    if change == 'action': manifest['action'] = 'import'
    if change == 'path':
        other = source.with_name('cwng-acquisition-other.epub'); source.rename(other); source = other
    if change == 'bytes': source.write_bytes(b'substituted')
    if change == 'symlink':
        other = source.with_name('original'); source.rename(other); source.symlink_to(other)
    with pytest.raises(i.IngestIntentError): i.load_intent(repo, source, tmp_path, manifest)
    assert repo.get_job(1,job.id).state == 'publishing'


def test_receipt_db_failure_preserves_source_and_recovery_membership(store, tmp_path):
    repo, job, source, manifest, intent, metadata, result = published(store, tmp_path)
    with repo.engine.begin() as con:
        con.execute(text("CREATE TRIGGER deny_receipt BEFORE INSERT ON acquisition_import_receipt BEGIN SELECT RAISE(ABORT,'injected'); END"))
    with pytest.raises(i.IngestIntentError): i.finalize(repo,intent,result,metadata,tmp_path)
    assert source.exists() and repo.get_job(1,job.id).state == 'publishing'
    with repo.engine.begin() as con:
        assert con.execute(text('SELECT * FROM user_library_book')).fetchall() == []
        con.execute(text('DROP TRIGGER deny_receipt'))
    i.finalize(repo,intent,result,metadata,tmp_path)
    assert repo.get_job(1,job.id).state == 'imported'


@pytest.mark.parametrize('change', ['missing_book','wrong_ids','legacy_marker','wrong_digest'])
def test_receipt_rejects_unproven_or_missing_book_ids(store, tmp_path, change):
    repo, job, source, manifest, intent, metadata, result = published(store, tmp_path)
    if change == 'missing_book':
        with sqlite3.connect(metadata) as con: con.execute('DELETE FROM books')
    if change == 'wrong_ids': result['book_ids'] = [18]
    if change == 'legacy_marker':
        with sqlite3.connect(metadata) as con:
            con.execute('DELETE FROM cwng_acquisition_ingest_result')
            con.execute('INSERT INTO identifiers VALUES (?,?,?)', (17,'cwng_acquisition_result_'+intent.source_sha256,json.dumps(result)))
    if change == 'wrong_digest': result['imported_sha256'] = 'c'*64
    with pytest.raises(i.IngestIntentError): i.finalize(repo,intent,result,metadata,tmp_path)
    assert repo.get_job(1,job.id).state == 'publishing'


@pytest.mark.parametrize('mode', ['deleted','global','opt_out'])
def test_no_personal_membership_for_inapplicable_current_account(store,tmp_path,mode):
    repo, job, source, manifest, intent, metadata, result = published(store,tmp_path)
    with repo.engine.begin() as con:
        if mode == 'deleted': con.execute(text('DELETE FROM user WHERE id=1'))
        if mode == 'global': con.execute(text('UPDATE user SET has_own_library=0 WHERE id=1'))
        if mode == 'opt_out': con.execute(text('UPDATE acquisition_job SET add_to_my_library=0'))
    i.finalize(repo,intent,result,metadata,tmp_path)
    with repo.engine.connect() as con: assert con.execute(text('SELECT * FROM user_library_book')).fetchall() == []
    assert repo.get_job(1,job.id).state == 'imported'

from tests.unit.test_ingest_overwrite_guard import ingest_processor, _processor, _disable_post_import_work


@pytest.mark.parametrize('route', ['fresh','early','concurrent'])
def test_processor_acknowledges_all_exact_result_routes_before_return(ingest_processor,monkeypatch,tmp_path,route):
    from types import SimpleNamespace
    p = _processor(ingest_processor,tmp_path)
    _disable_post_import_work(ingest_processor,p,monkeypatch)
    source = Path(p.filepath); source.write_bytes(b'original')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    p.acquisition_intent = SimpleNamespace(source_path=source,source_sha256=digest)
    result = dict(status='already_imported' if route != 'fresh' else 'imported', source_sha256=digest, imported_sha256=digest, book_ids=[17], disposition='imported')
    p._acquisition_result = lambda value: result if route == 'early' else None
    calls = []
    p._run_calibre_transaction = lambda *args: result
    p._finish_acquisition = lambda value: calls.append(value)
    p._prepare_destructive_overwrite = lambda *args,**kwargs: pytest.fail('acquisition attempted overwrite')
    p.add_book_to_library(str(source))
    assert calls == [result] and p.last_added_book_ids == [17]
    assert source.read_bytes() == b'original'


@pytest.mark.parametrize('manifest', [None, '{broken', '{"action":"import","uploader_user_id":2}'])
def test_main_preserves_reserved_source_when_capability_missing_or_invalid(ingest_processor,monkeypatch,tmp_path,manifest):
    from tests.unit.test_1094_failed_conversion_imports_original import _FakeProcessor
    source = tmp_path/'cwng-acquisition-owned.epub'; source.write_bytes(b'original')
    if manifest is not None: Path(str(source)+'.cwa.json').write_text(manifest)
    fake = _FakeProcessor(str(source),convert_result=(False,''))
    fake._load_acquisition_intent = lambda _: (_ for _ in ()).throw(i.IngestIntentError('invalid'))
    monkeypatch.setattr(ingest_processor,'_acquire_process_lock_or_exit',lambda:None)
    monkeypatch.setattr(ingest_processor,'NewBookProcessor',lambda _:fake)
    assert ingest_processor.main(str(source)) == 3
    assert fake.imported == [] and fake.delete_current_file_calls == 0 and source.exists()


@pytest.mark.parametrize('acknowledged', [False,True])
def test_main_cleanup_keeps_source_until_receipt_acknowledged(ingest_processor,monkeypatch,tmp_path,acknowledged):
    from tests.unit.test_1094_failed_conversion_imports_original import _FakeProcessor
    source = tmp_path/'cwng-acquisition-owned.epub'; source.write_bytes(b'original')
    sidecar=Path(str(source)+'.cwa.json'); sidecar.write_text('{"action":"acquisition_import"}')
    fake = _FakeProcessor(str(source),convert_result=(False,'')); fake.is_target_format=True
    def load(_): fake.acquisition_intent=True
    fake._load_acquisition_intent=load
    def import_book(*args,**kwargs): fake.acquisition_acknowledged=acknowledged
    fake.add_book_to_library=import_book
    fake.delete_current_file=lambda:source.unlink(missing_ok=True)
    monkeypatch.setattr(ingest_processor,'_acquire_process_lock_or_exit',lambda:None)
    monkeypatch.setattr(ingest_processor,'NewBookProcessor',lambda _:fake)
    assert ingest_processor.main(str(source)) == (0 if acknowledged else 1)
    assert source.exists() is not acknowledged and sidecar.exists() is not acknowledged


@pytest.mark.parametrize('change',['replaced','deleted','format_row_deleted'])
def test_new_receipt_cannot_reuse_stale_format_provenance(store,tmp_path,change):
    repo,job,source,manifest,intent,metadata,result=published(store,tmp_path)
    stored=tmp_path/'library/stored.epub'
    if change=='replaced': stored.write_bytes(b'admin replaced this edition')
    if change=='deleted': stored.unlink()
    if change=='format_row_deleted':
        with sqlite3.connect(metadata) as con: con.execute('DELETE FROM data')
    assert i.read_result(metadata,intent.source_sha256,tmp_path) is None
    with pytest.raises(i.IngestIntentError): i.finalize(repo,intent,result,metadata,tmp_path)
    assert repo.get_job(1,job.id).state=='publishing'


def test_split_metadata_database_uses_explicit_library_root(store,tmp_path):
    repo,job,source,manifest,intent,metadata,result=published(store,tmp_path)
    database_dir=tmp_path/'separate-database'; database_dir.mkdir()
    separate=database_dir/'metadata.db'; metadata.rename(separate)
    assert i.read_result(separate,intent.source_sha256,database_dir) is None
    assert i.read_result(separate,intent.source_sha256,tmp_path).book_ids==(17,)
    i.finalize(repo,intent,result,separate,tmp_path)
    assert repo.get_job(1,job.id).state=='imported'
