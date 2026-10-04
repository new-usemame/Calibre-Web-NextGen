#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Calibre + production import-method/receipt boundary, isolated fixtures.

App tables come from ub.init_db, Calibre tables from LibraryDatabase. The
processor uses the existing runtime probe's minimal object setup: unrelated
network enrichment/notifications are suppressed, not the import or receipt.
This is not a watcher, HTTP, or full NewBookProcessor constructor test.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

APP = Path('/app/calibre-web-automated')
HELPER = APP/'scripts/calibre_ingest_transaction.py'
COMMANDS = []


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(argv):
    result = subprocess.run([str(x) for x in argv], capture_output=True, text=True, timeout=90)
    COMMANDS.append({'argv': [str(x) for x in argv], 'exit': result.returncode})
    if result.returncode:
        raise RuntimeError(f'command failed: {argv}\n{result.stdout}\n{result.stderr}')
    return result.stdout


def ebook(fixture, target, *, title, body, identifiers=None, language=None):
    dc = 'http://purl.org/dc/elements/1.1/'
    opf = 'http://www.idpf.org/2007/opf'
    with zipfile.ZipFile(fixture) as source, zipfile.ZipFile(target, 'w') as output:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.endswith('.opf'):
                root = ET.fromstring(data)
                root.find('.//{'+dc+'}title').text = title
                if language is not None:
                    root.find('.//{'+dc+'}language').text = language
                metadata = root.find('{'+opf+'}metadata')
                for key, value in (identifiers or {}).items():
                    node = ET.SubElement(metadata, '{'+dc+'}identifier', {'{'+opf+'}scheme':key})
                    node.text = value
                data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
            elif info.filename.endswith(('.xhtml', '.html')):
                data = data.replace(b'</body>', ('<p>'+body+'</p></body>').encode())
            output.writestr(info, data)
    return target


def run_helper(library, source, *, acquisition=True, identity_path=None):
    library.mkdir(exist_ok=True)
    identity_path = identity_path or source
    args = ['calibre-debug', '-e', HELPER, '--', '--library-path', library,
            '--path', source, '--identity-path', identity_path, '--automerge', 'overwrite',
            '--expected-import-sha256', digest(source), '--expected-source-sha256', digest(identity_path)]
    if acquisition:
        args.append('--acquisition')
    output = command(args)
    return json.loads(next(line.split('=', 1)[1] for line in reversed(output.splitlines())
                           if line.startswith('CWNG_INGEST_RESULT=')))


def library_format(library, book_id, extension='EPUB'):
    with sqlite3.connect(library/'metadata.db') as conn:
        row = conn.execute('SELECT books.path,data.name FROM books JOIN data ON data.book=books.id WHERE books.id=? AND data.format=?', (book_id,extension)).fetchone()
    return library/row[0]/(row[1]+'.'+extension.lower()) if row else None


def count_books(library):
    with sqlite3.connect(library/'metadata.db') as conn:
        return conn.execute('SELECT count(*) FROM books').fetchone()[0]


def annotations(library, book_id, *, seed=False, language=None):
    """Observe real Calibre annotation records, not a file named 'annotated'."""
    code = ('from calibre.db.legacy import LibraryDatabase; import json; '
            'd=LibraryDatabase('+repr(str(library))+'); c=d.new_api; ')
    if language is not None:
        code += "c.set_field('languages',{"+str(book_id)+":"+repr(language)+"}); "
    if seed:
        bookmark = dict(type='bookmark', title='Original edition bookmark',
                        pos_type='epubcfi', pos='epubcfi(/6/2!/4/2/1:0)',
                        timestamp='2026-10-02T00:00:00Z')
        code += ('c.set_annotations_for_book('+str(book_id)+",'EPUB',[("+
                 repr(bookmark)+',1790899200.0)]); ')
    code += ("print('CWNG_ANNOTATIONS='+json.dumps(c.all_annotations_for_book("+
             str(book_id)+'))); d.close()')
    output = command(['calibre-debug', '-c', code])
    return json.loads(next(line.split('=', 1)[1] for line in output.splitlines()
                           if line.startswith('CWNG_ANNOTATIONS=')))


def language_scenarios(root, fixture):
    results = []
    for language in (['eng'], []):
        case = root/('known-language' if language else 'missing-language')
        case.mkdir()
        original = ebook(fixture, case/'english.epub', title='Language edition fixture',
                         body='Original English passage', language='en')
        german = ebook(fixture, case/'german.epub', title='Language edition fixture',
                       body='Ausgewählter deutscher Text', language='de')
        library = case/'library'
        first = run_helper(library, original)
        before = annotations(library, first['book_ids'][0], seed=True, language=language)
        assert len(before) == 1
        if not language:
            # Reproduce an old metadata-only retention proof. A future request
            # must reinspect it rather than recovering the substituted edition.
            legacy = dict(first, source_sha256=digest(german), disposition='existing_retained')
            legacy.pop('artifact_identity_version', None)
            with sqlite3.connect(library/'metadata.db') as conn:
                conn.execute('INSERT INTO cwng_acquisition_ingest_result VALUES (?,?)',
                             (digest(german), json.dumps(legacy)))
        selected = run_helper(library, german)
        assert selected['disposition'] == 'imported' and selected['book_ids'] != first['book_ids']
        assert digest(library_format(library, selected['book_ids'][0])) == digest(german) == selected['imported_sha256']
        assert digest(library_format(library, first['book_ids'][0])) == digest(original)
        assert annotations(library, first['book_ids'][0]) == before
        assert annotations(library, selected['book_ids'][0]) == []
        assert run_helper(library, german) == dict(selected, status='already_imported')
        results.append(dict(existing_language=language, selected=selected,
                            original_book_id=first['book_ids'][0], annotations_preserved=True))
    return results


def boundary_scenario(root, fixture):
    """Helper recovery and actual receipt verification agree on library paths."""
    from cps.services.acquisition.ingest import read_result
    case = root/'external-format';case.mkdir()
    source = ebook(fixture, case/'source.epub', title='Contained edition fixture', body='Selected bytes')
    library = case/'library'
    first = run_helper(library, source)
    book_id = first['book_ids'][0]
    before = annotations(library, book_id, seed=True)
    stored = library_format(library, book_id)
    outside = case/'outside.epub';outside.write_bytes(stored.read_bytes())
    stored.unlink();stored.symlink_to(outside)
    selected = run_helper(library, source)
    assert selected['disposition']=='imported' and selected['book_ids']!=first['book_ids']
    assert read_result(library/'metadata.db', digest(source), library).book_ids==tuple(selected['book_ids'])
    assert digest(library_format(library, selected['book_ids'][0]))==digest(source)
    assert stored.is_symlink() and digest(outside)==digest(source)
    assert annotations(library, book_id)==before
    return dict(selected=selected, helper_and_receipt_agree=True,
                external_bytes_unchanged=True, annotations_preserved=True)


def helper_scenarios(root, fixture):
    original = ebook(fixture, root/'original.epub', title='Runtime annotated edition', body='Original passage')
    candidate = ebook(fixture, root/'candidate.epub', title='Runtime annotated edition', body='Different candidate passage')
    replacement = ebook(fixture, root/'replacement.epub', title='Runtime annotated edition', body='Explicit later user replacement')
    library = root/'library'
    fresh = run_helper(library, original)
    assert fresh['disposition']=='imported' and fresh['status']=='imported'
    assert fresh['source_sha256']==digest(original)==fresh['imported_sha256']
    assert digest(library_format(library,fresh['book_ids'][0]))==fresh['imported_sha256']
    replay = run_helper(library, original)
    assert replay==dict(fresh,status='already_imported') and count_books(library)==1
    before = annotations(library, fresh['book_ids'][0], seed=True)
    assert len(before) == 1
    distinct = run_helper(library, candidate)
    assert distinct['disposition']=='imported' and distinct['book_ids']!=fresh['book_ids']
    assert distinct['source_sha256']==digest(candidate)==distinct['imported_sha256']
    assert annotations(library, fresh['book_ids'][0])==before
    # A different source can prepare to the identical stored artifact. Retain
    # only that artifact, keeping source identity separate from imported bytes.
    identity = ebook(fixture, root/'repacked.epub', title='Repacked source', body='Pre-plugin source')
    retained = run_helper(library, original, identity_path=identity)
    assert retained['disposition']=='existing_retained' and retained['book_ids']==fresh['book_ids']
    assert retained['source_sha256']==digest(identity) and retained['imported_sha256']==digest(original)
    assert digest(library_format(library,fresh['book_ids'][0]))==digest(original)
    assert annotations(library, fresh['book_ids'][0])==before
    # Remove through real Calibre APIs, then reacquire the original request.
    book_id=fresh['book_ids'][0]
    code = "from calibre.db.legacy import LibraryDatabase; d=LibraryDatabase("+repr(str(library))+"); d.new_api.remove_formats({"+str(book_id)+": ['EPUB']}); d.close()"
    command(['calibre-debug','-c',code])
    removed = run_helper(library, original)
    assert removed['status']=='imported' and removed['disposition']=='imported'
    assert removed['book_ids']!=fresh['book_ids']
    assert digest(library_format(library,removed['book_ids'][0]))==digest(original)
    # Explicit later format replacement must invalidate old acquisition proof.
    book_id=removed['book_ids'][0]
    code = "from calibre.db.legacy import LibraryDatabase; d=LibraryDatabase("+repr(str(library))+"); d.new_api.add_format("+str(book_id)+", 'EPUB', "+repr(str(replacement))+", replace=True, run_hooks=False); d.close()"
    command(['calibre-debug','-c',code])
    replaced = run_helper(library, original)
    assert replaced['status']=='imported' and replaced['disposition']=='imported'
    assert replaced['book_ids']!=removed['book_ids'] and replaced['imported_sha256']==digest(original)
    assert digest(library_format(library,book_id))==digest(replacement)
    assert run_helper(library,original)==dict(replaced,status='already_imported')

    victim=ebook(fixture,root/'victim.epub',title='Victim requested edition',body='Requested bytes')
    source_hash=digest(victim)
    fake_result=json.dumps(dict(source_sha256=source_hash,imported_sha256='b'*64,book_ids=[1],disposition='imported'))
    forged=ebook(fixture,root/'forged.epub',title='Unrelated malicious edition',body='Unrelated bytes',identifiers={
        'cwng_ingest_sha256_'+source_hash:source_hash,
        'cwng_acquisition_result_'+source_hash:fake_result})
    forgery_library=root/'forgery-library'
    seed=run_helper(forgery_library,forged,acquisition=False)
    with sqlite3.connect(forgery_library/'metadata.db') as conn:
        rows=dict(conn.execute('SELECT type,val FROM identifiers WHERE book=?',(seed['book_ids'][0],)))
    assert rows['cwng_ingest_sha256_'+source_hash]==source_hash
    # Calibre normalizes identifier punctuation; prove the forged reserved
    # key/payload arrived, without assuming opaque JSON survives unchanged.
    assert source_hash in rows['cwng_acquisition_result_'+source_hash]
    assert 'book_ids' in rows['cwng_acquisition_result_'+source_hash]
    protected=run_helper(forgery_library,victim)
    assert protected['status']=='imported' and protected['book_ids']!=seed['book_ids']
    assert protected['imported_sha256']==source_hash and count_books(forgery_library)==2
    return dict(fresh=fresh,replay=replay,retained=retained,distinct=distinct,
                annotations_preserved=True,removed_reacquire=removed,
                replacement_reinspect=replaced,forged_identifier_ignored=protected,
                helper_book_count=count_books(library),
                forged_metadata_value=rows['cwng_acquisition_result_'+source_hash])


def receipt_scenario(root, fixture, setup_path):
    # Real production app schema, no fabricated membership/user tables.
    sys.path[:0]=[str(APP),str(APP/'scripts')]
    sys.argv=[sys.argv[0]]
    from cps import ub, config_sql, constants
    from cps.services.acquisition import runtime, staging
    app_db=root/'app.db'
    constants.CONFIG_DIR=str(root)
    ub.init_db(str(app_db))
    config_sql._Base.metadata.create_all(ub.session.bind)
    user=ub.User(name='fixture-reader',email='fixture-reader@example.invalid',
                 role=constants.ROLE_ACQUISITION_ACCESS,has_own_library=True)
    other=ub.User(name='other-fixture',email='other@example.invalid',has_own_library=True)
    ub.session.add_all([user,other,config_sql._Settings()]);ub.session.commit()
    owner=user.id
    ub.session.close();ub.session.bind.dispose()
    original=ebook(fixture,root/'download.epub',title='Actual receipt boundary',body='Receipt bytes')
    ingest_dir=root/'ingest';ingest_dir.mkdir()
    with runtime.open_repository(app_db,initialize_key=True) as repo:
        connection=repo.create_connection('Fixture only','opds',{'endpoint':'https://fixture.invalid/catalog'},enabled=True)
        offer=repo.create_offer(owner,connection.id,{'title':'Actual receipt boundary'})
        job=repo.create_job(owner,offer,'receipt-intent',requires_approval=False,add_to_my_library=True)
        claim=repo.claim()
        repo.advance(job.id,claim.token,'queued','resolving')
        repo.advance(job.id,claim.token,'resolving','downloading')
        repo.advance(job.id,claim.token,'downloading','staged',source_sha256=digest(original),staging_key='receipt')
        permit=repo.prepare_publication(job.id,claim.token,secrets.token_urlsafe(32))
        published=staging.publish(original,ingest_dir,permit,'epub')
    manifest=json.loads(Path(str(published)+'.cwa.json').read_text())
    os.environ['CWA_APP_DB_PATH']=str(app_db)
    import ingest_processor
    spec=importlib.util.spec_from_file_location('_existing_runtime_setup',setup_path)
    setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
    processor=setup._processor(ingest_processor,root,published)
    processor.ingest_folder=str(ingest_dir)
    processor._load_acquisition_intent(manifest)
    calls=[]
    original_call=processor._run_calibre_transaction
    def tracked(*args):
        calls.append(args[-1]);return original_call(*args)
    processor._run_calibre_transaction=tracked
    with sqlite3.connect(app_db) as conn:
        conn.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON acquisition_import_receipt BEGIN SELECT RAISE(ABORT,'fixture-receipt-failure'); END")
    try:
        processor.add_book_to_library(str(published))
    except ingest_processor.RetryIngestSourceError:
        pass
    else:
        raise AssertionError('receipt write failure did not preserve retry state')
    assert digest(original)==digest(published)
    assert Path(str(published)+'.cwa.json').is_file()
    with sqlite3.connect(app_db) as conn:
        assert conn.execute('SELECT count(*) FROM user_library_book').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM acquisition_import_receipt').fetchone()[0]==0
        assert conn.execute('SELECT state FROM acquisition_job').fetchone()[0]=='publishing'
        assert conn.execute('SELECT config_acquisition_enabled FROM settings').fetchone()[0]==0
        conn.execute('DROP TRIGGER reject_receipt')
    assert count_books(Path(processor.library_dir))==1
    before_calls=len(calls)
    processor.add_book_to_library(str(published))
    assert len(calls)==before_calls, 'receipt retry repeated a Calibre import'
    assert processor.acquisition_acknowledged
    assert count_books(Path(processor.library_dir))==1
    with sqlite3.connect(app_db) as conn:
        rows=conn.execute('SELECT user_id,book_id FROM user_library_book').fetchall()
        receipt=conn.execute('SELECT source_sha256,imported_sha256,book_ids_json FROM acquisition_import_receipt').fetchone()
        assert rows==[(owner,processor.last_added_book_ids[0])]
        assert json.loads(receipt[2])==processor.last_added_book_ids
        assert receipt[0]==digest(original)
        assert receipt[1]==digest(library_format(Path(processor.library_dir),rows[0][1]))
        assert conn.execute('SELECT state FROM acquisition_job').fetchone()[0]=='imported'
    assert digest(original)==digest(published) and Path(str(published)+'.cwa.json').is_file()
    return dict(real_ub_schema=True,receipt_failure_rolled_back_membership=True,
                source_retained=True,retry_reimports=False,book_count=1,membership_count=len(rows),
                helper_calls=calls,acknowledged=True,feature_disabled_during_ack=True,
                boundary='NewBookProcessor.add_book_to_library; watcher/main cleanup not exercised')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--fixture',type=Path,required=True)
    parser.add_argument('--setup-probe',type=Path,default=APP/'tests/integration/calibre_ingest_runtime_probe.py')
    args=parser.parse_args()
    result={'calibre_version':command(['calibre-debug','--version']).strip().splitlines()[-1]}
    with tempfile.TemporaryDirectory(prefix='cwng-acquisition-runtime-') as temporary:
        root=Path(temporary)
        helper_root=root/'helper';helper_root.mkdir()
        result['helper']=helper_scenarios(helper_root,args.fixture)
        result['languages']=language_scenarios(helper_root,args.fixture)
        receipt_root=root/'receipt';receipt_root.mkdir()
        result['receipt']=receipt_scenario(receipt_root,args.fixture,args.setup_probe)
        result['boundary']=boundary_scenario(helper_root,args.fixture)
    result['commands']=COMMANDS
    print('CWNG_ACQUISITION_RUNTIME='+json.dumps(result,sort_keys=True))


if __name__=='__main__':
    main()
