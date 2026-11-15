# SPDX-License-Identifier: GPL-3.0-or-later
"""Original paired-topic authority through real worker/SQLite/local HTTP.

Historical released LT2.0.11 creator vector; no daemon or peer-transfer claim.
The HTTP endpoint models the configured trusted native properties contract.
"""
import base64
import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.unit.test_acquisition_clients import clients, config, document
from tests.unit.test_acquisition_usenet import repo, spec
from tests.unit.test_acquisition_v2_magnets import OwnedDownload

pytestmark = pytest.mark.unit
V1 = 'd3e6c98ab8696750d183a9fcf37d6178452bcce4'
V2 = '6df5bebbb680f6a2eac792e6ed76c79eff25b108f21269f695cd6fdf247f21c9'
ID = V2[:40]
ORIGINAL = ('magnet:?xt=urn:btih:' + V1 + '&xt=urn:btmh:1220' + V2
            + '&dn=Original.epub&tr=http%3a%2f%2f127.0.0.1%3a50737%2fannounce')
TRACKERLESS = ('magnet:?xt=urn%3Abtih%3A' + V1 + '&xt=urn%3Abtmh%3A1220' + V2
               + '&dn=Original.epub')
REVERSED_BASE32 = ('magnet:?xt=urn:btmh:1220' + V2.upper() + '&dn=Original.epub'
                   + '&xt=urn:btih:' + base64.b32encode(bytes.fromhex(V1)).decode().lower())


class PairDownload(OwnedDownload):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, href=kwargs.pop('href', ORIGINAL), tracker='http://127.0.0.1:50737', **kwargs)
        self.identity = ID
        self.properties = dict(hash=ID, has_metadata=True, infohash_v1=V1, infohash_v2=V2)
        self.collision = False
        self.change = None
        self.wire = []
        self.errors = []

    def transfer(self, url, policy, **kw):
        path = urlsplit(url).path
        if path.endswith('/redirect.torrent'):
            return type('Redirect', (), dict(url=ORIGINAL, body=b''))()
        if path.endswith('/torrents/info') and parse_qs(urlsplit(url).query).get('hashes') == [ID + '|' + V1]:
            self.calls.append(path)
            result = document([dict(hash=ID)] if self.collision or self.tag else [])
            if self.change == 'engine': self.engine = '1.2.19.0'
            elif self.change == 'api': self.api = '2.9.3'
            return result
        if path.endswith('/torrents/info') and self.collision:
            self.calls.append(path)
            return document([dict(hash=ID)])
        result = super().transfer(url, policy, **kw)
        if path.endswith('/torrents/info') and self.change:
            if self.change == 'engine': self.engine = '1.2.19.0'
            else: self.api = '2.9.3'
        return result

    def run(self, **kwargs):
        worker = importlib.import_module(spec.name + '.worker')
        http = importlib.import_module(spec.name + '.http')
        result = worker.AcquisitionWorker(self.repo, self.tmp/'staging', self.tmp/'ingest',
            allowed=lambda _: True, transfer=http.run_transfer, **kwargs).run_once()
        assert self.errors == [], self.errors
        return result


@pytest.fixture
def paired(repo, tmp_path, request):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self): self.reply()
        def do_POST(self): self.reply()
        def reply(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            form = {k: v[0] for k, v in parse_qs(raw.decode(), keep_blank_values=True).items()} if raw else None
            fixture.wire.append((self.command, self.path, self.headers.get('Content-Type'), raw, form))
            try:
                if self.path != '/api/v2/auth/login':
                    assert self.headers.get('Cookie') == 'SID=owned'
                if self.path == '/api/v2/torrents/add':
                    assert self.headers.get('Content-Type') == 'application/x-www-form-urlencoded'
                    assert fixture.row()['submission_started'] is not None
                result = fixture.transfer(fixture.endpoint + self.path, None, form=form, upload=None)
                self.send_response(result.status)
                for k, v in result.headers.items(): self.send_header(k, v)
                self.send_header('Content-Length', str(len(result.body)))
                self.end_headers()
                self.wfile.write(result.body)
            except Exception as error:
                fixture.errors.append(repr(error))
                self.send_error(500)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    endpoint = 'http://127.0.0.1:' + str(server.server_port)
    cfg = config(tmp_path, 'qbittorrent')
    cfg.update(endpoint=endpoint, credential_origins=[endpoint],
               private_origins=[endpoint], private_networks=['127.0.0.1/32'])
    fixture = PairDownload(*repo, tmp_path, client_config=cfg, href=getattr(request, 'param', ORIGINAL))
    fixture.endpoint = endpoint
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try: yield fixture
    finally:
        server.shutdown(); server.server_close(); thread.join()
        (tmp_path/'pair-observations.json').write_text(json.dumps(dict(
            wire=fixture.wire, calls=fixture.calls, properties=fixture.properties,
            adds=fixture.adds, files=fixture.files, job=dict(fixture.row()),
            published=[p.name for p in (tmp_path/'ingest').iterdir()],
            errors=fixture.errors), default=lambda value: value.decode() if isinstance(value, bytes) else str(value), indent=2))


@pytest.mark.parametrize('uri', [ORIGINAL, TRACKERLESS, REVERSED_BASE32])
def test_pair_parser_retains_both_normalized_original_hashes(uri):
    torrent = importlib.import_module(spec.name + '.torrent')
    assert torrent.magnet_identities(uri, tracker_origins=['http://127.0.0.1:50737']) == (V1, V2)
    assert torrent.validate_magnet(uri) == V1


@pytest.mark.parametrize('paired', [ORIGINAL, TRACKERLESS, REVERSED_BASE32], indirect=True)
def test_exact_original_pair_form_add_reaches_normal_copy_boundary(paired):
    assert paired.run().state == 'importing'
    assert len(paired.adds) == 1 and paired.adds[0]['urls'] == paired.payload['href']
    assert paired.files == [ID]
    assert next((paired.tmp/'ingest').glob('*.pdf')).read_bytes() == paired.book.read_bytes()
    assert paired.book.stat().st_mode & 0o777 == 0o640
    assert paired.calls.count('/api/v2/app/buildInfo') == 2
    assert paired.calls.count('/api/v2/app/webapiVersion') == 2
    assert all('/torrents/' in p or '/app/' in p or '/auth/' in p for _, p, *_ in paired.wire)


@pytest.mark.parametrize('known', [True, False])
@pytest.mark.parametrize('updates, expected', [
    ({'infohash_v1': None}, 'downloading'), ({'infohash_v1': ''}, 'downloading'),
    ({'infohash_v2': None}, 'downloading'), ({'infohash_v2': ''}, 'downloading'),
    ({'has_metadata': False}, 'downloading'), ({'has_metadata': None}, 'invalid_client_response'),
    ({'infohash_v1': 'a'*40}, 'client_job_mismatch'),
    ({'infohash_v1': 'g'*40}, 'invalid_client_response'),
    ({'infohash_v1': True}, 'invalid_client_response'),
    ({'infohash_v2': ID + '0'*24}, 'client_job_mismatch'),
    ({'infohash_v2': 'a'*64}, 'client_job_mismatch'),
    ({'infohash_v2': ID}, 'invalid_client_response'),
    ({'hash': V1}, 'client_job_mismatch'),
    ({'hash': ''}, 'invalid_client_response'),
])
def test_pair_recovery_blocks_files_and_publication_until_both_full_hashes_match(paired, known, updates, expected):
    paired.fence(known=known)
    before = paired.row()
    paired.properties.update(updates)
    result = paired.run()
    assert (result.state if expected == 'downloading' else result.error_code) == expected
    assert paired.adds == [] and paired.files == []
    assert not list((paired.tmp/'ingest').glob('*'))
    assert not list((paired.tmp/'staging').rglob('*.part'))
    after = paired.row()
    assert after['submission_started'] == before['submission_started']
    assert after['submission_key'] == before['submission_key']
    assert after['submission_invalid'] is not True


@pytest.mark.parametrize('lost_ack', [False, True])
def test_pair_pending_restart_and_uncertain_acceptance_rebind_both_topics(paired, lost_ack):
    paired.properties['has_metadata'] = False
    paired.lose_ack = lost_ack
    # Lost acknowledgement is modeled after accepted add by a response the adapter rejects.
    if lost_ack:
        original = paired.transfer
        def transfer(url, policy, **kw):
            if urlsplit(url).path.endswith('/torrents/add'):
                paired.lose_ack = False
                original(url, policy, **kw)
                return document(b'accepted-but-not-a-valid-ack')
            return original(url, policy, **kw)
        paired.transfer = transfer
    assert paired.run().state == ('failed' if lost_ack else 'downloading')
    before = paired.row()
    if lost_ack: paired.repo.retry(1, paired.job.id)
    else: paired.now[0] += 61
    paired.properties.update(has_metadata=True, infohash_v1='a'*40)
    assert paired.run().error_code == 'client_job_mismatch'
    assert len(paired.adds) == 1 and paired.files == []
    assert paired.row()['submission_key'] == before['submission_key']
    paired.repo.retry(1, paired.job.id)
    paired.properties['infohash_v1'] = V1.upper()
    assert paired.run().state == 'importing'
    assert len(paired.adds) == 1 and paired.files == [ID]


@pytest.mark.parametrize('change', ['engine', 'api'])
def test_pair_collision_lookup_compatibility_change_has_no_fence_then_upgrades(paired, change):
    paired.change = change
    assert paired.run().error_code == 'unsupported_client_version'
    assert all(paired.row()[k] is None for k in ('submission_started', 'submission_key', 'external_id'))
    assert paired.adds == []
    paired.repo.retry(1, paired.job.id)
    paired.change = None; paired.engine = '2.0.11.0'; paired.api = '2.15.1'
    assert paired.run().state == 'importing'
    assert len(paired.adds) == 1


@pytest.mark.parametrize('engine, api', [('1.2.19.0', '2.11.2'), ('unknown', '2.11.2'),
    ('2.0.11.0', '2.11.1'), ('2.0.11.0', '2.15.2')])
def test_pair_incompatible_client_refuses_before_attempt_then_first_original_add(paired, engine, api):
    paired.engine, paired.api = engine, api
    assert paired.run().error_code == 'unsupported_client_version'
    assert paired.adds == [] and paired.files == []
    assert all(paired.row()[k] is None for k in ('submission_started', 'submission_key', 'external_id'))
    paired.repo.retry(1, paired.job.id)
    paired.engine, paired.api = '2.0.11.0', '2.11.2'
    assert paired.run().state == 'importing'
    assert len(paired.adds) == 1 and paired.adds[0]['urls'] == ORIGINAL


def test_pair_transmission_and_legacy_adapter_refuse_even_with_valid_v1(repo, tmp_path):
    fixture = PairDownload(*repo, tmp_path, adapter='transmission')
    assert OwnedDownload.run(fixture).error_code == 'unsupported_client_version'
    assert fixture.calls == []
    assert all(fixture.row()[k] is None for k in ('submission_started', 'submission_key', 'external_id'))
    fixture.repo.retry(1, fixture.job.id)
    class Legacy:
        def __init__(self, *args, **kwargs): pass
        def submit(self, *args, **kwargs): pytest.fail('legacy add')
        def find(self, *args, **kwargs): pytest.fail('legacy find')
    worker = importlib.import_module(spec.name + '.worker')
    assert worker.AcquisitionWorker(fixture.repo, tmp_path/'staging', tmp_path/'ingest',
        allowed=lambda _: True, client_factory=Legacy).run_once().error_code == 'unsupported_client_version'
    assert all(fixture.row()[k] is None for k in ('submission_started', 'submission_key', 'external_id'))


def test_pair_shared_adopter_cannot_import_pure_v2_or_prefix_collision(paired):
    paired.properties['has_metadata'] = False
    assert paired.run().state == 'downloading'
    before = paired.row()
    offer = paired.repo.create_offer(2, before['connection_id'], paired.payload)
    second = paired.repo.create_job(2, offer, 'request', requires_approval=False)
    paired.properties.update(has_metadata=True, infohash_v1='a'*40)
    assert paired.run().error_code == 'client_job_mismatch'
    with paired.repo.engine.connect() as conn:
        after = conn.execute(paired.repo.tables.jobs.select().where(paired.repo.tables.jobs.c.id == second.id)).mappings().one()
    assert after['submission_key'] == before['submission_key']
    assert after['submission_started'] == before['submission_started']
    assert len(paired.adds) == 1 and paired.files == []
    paired.repo.retry(2, second.id)
    paired.properties.update(infohash_v1=V1, infohash_v2=ID + '0'*24)
    assert paired.run().error_code == 'client_job_mismatch'
    assert len(paired.adds) == 1 and paired.files == []
    paired.repo.retry(2, second.id)
    paired.properties['infohash_v2'] = V2
    assert paired.run().state == 'importing'
    assert len(paired.adds) == 1


def test_pair_missing_v1_remains_pending_only_within_existing_deadline(paired):
    paired.fence(known=True)
    paired.properties.pop('infohash_v1')
    assert paired.run().state == 'downloading'
    paired.now[0] += 8*24*60*60
    assert paired.run().error_code == 'client_job_stalled'
    assert paired.adds == [] and paired.files == []
    assert paired.row()['submission_invalid'] is not True


def test_pair_existing_unrelated_collision_can_retry_without_duplicate_add(paired):
    paired.collision = True
    assert paired.run().error_code == 'torrent_already_exists'
    assert all(paired.row()[k] is None for k in ('submission_started', 'submission_key', 'external_id'))
    paired.repo.retry(1, paired.job.id); paired.collision = False
    assert paired.run().state == 'importing'
    assert len(paired.adds) == 1


def test_pair_http_redirect_without_private_durable_pair_refuses_before_fence(repo, tmp_path):
    fixture = PairDownload(*repo, tmp_path, href='https://source.example/redirect.torrent')
    assert OwnedDownload.run(fixture).error_code == 'unsupported_magnet_redirect'
    assert fixture.adds == []
    assert all(fixture.row()[k] is None for k in ('submission_started', 'submission_key', 'external_id'))


@pytest.mark.parametrize('extra', ['urn:btih:' + V1, 'urn:btmh:1220' + V2,
                                  'urn:btmh:1120' + V2, 'urn:btmh:1220' + V2[:-1]])
def test_pair_extra_or_malformed_topics_remain_unsafe(extra):
    torrent = importlib.import_module(spec.name + '.torrent')
    with pytest.raises(torrent.TransportError, match='invalid_magnet'):
        torrent.magnet_identities(TRACKERLESS + '&xt=' + extra)


@pytest.mark.parametrize('topics', [
    ['urn:btih:' + V1, 'urn:btih:' + V1],
    ['urn:btmh:1220' + V2, 'urn:btmh:1220' + V2],
    ['urn:btih:' + V1, 'urn:btmh:1120' + V2],
    ['urn:btih:' + V1, 'urn:btmh:1220' + V2[:-1]],
    ['urn:btmh:1220' + V2, 'urn:btih:' + V1[:-1]],
])
def test_two_topics_require_one_valid_hash_per_family(topics):
    torrent = importlib.import_module(spec.name + '.torrent')
    with pytest.raises(torrent.TransportError, match='invalid_magnet'):
        torrent.magnet_identities('magnet:?' + '&'.join('xt=' + topic for topic in topics))


def test_pair_current_authority_revoked_after_collision_never_fences_or_adds(paired):
    allowed = [True]
    original = paired.transfer
    def transfer(url, policy, **kw):
        result = original(url, policy, **kw)
        if urlsplit(url).path.endswith('/torrents/info'):
            allowed[0] = False
        return result
    paired.transfer = transfer
    assert paired.run(execution_allowed=lambda job: allowed[0]).error_code == 'access_revoked'
    assert paired.adds == [] and paired.files == []
    assert all(paired.row()[k] is None for k in ('submission_started', 'submission_key', 'external_id'))
    assert not list((paired.tmp/'ingest').glob('*'))


@pytest.mark.parametrize('paired,mode',[(ORIGINAL,'collision'),(TRACKERLESS,'collision'),(REVERSED_BASE32,'collision'),(ORIGINAL,'invalid')],indirect=['paired'])
def test_original_v1_collision_or_invalid_read_refuses_without_effect(paired,mode):
 original=paired.transfer;queries=[]
 def transfer(url,policy,**kwargs):
  if urlsplit(url).path.endswith('/torrents/info'):
   ids=parse_qs(urlsplit(url).query).get('hashes',[])
   if ids:
    queries.extend(ids)
    if V1 in ids[0].split('|'):
     return document([dict(hash=V1,category='other',tags='unrelated-owned-tag')] if mode=='collision' else {'malformed':'not rows'})
  return original(url,policy,**kwargs)
 paired.transfer=transfer
 result=paired.run()
 assert result.state=='failed' and result.error_code==('torrent_already_exists' if mode=='collision' else 'invalid_client_response'),result
 assert paired.adds==[] and paired.files==[] and not list((paired.tmp/'ingest').iterdir())
 assert all(paired.row()[k] is None for k in ['submission_started','submission_key','external_id'])
 assert queries==[ID+'|'+V1],queries
