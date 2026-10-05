# SPDX-License-Identifier: GPL-3.0-or-later
"""Reviewed metainfo through real loopback Transmission transport and ingest.

The server implements the pinned RPC contract; it is not a running torrent
client or a peer download. Product adapters and child transport are unchanged.
"""
import base64
import hashlib
import json
import sqlite3
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from acquisition_calibre_runtime_probe import APP, ebook, digest, library_format


def bencode(value):
    if isinstance(value, int): return b'i'+str(value).encode()+b'e'
    if isinstance(value, str): value=value.encode()
    if isinstance(value, bytes): return str(len(value)).encode()+b':'+value
    if isinstance(value, list): return b'l'+b''.join(bencode(v) for v in value)+b'e'
    return b'd'+b''.join(bencode(k)+bencode(value[k]) for k in sorted(value))+b'e'


def run_torrent_metadata_runtime(root, repo, owner, fixture, ingest, library):
    from cps.services.acquisition import admission
    from cps.services.acquisition.clients import connection_config
    from cps.services.acquisition.newznab import connection_config as indexer_config
    from cps.services.acquisition.worker import AcquisitionWorker
    from cwa_db import CWA_DB

    db=CWA_DB();db.update_cwa_settings(dict(auto_convert=1,auto_convert_target_format='epub',kindle_epub_fixer=0));db.con.close()
    outcomes=[]
    for multi in (False, True):
        case='multi' if multi else 'single'
        completed=root/('metadata-'+case); completed.mkdir()
        name='Owned '+case+'.epub'
        folder=completed/'owned' if multi else completed
        folder.mkdir(exist_ok=True)
        book=ebook(fixture,folder/name,title='Metadata '+case,
            body='Original legal '+case+' metainfo fixture.')
        book.chmod(0o755)
        original_mode=book.stat().st_mode
        original=book.read_bytes()
        row=dict(length=len(original),sha1=hashlib.sha1(original).digest(),attr=b'hx')
        payload=original
        info={'name':'owned' if multi else name,'piece length':16384,'private':1}
        files=[dict(name=('owned/' if multi else '')+name,length=len(original),bytesCompleted=len(original))]
        if multi:
            padding=16384-len(original)
            companion=b'Original companion.'
            (folder/'readme.txt').write_bytes(companion)
            payload+=bytes(padding)+companion
            info['files']=[dict(row,path=[name]),dict(length=padding,path=['.pad',str(padding)],attr=b'p'),
                dict(length=len(companion),path=['readme.txt'])]
            # BEP47 clients may omit synthetic padding on disk and from files.
            files.append(dict(name='owned/readme.txt',length=len(companion),bytesCompleted=len(companion)))
        else:
            info.update(row)
        info['pieces']=b''.join(hashlib.sha1(payload[i:i+16384]).digest() for i in range(0,len(payload),16384))
        identity=hashlib.sha1(bencode(info)).hexdigest()
        submitted=[]; descriptor=[]
        source_files={p.relative_to(completed).as_posix():p.read_bytes() for p in completed.rglob('*') if p.is_file()}

        class Handler(BaseHTTPRequestHandler):
            def reply(self,value,media='application/json'):
                body=value if isinstance(value,bytes) else json.dumps(value).encode()
                self.send_response(200);self.send_header('Content-Type',media)
                self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def do_GET(self):
                assert self.path=='/descriptor?apikey=fixture-indexer-key'
                self.reply(descriptor[0],'application/x-bittorrent')
            def do_POST(self):
                assert self.path=='/rpc'
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                method=request['method'];args=request['arguments']
                if method=='torrent-get':
                    rows=[]
                    if submitted:
                        rows=[dict(hashString=identity,labels=submitted[0]['labels'],downloadDir='/downloads',
                            files=files,status=6,metadataPercentComplete=1,percentDone=1,leftUntilDone=0,error=0)]
                    return self.reply(dict(result='success',arguments=dict(torrents=rows)))
                assert method=='torrent-add' and len(submitted)==0
                assert base64.b64decode(args['metainfo'])==descriptor[0]
                assert args['download-dir']=='/downloads' and args['paused'] is False
                submitted.append(args)
                self.reply(dict(result='success',arguments={'torrent-added':dict(hashString=identity)}))
            def log_message(self,*args): pass

        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        origin='http://127.0.0.1:'+str(server.server_port)
        descriptor.append(bencode(dict(announce=origin+'/announce',info=info)))
        try:
            client=connection_config('transmission',dict(endpoint=origin+'/rpc',auth_kind='none',
                category='books',remote_path='/downloads',local_path=str(completed)))
            client.update(private_origins=[origin],private_networks=['127.0.0.0/8'])
            download=repo.create_connection('Metadata '+case+' client','transmission',client,enabled=True)
            config=indexer_config(dict(endpoint=origin+'/indexer',secret='fixture-indexer-key',category='7020',
                client_id=download.id,tracker_origins=[origin]))
            config.update(private_origins=[origin],private_networks=['127.0.0.0/8'])
            source=repo.create_connection('Metadata '+case+' source','torznab',config,enabled=True)
            offer=dict(kind='acquisition',transport='torrent',media_type='application/x-bittorrent',
                href=origin+'/descriptor?apikey=fixture-indexer-key',release_key=hashlib.sha256(descriptor[0]).hexdigest(),
                client_id=download.id,client_revision=download.revision,title='Metadata '+case)
            job=repo.create_job(owner,repo.create_offer(owner,source.id,offer),'metadata-'+case,requires_approval=False)
            worker=AcquisitionWorker(repo,root/'acquisition-staging',ingest,
                allowed=lambda user:admission.account_allowed(root/'app.db',user),
                enabled=lambda:admission.instance_enabled(root/'app.db'),
                execution_allowed=lambda job:admission.job_allowed(root/'app.db',job.id,job.owner_id))
            current=worker.run_once();assert current.state=='importing',current
            with sqlite3.connect(root/'app.db') as c:
                assert c.execute('SELECT external_id FROM acquisition_job WHERE id=?',(job.id,)).fetchone()==(identity,)
            published=next(ingest.glob('*.epub'));assert published.read_bytes()==original
            assert not published.stat().st_mode & 0o111
            result=subprocess.run(['python3',str(APP/'scripts/ingest_processor.py'),str(published)],
                text=True,capture_output=True,timeout=180)
            print('METADATA PROCESS '+case+' EXIT '+str(result.returncode)+'\n'+result.stdout+result.stderr,flush=True)
            assert result.returncode==0
            receipt=repo.get_receipt(owner,job.id)
            assert receipt and receipt.source_sha256==digest(book)
            stored=library_format(library,receipt.book_ids[0],'EPUB')
            assert stored and digest(stored)==receipt.imported_sha256
            assert repo.get_job(owner,job.id).state=='imported'
            assert len(submitted)==1
            assert book.stat().st_mode==original_mode
            assert {p.relative_to(completed).as_posix():p.read_bytes() for p in completed.rglob('*') if p.is_file()}==source_files
            worker.cleanup_completed();assert not published.exists()
            outcomes.append(dict(case=case,v1_infohash=identity,book_ids=list(receipt.book_ids),
                exact_descriptor_submitted=True,source_files_unchanged=True,remote_submissions=1))
        finally:
            server.shutdown();server.server_close();thread.join()
    assert outcomes[0]['book_ids']!=outcomes[1]['book_ids']
    return dict(outcomes=outcomes,loopback_transmission_rpc=True,full_processor_subprocess=True)
