# SPDX-License-Identifier: GPL-3.0-or-later
"""Pinned released API contracts and safe completed torrent selection."""
import importlib
import json
from types import SimpleNamespace

import pytest
from tests.unit.test_acquisition_usenet import repo, spec


def clients():
    return importlib.import_module(spec.name + '.clients')


def document(body, *, status=200, headers=None):
    return SimpleNamespace(body=body if isinstance(body, bytes) else json.dumps(body).encode(),
        status=status, headers=headers or {}, url='https://client.example/', content_type='application/json')


def config(tmp_path, adapter):
    return clients().connection_config(adapter, {'endpoint': 'https://client.example/',
        'auth_kind': 'basic', 'username': 'admin', 'secret': 'PRIVATE',
        'category': 'books', 'remote_path': '/downloads', 'local_path': str(tmp_path)})


def test_nzbget_24_positional_append_and_restricted_credentials(tmp_path):
    c = clients(); calls = []
    def transfer(url, policy, **kw):
        value = json.loads(kw['body']); calls.append(value)
        if value['method'] == 'version': return document({'result': '24.8'})
        if value['method'] == 'append': return document({'result': 12})
        return document({'error': {'code': 401, 'message': 'PRIVATE'}})
    client = c.NZBGetClient(config(tmp_path, 'nzbget'), transfer=transfer)
    assert client.submit('cwng-owned', b'<nzb><file/></nzb>') == '12'
    assert calls[1]['params'][:4] == ['cwng-owned.nzb', 'PG56Yj48ZmlsZS8+PC9uemI+', 'books', 0]
    with pytest.raises(c.ClientError, match='needs_auth'): client.probe()


@pytest.mark.parametrize('expiry_status',[401,403])
def test_qbit_5_cookie_expiry_reauth_and_complete_all_files(tmp_path,expiry_status):
    c = clients(); calls = []; logins = []; expired = [True]
    def transfer(url, policy, **kw):
        calls.append((url, kw))
        if url.endswith('/auth/login'):
            logins.append(1); return document(b'Ok.', headers={'set-cookie': 'SID=abc123; HttpOnly; path=/'})
        assert kw['headers']['Cookie'] == 'SID=abc123'
        if expired[0]: expired[0] = False; return document(b'Forbidden', status=expiry_status)
        if '/torrents/files' in url: return document([{'name': 'book.epub', 'size': 24, 'progress': 0.5}])
        return document([{'hash': 'a'*40, 'tags': 'cwng-owned', 'category': 'books',
            'save_path': '/downloads', 'state': 'uploading', 'progress': 1, 'amount_left': 0}])
    client = c.QBitClient(config(tmp_path, 'qbittorrent'), transfer=transfer)
    assert client.find('cwng-owned', 'a'*40)['status'] == 'Downloading'
    assert len(logins) == 2
    assert 'PRIVATE' not in repr(calls[1:2])


def test_transmission_4_rpc17_session_handshake_and_partial_seeding(tmp_path):
    c = clients(); calls = []
    def transfer(url, policy, **kw):
        calls.append(kw)
        if len(calls) == 1: return document(b'', status=409, headers={'x-transmission-session-id': 'session123'})
        assert kw['headers']['X-Transmission-Session-Id'] == 'session123'
        return document({'result': 'success', 'arguments': {'torrents': [{
            'hashString': 'a'*40, 'labels': ['books', 'cwng-owned'], 'downloadDir': '/downloads',
            'status': 6, 'error': 0, 'percentDone': 1, 'leftUntilDone': 0, 'metadataPercentComplete': 1,
            'files': [{'name': 'book.epub', 'length': 24, 'bytesCompleted': 12}]}]}})
    client = c.TransmissionClient(config(tmp_path, 'transmission'), transfer=transfer)
    assert client.find('cwng-owned', 'a'*40)['status'] == 'Downloading'
    assert len(calls) == 2


@pytest.mark.parametrize('adapter', ['qbittorrent', 'transmission'])
def test_torrent_selection_is_only_owned_files_copy_safe(tmp_path, adapter):
    c = clients(); folder = tmp_path / 'owned'; folder.mkdir()
    book = folder / 'book.epub'; book.write_bytes(b'book')
    (tmp_path / 'unrelated.pdf').write_bytes(b'%PDF-unrelated')
    cfg = config(tmp_path, adapter)
    assert c.torrent_book(cfg, '/downloads', [{'name': 'owned/book.epub'}])[0] == book
    for name in ('../outside.epub', '/outside.epub', 'owned/../book.epub', 'owned\\book.epub'):
        with pytest.raises(c.ClientError, match='unsafe_completed_path'):
            c.torrent_book(cfg, '/downloads', [{'name': name}])
    with pytest.raises(c.ClientError, match='multiple_books'):
        c.torrent_book(cfg, '/downloads', [{'name': 'owned/book.epub'}, {'name': 'unrelated.pdf'}])
    assert book.read_bytes() == b'book'


def test_torrent_descriptor_rejects_traversal_and_magnet_web_fetch():
    t = importlib.import_module(spec.name + '.torrent')
    with pytest.raises(ValueError): t.validate_magnet('magnet:?xt=urn:btih:' + 'a'*40 + '&xs=http://internal/secret')
    with pytest.raises(ValueError): t.validate_torrent(b'd4:infod6:lengthi1e4:name12:../book.epub12:piece lengthi16384e6:pieces20:aaaaaaaaaaaaaaaaaaaaee')


def test_qbit_524_no_content_login_has_scoped_cookie(tmp_path):
    c = clients(); calls = []
    def transfer(url, policy, **kw):
        calls.append((url, policy, kw))
        if url.endswith('/auth/login'): return document(b'', status=204, headers={'set-cookie':'QBT_SID_8080=current/session+value=; HttpOnly'})
        return document(b'2.15.0')
    client = c.QBitClient(config(tmp_path,'qbittorrent'), transfer=transfer)
    assert client.call('app/webapiVersion').body == b'2.15.0'
    assert calls[1][2]['headers']['Cookie'] == 'QBT_SID_8080=current/session+value='
    assert calls[1][1].authorization is None


@pytest.mark.parametrize('adapter,transport', [('nzbget','nzb'),('qbittorrent','torrent'),('transmission','torrent')])
def test_each_client_reuses_durable_job_restart_failure_and_staleness(repo,tmp_path,adapter,transport):
    c=clients(); w=importlib.import_module(spec.name+'.worker'); h=importlib.import_module(spec.name+'.http')
    repository,now=repo
    client=repository.create_connection('Client',adapter,config(tmp_path,adapter),enabled=True)
    indexer=repository.create_connection('Indexer','newznab',{'secret':'PRIVATE','auth_kind':'none','username':'','credential_origins':[],'private_origins':[],'private_networks':[]},enabled=True)
    payload={'kind':'acquisition','transport':transport,'media_type':'application/x-nzb' if transport=='nzb' else 'application/x-bittorrent','href':'https://indexer.example/descriptor','client_id':client.id,'client_revision':1,'release_key':'b'*64}
    if transport=='torrent': payload['href']='magnet:?xt=urn:btih:'+'a'*40
    offers=[repository.create_offer(owner,indexer.id,payload) for owner in (1,1,2)]
    first=repository.create_job(1,offers[0],'first',requires_approval=False)
    assert repository.create_job(1,offers[1],'double-click',requires_approval=False).id==first.id
    second=repository.create_job(2,offers[2],'other-owner',requires_approval=False)
    submissions=[]; state=['Downloading']
    class FakeClient:
        def __init__(self,*a,**k): pass
        def submit(self,name,*a,**k): submissions.append(name); return 'owned'
        def find(self,name,external_id=None,**k): return {'nzo_id':'owned','status':state[0]}
    def worker(): return w.AcquisitionWorker(repository,tmp_path/'staging',tmp_path/'ingest',allowed=lambda _:True,client_factory=FakeClient,download_deadline_seconds=60,transfer=lambda url,*a,**k:h.FetchedDocument(b'<nzb><file/></nzb>',url,'text/xml'))
    assert worker().run_once().state=='downloading'
    assert worker().run_once().state=='downloading'  # second subscriber, new worker instance
    assert len(submissions)==1
    now[0]+=61
    assert worker().run_once().error_code=='client_job_stalled'
    assert len(submissions)==1
    state[0]='Failed'
    failed=worker().run_once()
    assert failed.state=='failed' and failed.error_code=='client_job_failed'
    assert {repository.get_job(1,first.id).error_code,repository.get_job(2,second.id).error_code} == {'client_job_failed'}


def test_torrent_reported_directory_cannot_authorize_an_unreported_book(tmp_path):
    c=clients(); folder=tmp_path/'owned.epub';folder.mkdir();(folder/'unreported.epub').write_bytes(b'book')
    with pytest.raises(c.ClientError,match='unsafe_completed_path'):
        c.torrent_book(config(tmp_path,'qbittorrent'),'/downloads',[{'name':'owned.epub'}])


@pytest.mark.parametrize('adapter', ['qbittorrent','transmission'])
def test_failed_torrent_retry_preserves_owned_identity_for_reconciliation(repo,tmp_path,adapter):
    repository,now=repo; c=clients()
    client=repository.create_connection('Client',adapter,config(tmp_path,adapter),enabled=True)
    source=repository.create_connection('Source','newznab',{},enabled=True)
    payload={'transport':'torrent','client_id':client.id,'client_revision':1,'release_key':'c'*64}
    job=repository.create_job(1,repository.create_offer(1,source.id,payload),'request',requires_approval=False)
    claim=repository.claim(); repository.advance(job.id,claim.token,'queued','resolving');repository.advance(job.id,claim.token,'resolving','downloading')
    repository.begin_submission(job.id,claim.token);repository.record_external(job.id,claim.token,'a'*40)
    prior=repository.submission_identity(job.id,claim.token)
    repository.fail_submission_adopters(job.id,claim.token)
    repository.advance(job.id,claim.token,'downloading','failed',error_code='client_job_failed')
    repository.retry(1,job.id);claim=repository.claim()
    assert repository.submission_identity(job.id,claim.token)==prior
    repository.advance(job.id,claim.token,'queued','resolving')
    repository.advance(job.id,claim.token,'resolving','downloading')
    repository.release(job.id,claim.token,delay_seconds=30)
    second=repository.create_job(2,repository.create_offer(2,source.id,payload),'second-owner',requires_approval=False)
    claim=repository.claim()
    assert claim.job.id==second.id
    assert not repository.begin_submission(second.id,claim.token)
    assert repository.submission_identity(second.id,claim.token)==prior


def test_tracker_and_bootstrap_authority_is_admin_defined():
    t=importlib.import_module(spec.name+'.torrent')
    def bencode(v):
        if isinstance(v,int): return b'i'+str(v).encode()+b'e'
        if isinstance(v,str): v=v.encode()
        if isinstance(v,bytes): return str(len(v)).encode()+b':'+v
        if isinstance(v,list): return b'l'+b''.join(bencode(x) for x in v)+b'e'
        return b'd'+b''.join(bencode(k)+bencode(v[k]) for k in sorted(v))+b'e'

    raw={'info':{'name':'book.epub','length':1,'piece length':16384,'pieces':b'a'*20}}
    raw['announce']='http://tracker.example/announce'
    with pytest.raises(t.TransportError,match='untrusted_torrent_tracker'):
        t.validate_torrent(bencode(raw),tracker_origins=[])
    assert len(t.validate_torrent(bencode(raw),tracker_origins=['http://tracker.example']))==40
    raw['nodes']=[['169.254.169.254',80]]
    with pytest.raises(t.TransportError): t.validate_torrent(bencode(raw),tracker_origins=['http://tracker.example'])
    for url in ('http://169.254.169.254/announce','file:///etc/passwd','http://user:secret@tracker.example/announce'):
        with pytest.raises(t.TransportError):t.validate_magnet('magnet:?xt=urn:btih:'+'a'*40+'&tr='+url,tracker_origins=['http://tracker.example'])


@pytest.mark.parametrize('status,expected',[('SUCCESS/ALL','Completed'),('SUCCESS/UNPACK','Completed'),('SUCCESS/HEALTH','Completed'),('SUCCESS/PAR','Failed'),('SUCCESS/MARK','Failed'),('WARNING/SCRIPT','Failed'),('FAILURE/UNPACK','Failed')])
def test_nzbget_pinned_final_history_statuses(tmp_path,status,expected):
    c=clients()
    def transfer(url,policy,**kw):
        method=json.loads(kw['body'])['method']
        return document({'result': [] if method=='listgroups' else [{'NZBID':1,'NZBName':'cwng-owned','Category':'books','Status':status,'DestDir':'/downloads/owned'}]})
    assert c.NZBGetClient(config(tmp_path,'nzbget'),transfer=transfer).find('cwng-owned','1')['status']==expected


def test_encoded_source_key_cannot_be_forwarded_to_an_allowed_tracker():
    t=importlib.import_module(spec.name+'.torrent')
    with pytest.raises(t.TransportError,match='untrusted_torrent_tracker'):
        t.validate_magnet('magnet:?xt=urn:btih:'+'a'*40+'&tr=https%3A%2F%2Ftracker.example%2Fannounce%3Fkey%3D%2550%2552%2549%2556%2541%2554%2545',tracker_origins=['https://tracker.example'],secret='PRIVATE')


def test_qbit_524_json_add_ack_persists_hash_before_followup_auth(tmp_path):
    c=clients(); identity='a'*40
    def transfer(url,policy,**kw):
        if url.endswith('/auth/login'): return document(b'',status=204,headers={'set-cookie':'QBT_SID_8080=encoded/session+value='})
        if url.endswith('/torrents/info?hashes='+identity):return document([])
        if url.endswith('/torrents/add'):return document({'success_count':1,'failure_count':0,'pending_count':0,'added_torrent_ids':[identity]})
        pytest.fail('Submission must return its hash before doing followup polling')
    assert c.QBitClient(config(tmp_path,'qbittorrent'),transfer=transfer).submit('cwng-owned','magnet:?xt=urn:btih:'+identity)==identity


@pytest.mark.parametrize('variant',['nzbget-24.8','nzbget-26.3','qbittorrent-5.0','qbittorrent-5.2.4','transmission-4.0.6','transmission-4.1.3'])
def test_pinned_released_compatibility_fixtures(tmp_path,variant):
    from pathlib import Path
    data=json.loads((Path(__file__).parents[1]/'fixtures/acquisition-clients.json').read_text())[variant];c=clients();identity='a'*40
    def transfer(url,policy,**kw):
        if variant.startswith('nzbget'):
            value=json.loads(kw['body'])
            if value['method']=='version': return document({'result':data['version']})
            assert value['method']=='append' and len(value['params'])==data['append_parameters']
            return document({'result':1})
        if variant.startswith('qbittorrent'):
            if url.endswith('/auth/login'):return document(data['login_body'].encode(),status=data['login_status'],headers={'set-cookie':data['cookie']})
            if '/torrents/info?' in url:return document([])
            if url.endswith('/torrents/add'):return document(data['add_body'].encode() if isinstance(data['add_body'],str) else data['add_body'])
            pytest.fail('Unexpected API operation')
        method=json.loads(kw['body'])['method']
        if method=='session-get':return document({'result':'success','arguments':{'rpc-version':data['rpc_version'],'rpc-version-semver':data['rpc_version_semver']}})
        if method=='torrent-get':return document({'result':'success','arguments':{'torrents':[]}})
        return document({'result':'success','arguments':{'path':'/downloads','size-bytes':1024}})
    if variant.startswith('nzbget'): assert c.NZBGetClient(config(tmp_path,'nzbget'),transfer=transfer).submit('cwng-owned',b'<nzb><file/></nzb>')=='1'
    elif variant.startswith('qbittorrent'): assert c.QBitClient(config(tmp_path,'qbittorrent'),transfer=transfer).submit('cwng-owned','magnet:?xt=urn:btih:'+identity)==identity
    else: assert c.TransmissionClient(config(tmp_path,'transmission'),transfer=transfer).probe()['api_version']==data['rpc_version']


@pytest.mark.parametrize('extra',[{'meta version':2,'file tree':{}},{'attr':b'l','symlink path':['outside.epub']}])
def test_v1_descriptor_refuses_hybrid_and_single_file_symlink_semantics(extra):
    def encode(value):
        if isinstance(value,int):return b'i'+str(value).encode()+b'e'
        if isinstance(value,str):value=value.encode()
        if isinstance(value,bytes):return str(len(value)).encode()+b':'+value
        if isinstance(value,list):return b'l'+b''.join(encode(v) for v in value)+b'e'
        return b'd'+b''.join(encode(k)+encode(value[k]) for k in sorted(value))+b'e'
    t=importlib.import_module(spec.name+'.torrent')
    raw=encode({'info':dict({'name':'book.epub','length':1,'piece length':16384,'pieces':b'a'*20},**extra)})
    with pytest.raises(t.TransportError,match='invalid_torrent'):t.validate_torrent(raw)
