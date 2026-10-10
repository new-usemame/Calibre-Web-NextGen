# SPDX-License-Identifier: GPL-3.0-or-later
"""Hybrid IDs at the existing qBittorrent submission/polling boundary."""
import hashlib
import importlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.unit.test_acquisition_clients import clients, config, document
from tests.unit.test_acquisition_torrent_metadata import bencode, validator
from tests.unit.test_acquisition_usenet import repo, spec

pytestmark = pytest.mark.unit


def hybrid():
    payload = b'Original legal hybrid fixture book.'
    info = {'name': 'Owned.epub', 'piece length': 16384, 'length': len(payload),
            'pieces': hashlib.sha1(payload).digest(), 'meta version': 2,
            'file tree': {'Owned.epub': {'': {'length': len(payload),
                                            'pieces root': hashlib.sha256(payload).digest()}}}}
    raw = bencode({'info': info, 'piece layers': {}})
    v1 = hashlib.sha1(bencode(info)).hexdigest()
    v2 = hashlib.sha256(bencode(info)).hexdigest()[:40]
    return raw, v1, v2


@pytest.mark.parametrize('engine', ['1.2.19.0', '2.0.11.0'])
@pytest.mark.parametrize('json_ack', [False, True])
def test_hybrid_client_fences_submits_and_polls_its_released_engine_identity(tmp_path, engine, json_ack):
    raw, v1, v2 = hybrid()
    identity = v2 if engine.startswith('2.') else v1
    uploads, methods = [], []
    def transfer(url, policy, **kw):
        path = urlsplit(url).path
        methods.append(path)
        if path.endswith('/auth/login'):
            return document(b'Ok.', headers={'set-cookie': 'SID=owned; HttpOnly'})
        if path.endswith('/app/buildInfo'):
            return document({'libtorrent': engine})
        if path.endswith('/torrents/info'):
            assert parse_qs(urlsplit(url).query)['hashes'] == [identity]
            rows = [] if not uploads else [dict(hash=identity, tags='cwng-owned', category='books',
                state='uploading', progress=1, amount_left=0, save_path='/downloads')]
            return document(rows)
        if path.endswith('/torrents/add'):
            uploads.append(kw['upload'][1])
            return document(dict(success_count=1, failure_count=0, pending_count=0,
                                 added_torrent_ids=[identity]) if json_ack else b'Ok.')
        if path.endswith('/torrents/files'):
            assert parse_qs(urlsplit(url).query)['hash'] == [identity]
            return document([dict(name='Owned.epub', size=34, progress=1)])
        raise AssertionError(url)
    client = clients().QBitClient(config(tmp_path, 'qbittorrent'), transfer=transfer)
    assert client.submit('cwng-owned', raw) == identity
    assert uploads == [raw]
    result = client.find('cwng-owned', identity)
    assert result['status'] == 'Completed' and result['nzo_id'] == identity
    assert methods.index('/api/v2/app/buildInfo') < methods.index('/api/v2/torrents/add')


@pytest.mark.parametrize('build', [{}, {'libtorrent': None}, {'libtorrent': '3.0.0'},
                                  {'libtorrent': '2.0 unknown'}, [], '2.0.11.0'])
def test_unknown_hybrid_engine_never_reaches_submission(tmp_path, build):
    raw, _, _ = hybrid()
    methods = []
    def transfer(url, policy, **kw):
        path = urlsplit(url).path
        methods.append(path)
        if path.endswith('/auth/login'):
            return document(b'Ok.', headers={'set-cookie': 'SID=owned; HttpOnly'})
        if path.endswith('/app/buildInfo'):
            return document(build)
        raise AssertionError('Unknown engine reached an unsafe downstream operation: '+path)
    client = clients().QBitClient(config(tmp_path, 'qbittorrent'), transfer=transfer)
    with pytest.raises(clients().ClientError):
        client.submit('cwng-owned', raw)
    assert not any(p.endswith('/torrents/add') for p in methods)


def test_hybrid_already_in_client_is_refused_without_upload(tmp_path):
    raw, _, v2 = hybrid()
    def transfer(url, policy, **kw):
        path = urlsplit(url).path
        if path.endswith('/auth/login'):
            return document(b'Ok.', headers={'set-cookie': 'SID=owned; HttpOnly'})
        if path.endswith('/app/buildInfo'):
            return document({'libtorrent': '2.0.11.0'})
        if path.endswith('/torrents/info'):
            assert parse_qs(urlsplit(url).query)['hashes'] == [v2]
            return document([{'hash': v2}])
        raise AssertionError('Pre-existing torrent reached an upload')
    client = clients().QBitClient(config(tmp_path, 'qbittorrent'), transfer=transfer)
    with pytest.raises(clients().ClientError, match='torrent_already_exists'):
        client.submit('cwng-owned', raw)


def test_unknown_hybrid_engine_is_retryable_without_an_uncertain_submission(repo, tmp_path):
    repository, now = repo
    raw, _, v2 = hybrid()
    engine = ['3.0.0']
    uploads = []
    download = repository.create_connection('Client', 'qbittorrent', config(tmp_path, 'qbittorrent'), enabled=True)
    source = repository.create_connection('Source', 'torznab', dict(secret='', auth_kind='none', username='',
        credential_origins=[], private_origins=[], private_networks=[], tracker_origins=[]), enabled=True)
    payload = dict(kind='acquisition', transport='torrent', media_type='application/x-bittorrent', href='https://source.example/file.torrent',
        release_key='c'*64, client_id=download.id, client_revision=download.revision)
    job = repository.create_job(1, repository.create_offer(1, source.id, payload), 'request', requires_approval=False)
    def transfer(url, policy, **kw):
        path = urlsplit(url).path
        if path.endswith('/file.torrent'):
            return document(raw)
        if path.endswith('/auth/login'):
            return document(b'Ok.', headers={'set-cookie': 'SID=owned; HttpOnly'})
        if path.endswith('/app/buildInfo'):
            return document({'libtorrent': engine[0]})
        if path.endswith('/torrents/info'):
            rows = [] if not uploads else [dict(hash=v2, tags=uploads[0]['tags'], category='books',
                state='downloading', progress=0, amount_left=34, save_path='/downloads')]
            return document(rows)
        if path.endswith('/torrents/add'):
            uploads.append(kw['form'])
            return document(b'Ok.')
        if path.endswith('/torrents/files'):
            return document([dict(name='Owned.epub', size=34, progress=0)])
        raise AssertionError(url)
    worker = importlib.import_module(spec.name+'.worker')
    def run():
        return worker.AcquisitionWorker(repository, tmp_path/'staging', tmp_path/'ingest',
            allowed=lambda _: True, transfer=transfer).run_once()
    result = run()
    assert result.state == 'failed' and result.error_code == 'unsupported_client_version'
    with repository.engine.connect() as connection:
        assert connection.execute(repository.tables.jobs.select().where(
            repository.tables.jobs.c.id == job.id)).mappings().one()['submission_started'] is None
    assert uploads == []
    engine[0] = '2.0.11.0'
    repository.retry(1, job.id)
    result = run()
    assert result.state == 'downloading' and len(uploads) == 1
    with repository.engine.connect() as connection:
        row = connection.execute(repository.tables.jobs.select().where(
            repository.tables.jobs.c.id == job.id)).mappings().one()
        assert row['external_id'] == v2 and row['submission_started'] is not None
