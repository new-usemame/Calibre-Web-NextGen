# SPDX-License-Identifier: GPL-3.0-or-later
"""Legal loopback completed-bundle fixture through real SAB transport and ingest."""
import json
from email import policy as email_policy
from email.parser import BytesParser
import sqlite3
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from acquisition_calibre_runtime_probe import APP, ebook, digest, library_format


def run_bundle_runtime(root, repo, owner, other_owner, fixture, ingest, library):
    from cps import constants
    from cps.services.acquisition import admission
    from cps.services.acquisition.newznab import connection_config as indexer_config
    from cps.services.acquisition.sabnzbd import connection_config as client_config
    from cps.services.acquisition.storage import NotFound
    from cps.services.acquisition.worker import AcquisitionWorker
    from cwa_db import CWA_DB

    completed = root/'bundle-completed'; owned = completed/'owned'; owned.mkdir(parents=True)
    for name in ('First','Second'):
        ebook(fixture,owned/(name+'.epub'),title='Bundle '+name,
            body='Original owned '+name+' bundle chapter, distinct bytes and identity.')
    originals={p.name:p.read_bytes() for p in owned.iterdir()}
    submits=[]
    class Handler(BaseHTTPRequestHandler):
        def reply(self,value,media='application/json'):
            body=value if isinstance(value,bytes) else json.dumps(value).encode()
            self.send_response(200); self.send_header('Content-Type',media)
            self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            path=urlsplit(self.path); query=parse_qs(path.query)
            if path.path=='/descriptor':
                assert query.get('apikey')==['fixture-indexer-key']
                return self.reply(b'<nzb><file><segments><segment>owned-fixture</segment></segments></file></nzb>','application/x-nzb')
            self.send_error(404)
        def do_POST(self):
            assert urlsplit(self.path).path=='/api'
            body=self.rfile.read(int(self.headers['Content-Length']))
            content_type=self.headers['Content-Type']
            if content_type.startswith('multipart/form-data'):
                message=BytesParser(policy=email_policy.default).parsebytes(
                    ('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+body)
                fields={part.get_param('name',header='content-disposition'):part.get_payload(decode=True).decode()
                    for part in message.iter_parts() if not part.get_filename()}
                assert b'owned-fixture' in body and b'fixture-indexer-key' not in body
            else:
                fields={key:value[0] for key,value in parse_qs(body.decode()).items()}
            assert fields['apikey']=='fixture-sab-key'
            mode=fields['mode']
            if mode=='queue': return self.reply({'queue':{'slots':[]}})
            if mode=='history':
                return self.reply({'history':{'slots':[dict(nzo_id='owned-bundle',filename=submits[0],cat='books',
                    status='Completed',loaded=False,storage='/downloads/owned')] if submits else []}})
            assert mode=='addfile' and content_type.startswith('multipart/form-data')
            submits.append(fields['nzbname']); assert len(submits)==1
            self.reply({'status':True,'nzo_ids':['owned-bundle']})
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    try:
        client=client_config(dict(endpoint=origin+'/api',secret='fixture-sab-key',category='books',
            remote_path='/downloads',local_path=str(completed)))
        # The same deliberate isolated-loopback harness exception as OPDS.
        client.update(private_origins=[origin],private_networks=['127.0.0.0/8'])
        download=repo.create_connection('Owned bundle client','sabnzbd',client,enabled=True)
        config=indexer_config(dict(endpoint=origin+'/indexer',secret='fixture-indexer-key',category='7020',client_id=download.id))
        config.update(private_origins=[origin],private_networks=['127.0.0.0/8'])
        source=repo.create_connection('Owned bundle source','newznab',config,enabled=True)
        payload=dict(kind='acquisition',transport='nzb',media_type='application/x-nzb',
            href=origin+'/descriptor?apikey=fixture-indexer-key',release_key='b'*64,
            client_id=download.id,client_revision=download.revision,title='Owned bundle')
        db=CWA_DB(); db.update_cwa_settings(dict(auto_convert=1,auto_convert_target_format='epub',kindle_epub_fixer=0));db.con.close()
        with sqlite3.connect(root/'app.db') as c:
            c.execute('UPDATE user SET role=? WHERE id=?',(constants.ROLE_ACQUISITION_ACCESS|constants.ROLE_ACQUISITION_AUTO_APPROVE,other_owner))
        worker=AcquisitionWorker(repo,root/'acquisition-staging',ingest,
            allowed=lambda user:admission.account_allowed(root/'app.db',user),
            enabled=lambda:admission.instance_enabled(root/'app.db'),
            execution_allowed=lambda job:admission.job_allowed(root/'app.db',job.id,job.owner_id))
        parent=repo.create_job(owner,repo.create_offer(owner,source.id,payload),'bundle-parent',requires_approval=False)
        current=worker.run_once(); assert current.state=='awaiting_selection', current
        assert worker.run_once() is None
        manifest=repo.bundle_choices(owner,parent.id)
        assert len(manifest['candidates'])==2 and 'relative_path' not in json.dumps(manifest)
        try: repo.bundle_choices(other_owner,parent.id)
        except NotFound: pass
        else: raise AssertionError('Another owner read the manifest')
        outcomes=[]
        def process_choice(actor, anchor, snapshot, name):
            candidate=next(c for c in snapshot['candidates'] if c['name']==name)
            job=admission.select_artifact(repo,actor,anchor.id,snapshot['generation'],candidate['id'])
            assert worker.run_once().state=='importing'
            published=next(ingest.glob('*.epub'))
            assert published.read_bytes()==originals[name]
            result=subprocess.run(['python3',str(APP/'scripts/ingest_processor.py'),str(published)],
                text=True,capture_output=True,timeout=180)
            print('BUNDLE PROCESS '+name+' EXIT '+str(result.returncode)+'\n'+result.stdout+result.stderr,flush=True)
            assert result.returncode==0
            receipt=repo.get_receipt(actor,job.id)
            assert receipt and receipt.source_sha256==digest(owned/name)
            stored=library_format(library,receipt.book_ids[0],'EPUB')
            assert stored and digest(stored)==receipt.imported_sha256
            with sqlite3.connect(root/'app.db') as c:
                assert c.execute('SELECT 1 FROM user_library_book WHERE user_id=? AND book_id=?',(actor,receipt.book_ids[0])).fetchone()
            assert repo.get_job(actor,job.id).state=='imported'
            assert repo.select_book(actor,anchor.id,snapshot['generation'],candidate['id']).id==job.id
            worker.cleanup_completed()
            assert not published.exists() and not Path(str(published)+'.cwa.json').exists()
            assert not (root/'acquisition-staging'/job.id).exists()
            outcomes.append(dict(owner=actor,job_id=job.id,book_ids=list(receipt.book_ids),
                source_sha256=receipt.source_sha256,imported_sha256=receipt.imported_sha256))
        process_choice(owner,parent,manifest,'First.epub')
        process_choice(owner,parent,manifest,'Second.epub')
        assert outcomes[0]['job_id']!=outcomes[1]['job_id'] and outcomes[0]['book_ids']!=outcomes[1]['book_ids']
        # Another account gets its own manifest/job/receipt, sharing one remote
        # submission and byte-identical Calibre content with private membership.
        other=repo.create_job(other_owner,repo.create_offer(other_owner,source.id,payload),'other-bundle',requires_approval=False)
        current=worker.run_once(); assert current.state=='awaiting_selection', current
        other_manifest=repo.bundle_choices(other_owner,other.id)
        assert {c['id'] for c in manifest['candidates']}.isdisjoint(c['id'] for c in other_manifest['candidates'])
        process_choice(other_owner,other,other_manifest,'First.epub')
        assert outcomes[2]['book_ids']==outcomes[0]['book_ids'] and outcomes[2]['job_id']!=outcomes[0]['job_id']
        assert len(submits)==1 and {p.name:p.read_bytes() for p in owned.iterdir()}==originals
        return dict(outcomes=outcomes,remote_submissions=len(submits),source_files_unchanged=True,
            waiting_not_polled=True,private_manifests=True,real_sab_http=True,full_processor_subprocess=True)
    finally:
        server.shutdown();server.server_close();thread.join()
