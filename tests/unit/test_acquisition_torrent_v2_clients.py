# SPDX-License-Identifier: GPL-3.0-or-later
"""Original pure-v2 descriptors reach only supported engines before POST fences."""
import importlib
from urllib.parse import urlsplit,parse_qs
import pytest
from tests.unit.test_acquisition_clients import clients,config,document
from tests.unit.test_acquisition_usenet import repo,spec
from tests.unit.test_acquisition_torrent_v2 import RELEASED

pytestmark=pytest.mark.unit
RAW,FULL=RELEASED[0].values
ID=FULL[:40]

@pytest.mark.parametrize('adapter',['qbittorrent','transmission'])
def test_unsupported_pure_v2_client_refuses_before_any_submission(tmp_path,adapter):
    module=clients()
    methods=[]
    def transfer(url,policy,**kw):
        path=urlsplit(url).path;methods.append(path)
        if path.endswith('/auth/login'):return document(b'Ok.',headers={'set-cookie':'SID=owned; HttpOnly'})
        if path.endswith('/app/buildInfo'):return document({'libtorrent':'1.2.19.0'})
        raise AssertionError('Unsupported pure-v2 reached downstream operation '+path)
    client=module.CLIENTS[adapter](config(tmp_path,adapter),transfer=transfer)
    with pytest.raises(module.ClientError,match='unsupported_client_version'):
        client.submit('cwng-owned',RAW)
    assert not any(path.endswith('/torrents/add') for path in methods)


def test_supported_pure_v2_lt2_preparation_is_cached_and_upload_exact(tmp_path):
    module=clients()
    uploads=[];methods=[]
    def transfer(url,policy,**kw):
        path=urlsplit(url).path;methods.append(path)
        if path.endswith('/auth/login'):return document(b'Ok.',headers={'set-cookie':'SID=owned; HttpOnly'})
        if path.endswith('/app/buildInfo'):return document({'libtorrent':'2.0.11.0'})
        if path.endswith('/torrents/info'):
            assert parse_qs(urlsplit(url).query)['hashes']==[ID]
            return document([])
        if path.endswith('/torrents/add'):
            uploads.append(kw['upload'][1]);return document(b'Ok.')
        raise AssertionError(path)
    client=module.QBitClient(config(tmp_path,'qbittorrent'),transfer=transfer)
    assert client.prepare_submission(RAW)==ID
    assert client.submit('cwng-owned',RAW)==ID
    assert uploads==[RAW] and methods.count('/api/v2/app/buildInfo')==1


@pytest.mark.parametrize("adapter",["qbittorrent","transmission"])
def test_unsupported_pure_v2_is_retryable_before_durable_attempt(repo,tmp_path,adapter):
    repository,_=repo;engine=["1.2.19.0"];uploads=[];requests=[]
    client=repository.create_connection("Client",adapter,config(tmp_path,adapter),enabled=True)
    source=repository.create_connection("Source","torznab",dict(secret="",auth_kind="none",username="",credential_origins=[],private_origins=[],private_networks=[],tracker_origins=[]),enabled=True)
    offer=repository.create_offer(1,source.id,dict(kind="acquisition",transport="torrent",media_type="application/x-bittorrent",href="https://source.example/file.torrent",release_key="d"*64,client_id=client.id,client_revision=client.revision))
    job=repository.create_job(1,offer,"request",requires_approval=False)
    def transfer(url,policy,**kw):
        path=urlsplit(url).path;requests.append(path)
        if path.endswith("/file.torrent"):return document(RAW)
        if path.endswith("/auth/login"):return document(b"Ok.",headers={"set-cookie":"SID=owned; HttpOnly"})
        if path.endswith("/app/buildInfo"):return document({"libtorrent":engine[0]})
        if path.endswith("/torrents/info"):
            return document([] if not uploads else [dict(hash=ID,tags=uploads[0]["tags"],category="books",state="downloading",progress=0,amount_left=1848,save_path="/downloads")])
        if path.endswith("/torrents/add"):
            assert kw["upload"][1]==RAW;uploads.append(kw["form"]);return document(b"Ok.")
        if path.endswith("/torrents/files"):return document([dict(name="Original.epub",size=1848,progress=0)])
        raise AssertionError("Unsupported pure-v2 reached RPC: "+path)
    worker=importlib.import_module(spec.name+".worker")
    def run():return worker.AcquisitionWorker(repository,tmp_path/"staging",tmp_path/"ingest",allowed=lambda _:True,transfer=transfer).run_once()
    result=run()
    assert result.state=="failed" and result.error_code=="unsupported_client_version"
    with repository.engine.connect() as conn:
        row=conn.execute(repository.tables.jobs.select().where(repository.tables.jobs.c.id==job.id)).mappings().one()
        assert row["submission_started"] is None and row["external_id"] is None
    assert uploads==[]
    if adapter=="transmission":
        assert requests==["/file.torrent"]
        repository.retry(1,job.id);assert run().error_code=="unsupported_client_version"
        assert uploads==[]
    else:
        engine[0]="2.0.11.0";repository.retry(1,job.id);result=run()
        assert result.state=="downloading" and len(uploads)==1
        with repository.engine.connect() as conn:
            row=conn.execute(repository.tables.jobs.select().where(repository.tables.jobs.c.id==job.id)).mappings().one()
            assert row["external_id"]==ID and row["submission_started"] is not None

@pytest.mark.parametrize('adapter', ['qbittorrent', 'transmission'])
@pytest.mark.parametrize('lease_pause', [0, 61])
def test_preexisting_torrent_refusal_never_fences_and_retry_can_submit(repo, tmp_path, adapter, lease_pause):
    """A remote collision is definite no-upload, not an uncertain acceptance."""
    import json
    from tests.unit.test_acquisition_hybrid_clients import hybrid
    repository, now = repo
    raw, identity = (RAW, ID) if adapter == 'qbittorrent' else hybrid()[:2]
    conflict = [True]
    pause = [lease_pause]
    uploads, fetches = [], []
    client = repository.create_connection('Client', adapter, config(tmp_path, adapter), enabled=True)
    source = repository.create_connection('Source', 'torznab', dict(secret='', auth_kind='none', username='',
        credential_origins=[], private_origins=[], private_networks=[], tracker_origins=[]), enabled=True)
    offer = repository.create_offer(1, source.id, dict(kind='acquisition', transport='torrent',
        media_type='application/x-bittorrent', href='https://source.example/file.torrent',
        release_key='e'*64, client_id=client.id, client_revision=client.revision))
    job = repository.create_job(1, offer, 'request', requires_approval=False)
    def transfer(url, policy, **kw):
        path = urlsplit(url).path
        if path.endswith('/file.torrent'):
            fetches.append(raw)
            return document(raw)
        if path.endswith('/auth/login'):
            return document(b'Ok.', headers={'set-cookie': 'SID=owned; HttpOnly'})
        if path.endswith('/app/buildInfo'):
            return document({'libtorrent': '2.0.11.0'})
        if adapter == 'qbittorrent':
            if path.endswith('/torrents/info'):
                if conflict[0] and pause[0]:
                    now[0] += pause[0]
                    pause[0] = 0
                rows = [dict(hash=identity, tags='unrelated', category='books')] if conflict[0] else []
                if uploads:
                    rows = [dict(hash=identity, tags=uploads[0]['tags'], category='books',
                        state='downloading', progress=0, amount_left=1848, save_path='/downloads')]
                return document(rows)
            if path.endswith('/torrents/add'):
                assert not conflict[0] and kw['upload'][1] == raw
                uploads.append(kw['form'])
                return document(b'Ok.')
            if path.endswith('/torrents/files'):
                return document([dict(name='Original.epub', size=1848, progress=0)])
        else:
            request = json.loads(kw['body'])
            method, args = request['method'], request['arguments']
            if method == 'torrent-get':
                if conflict[0] and pause[0]:
                    now[0] += pause[0]
                    pause[0] = 0
                rows = [dict(hashString=identity)] if conflict[0] else []
                if uploads:
                    rows = [dict(hashString=identity, labels=uploads[0]['labels'], downloadDir='/downloads',
                        status=4, error=0, percentDone=0, leftUntilDone=34, metadataPercentComplete=1,
                        files=[dict(name='Owned.epub', length=34, bytesCompleted=0)])]
                return document(dict(result='success', arguments=dict(torrents=rows)))
            if method == 'torrent-add':
                import base64
                assert not conflict[0] and base64.b64decode(args['metainfo']) == raw
                uploads.append(args)
                return document(dict(result='success', arguments={'torrent-added': dict(hashString=identity)}))
        raise AssertionError('Unexpected collision client operation: '+path)
    worker = importlib.import_module(spec.name+'.worker')
    def run():
        return worker.AcquisitionWorker(repository, tmp_path/'staging', tmp_path/'ingest',
            allowed=lambda _: True, transfer=transfer).run_once()
    result = run()
    if lease_pause:
        assert result.state == 'downloading'
        with repository.engine.connect() as conn:
            stale = conn.execute(repository.tables.jobs.select().where(
                repository.tables.jobs.c.id == job.id)).mappings().one()
        assert stale['submission_started'] is None and stale['submission_key'] is None
        assert stale['external_id'] is None and uploads == []
        result = run()
    assert result.state == 'failed' and result.error_code == 'torrent_already_exists'
    assert uploads == []
    with repository.engine.connect() as conn:
        refused = conn.execute(repository.tables.jobs.select().where(
            repository.tables.jobs.c.id == job.id)).mappings().one()
    assert refused['submission_started'] is None and refused['external_id'] is None
    assert refused['submission_key'] is None and refused['submission_invalid'] is None
    conflict[0] = False
    repository.retry(1, job.id)
    result = run()
    assert result.state == 'downloading' and len(uploads) == 1 and fetches == [raw] * (3 if lease_pause else 2)
    with repository.engine.connect() as conn:
        retried = conn.execute(repository.tables.jobs.select().where(
            repository.tables.jobs.c.id == job.id)).mappings().one()
    assert retried['submission_started'] is not None and retried['external_id'] == identity
    assert retried['submission_invalid'] is False
