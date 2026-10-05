# SPDX-License-Identifier: GPL-3.0-or-later
"""Original single btmh admission and durable full-identity completion boundaries.

The URI/full digest are released libtorrent 2.0.11 sessionless creator output.
Wire doubles model released qBittorrent properties, not a daemon or peer transfer.
"""
import importlib
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.unit.test_acquisition_clients import clients, config, document
from tests.unit.test_acquisition_usenet import repo, spec

pytestmark = pytest.mark.unit
FULL = 'c7983def0e14f59d0544435bb5b005f33a593f110ee6853d92a9493ab83e8508'
ID = FULL[:40]
ORIGINAL = ('magnet:?xt=urn:btmh:1220' + FULL
            + '&dn=Original.epub&tr=http%3a%2f%2f127.0.0.1%3a64155%2fannounce')
TRACKERLESS = 'magnet:?xt=urn%3Abtmh%3A1220' + FULL + '&dn=Original.epub'
TRACKER = 'http://127.0.0.1:64155'


class OwnedDownload:
    """Real SQLite attempts plus observable client wire and publication files."""
    def __init__(self, repository, now, tmp_path, *, adapter='qbittorrent', href=ORIGINAL):
        self.repo, self.now, self.tmp = repository, now, tmp_path
        self.engine = '2.0.11.0'
        self.api = '2.11.2'
        self.properties = dict(hash=ID, has_metadata=True, infohash_v2=FULL)
        self.adds, self.files, self.calls = [], [], []
        self.tag = None
        self.lose_ack = False
        self.client = repository.create_connection('Client', adapter, config(tmp_path, adapter), enabled=True)
        source = repository.create_connection('Source', 'torznab', dict(secret='', auth_kind='none',
            username='', credential_origins=[], private_origins=[], private_networks=[],
            tracker_origins=[TRACKER]), enabled=True)
        self.payload = dict(kind='acquisition', transport='torrent', media_type='application/x-bittorrent',
            href=href, release_key='b'*64, client_id=self.client.id, client_revision=self.client.revision)
        self.offer = repository.create_offer(1, source.id, self.payload)
        self.job = repository.create_job(1, self.offer, 'request', requires_approval=False)
        self.book = tmp_path / 'Owned.pdf'
        self.book.write_bytes(b'%PDF-1.7\nOriginal legal fixture\n%%EOF\n')
        self.book.chmod(0o640)
        (tmp_path / 'ingest').mkdir()

    def row(self):
        with self.repo.engine.connect() as conn:
            return conn.execute(self.repo.tables.jobs.select().where(
                self.repo.tables.jobs.c.id == self.job.id)).mappings().one()

    def fence(self, *, known):
        claim = self.repo.claim()
        self.repo.advance(self.job.id, claim.token, 'queued', 'resolving')
        self.repo.advance(self.job.id, claim.token, 'resolving', 'downloading')
        assert self.repo.begin_submission(self.job.id, claim.token)
        _, _, key = self.repo.submission_identity(self.job.id, claim.token)
        self.tag = 'cwng-' + self.repo.box.display_identity(str([
            self.payload['release_key'], self.client.id, self.client.revision, key]))
        if known:
            self.repo.record_external(self.job.id, claim.token, ID)
        self.repo.release(self.job.id, claim.token)

    def transfer(self, url, policy, **kw):
        path = urlsplit(url).path
        self.calls.append(path)
        if path.endswith('/redirect.torrent'):
            return type('Redirect', (), dict(url=ORIGINAL, body=b''))()
        if path.endswith('/auth/login'):
            return document(b'Ok.', headers={'set-cookie': 'SID=owned; HttpOnly'})
        if path.endswith('/app/buildInfo'):
            return document({'libtorrent': self.engine})
        if path.endswith('/app/webapiVersion'):
            return document(self.api.encode())
        if path.endswith('/torrents/info'):
            query = parse_qs(urlsplit(url).query)
            if 'hashes' in query:
                assert query['hashes'] == [ID]
            if not self.tag:
                return document([])
            return document([dict(hash=ID, tags=self.tag, category='books', state='uploading',
                progress=1, amount_left=0, save_path='/downloads')])
        if path.endswith('/torrents/add'):
            assert kw['upload'] is None and kw['form']['urls'] == self.payload['href']
            self.adds.append(kw['form'].copy())
            self.tag = kw['form']['tags']
            if self.lose_ack:
                raise clients().ClientError('submission_ambiguous')
            return document(b'Ok.')
        if path.endswith('/torrents/properties'):
            assert parse_qs(urlsplit(url).query)['hash'] == [ID]
            return document(self.properties)
        if path.endswith('/torrents/files'):
            self.files.append(ID)
            return document([dict(name=self.book.name, size=self.book.stat().st_size, progress=1)])
        raise AssertionError('Unexpected client/source effect: ' + path)

    def run(self):
        worker = importlib.import_module(spec.name + '.worker')
        return worker.AcquisitionWorker(self.repo, self.tmp/'staging', self.tmp/'ingest',
            allowed=lambda _: True, transfer=self.transfer).run_once()


@pytest.mark.parametrize('uri', [ORIGINAL, TRACKERLESS])
def test_original_single_btmh_preserves_full_digest_and_exact_submit_uri(tmp_path, uri):
    """Changing a topic or reserializing its URI must break the identity/wire proof."""
    module = clients()
    torrent = importlib.import_module(spec.name + '.torrent')
    assert torrent.validate_magnet(uri, tracker_origins=[TRACKER]) == ID
    hashes = torrent.magnet_identities(uri, tracker_origins=[TRACKER])
    assert hashes.v1 is None and hashes.v2 == FULL
    calls, fenced = [], []
    def transfer(url, policy, **kw):
        path = urlsplit(url).path
        calls.append(path)
        if path.endswith('/auth/login'):
            return document(b'Ok.', headers={'set-cookie': 'SID=owned; HttpOnly'})
        if path.endswith('/app/buildInfo'):
            return document({'libtorrent': '2.0.11.0'})
        if path.endswith('/app/webapiVersion'):
            return document(b'2.11.2')
        if path.endswith('/torrents/info'):
            return document([])
        if path.endswith('/torrents/add'):
            assert fenced == [True] and kw['form']['urls'] == uri and kw['upload'] is None
            return document(b'Ok.')
        raise AssertionError(path)
    client = module.QBitClient(config(tmp_path, 'qbittorrent'), transfer=transfer)
    assert client.prepare_submission(uri) == ID
    def fence():
        fenced.append(True)
        return 'cwng-owned'
    assert client.submit_fenced(uri, fence) == ID
    assert calls.count('/api/v2/app/buildInfo') == 2
    assert calls.count('/api/v2/app/webapiVersion') == 2


@pytest.mark.parametrize('properties, expected', [
    ({'hash': ID, 'has_metadata': False, 'infohash_v2': FULL}, 'downloading'),
    ({'hash': ID, 'infohash_v2': FULL}, 'downloading'),
    ({'hash': ID, 'has_metadata': True, 'infohash_v2': ''}, 'downloading'),
    ({'hash': ID, 'has_metadata': True}, 'downloading'),
    ({'hash': ID, 'has_metadata': '', 'infohash_v2': FULL}, 'invalid_client_response'),
    ({'hash': ID, 'has_metadata': 1, 'infohash_v2': FULL}, 'invalid_client_response'),
    ({'hash': ID, 'has_metadata': True, 'infohash_v2': ID}, 'invalid_client_response'),
    ({'hash': '', 'has_metadata': True, 'infohash_v2': FULL}, 'invalid_client_response'),
    ({'hash': ID, 'has_metadata': True, 'infohash_v2': ID + '0'*24}, 'client_job_mismatch'),
])
@pytest.mark.parametrize('known', [True, False])
def test_recovered_attempt_never_reads_files_without_usable_matching_full_metadata(repo, tmp_path, properties, expected, known):
    """A durable known-ID or tag-only recovery cannot import a prefix collision."""
    fixture = OwnedDownload(*repo, tmp_path)
    fixture.fence(known=known)
    before = fixture.row()
    fixture.properties = properties
    result = fixture.run()
    assert fixture.adds == [] and fixture.files == []
    assert (result.state if expected == 'downloading' else result.error_code) == expected
    assert not list((tmp_path/'ingest').glob('*'))
    after = fixture.row()
    assert after['submission_started'] == before['submission_started']
    assert after['submission_key'] == before['submission_key']
    assert after['submission_invalid'] is not True


@pytest.mark.parametrize('lost_ack', [False, True])
def test_pending_metadata_restart_and_lost_ack_recover_exactly_one_submission(repo, tmp_path, lost_ack):
    """Fresh adapters must rebind the original full digest before publishing bytes."""
    fixture = OwnedDownload(*repo, tmp_path)
    fixture.properties['has_metadata'] = False
    fixture.lose_ack = lost_ack
    result = fixture.run()
    assert result.state == ('failed' if lost_ack else 'downloading')
    assert len(fixture.adds) == 1 and fixture.files == []
    before = fixture.row()
    if lost_ack:
        assert before['external_id'] is None
        fixture.repo.retry(1, fixture.job.id)
    else:
        fixture.now[0] += 61
    fixture.properties = dict(hash=ID, has_metadata=True, infohash_v2=ID + '0'*24)
    assert fixture.run().error_code == 'client_job_mismatch'
    assert len(fixture.adds) == 1 and fixture.files == []
    assert fixture.row()['submission_key'] == before['submission_key']
    fixture.repo.retry(1, fixture.job.id)
    fixture.properties['infohash_v2'] = FULL
    assert fixture.run().state == 'importing'
    assert len(fixture.adds) == 1
    assert next((tmp_path/'ingest').glob('*.pdf')).read_bytes() == fixture.book.read_bytes()
    assert fixture.book.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize('adapter, engine', [
    ('qbittorrent', '1.2.19.0'), ('qbittorrent', ''),
    ('qbittorrent', 'unknown'), ('transmission', '2.0.11.0'),
])
def test_unqualified_single_btmh_refusal_has_no_durable_or_submission_effect(repo, tmp_path, adapter, engine):
    """Safe compatibility retry requires absent start, key and external identity."""
    fixture = OwnedDownload(*repo, tmp_path, adapter=adapter)
    fixture.engine = engine
    result = fixture.run()
    assert result.error_code == 'unsupported_client_version'
    row = fixture.row()
    assert all(row[key] is None for key in ('submission_started', 'submission_key', 'external_id'))
    assert fixture.adds == [] and fixture.files == []
    if adapter == 'qbittorrent':
        fixture.repo.retry(1, fixture.job.id)
        fixture.engine = '2.0.11.0'
        fixture.properties['has_metadata'] = False
        assert fixture.run().state == 'downloading'
        assert len(fixture.adds) == 1


def test_http_redirect_to_v2_magnet_without_durable_topic_binding_refuses_before_fence(repo, tmp_path):
    """A mutable HTTP source must not authorize a digest only held in adapter RAM."""
    fixture = OwnedDownload(*repo, tmp_path, href='https://source.example/redirect.torrent')
    assert fixture.run().error_code == 'unsupported_magnet_redirect'
    assert fixture.adds == []
    assert all(fixture.row()[key] is None for key in ('submission_started', 'submission_key', 'external_id'))


def test_single_topic_admission_keeps_multihash_and_network_authority_bounds():
    """Relaxing topic or tracker authority must admit at least one unsafe input."""
    torrent = importlib.import_module(spec.name + '.torrent')
    invalid = [
        'magnet:?xt=urn:btmh:' + tag + digest
        for tag, digest in [('1120', FULL), ('1221', FULL), ('1220', FULL[:-1]),
                            ('1220', FULL + '0'), ('1220', 'g'*64)]
    ] + [TRACKERLESS + suffix for suffix in (
        '&xt=urn:btmh:1220' + FULL, '&xt=urn:btih:' + 'a'*40,
        '&x.pe=127.0.0.1:51413', '&xs=https://source.example/file',
        '&dn=' + 'x'*8192, '&dn=x'*32,
    )]
    for uri in invalid:
        with pytest.raises(torrent.TransportError, match='invalid_magnet'):
            torrent.validate_magnet(uri, tracker_origins=[TRACKER])
    with pytest.raises(torrent.TransportError, match='untrusted_torrent_tracker'):
        torrent.validate_magnet(ORIGINAL, tracker_origins=[])
    with pytest.raises(torrent.TransportError, match='untrusted_torrent_tracker'):
        torrent.validate_magnet(TRACKERLESS + '&tr=https%3A%2F%2Ftracker.example%2Fannounce%3Fkey%3DPRIVATE',
            tracker_origins=['https://tracker.example'], secret='PRIVATE')
    assert torrent.validate_magnet('magnet:?xt=urn:btih:' + 'a'*40) == 'a'*40


def test_torznab_discovery_retains_exact_original_uri_only_in_private_offer(repo, tmp_path):
    """Source discovery must expose an opaque offer while retaining URI authority."""
    import json
    from xml.sax.saxutils import quoteattr
    repository, _ = repo
    indexer = importlib.import_module(spec.name + '.newznab')
    http = importlib.import_module(spec.name + '.http')
    client = repository.create_connection('Client', 'qbittorrent', config(tmp_path, 'qbittorrent'), enabled=True)
    cfg = indexer.connection_config(dict(endpoint='https://source.example/api', secret='PRIVATE',
        category='7020', client_id=client.id, tracker_origins=[TRACKER]))
    source = repository.create_connection('Source', 'torznab', cfg, enabled=True)
    responses = [
        b'<caps><searching><search available="yes" supportedParams="q"/></searching>'
        b'<categories><category id="7000"><subcat id="7020"/></category></categories></caps>',
        ('<rss><channel><item><title>Original legal book</title><guid>original</guid>'
         '<enclosure type="application/x-bittorrent" url=' + quoteattr(ORIGINAL)
         + '/></item></channel></rss>').encode(),
    ]
    def transfer(url, policy, **kw):
        return http.FetchedDocument(responses.pop(0), url, 'text/xml')
    public = indexer.IndexerService(repository, transfer=transfer).browse(1, source.id, query='original')
    offers = public['publications'][0]['offers']
    assert len(offers) == 1, public['publications'][0]
    payload = repository.offer_payload(1, offers[0]['offer_id'], source.id).offer
    assert payload['href'] == ORIGINAL
    assert FULL not in json.dumps(public) and 'magnet:' not in json.dumps(public)


def test_shared_attempt_adopter_rebinds_original_topic_without_another_add(repo, tmp_path):
    """A fresh subscriber uses one remote attempt and its own private receipt path."""
    fixture = OwnedDownload(*repo, tmp_path)
    fixture.properties['has_metadata'] = False
    assert fixture.run().state == 'downloading'
    row = fixture.row()
    second_offer = fixture.repo.create_offer(2, row['connection_id'], fixture.payload)
    second = fixture.repo.create_job(2, second_offer, 'request', requires_approval=False)
    fixture.properties['infohash_v2'] = ID + '0'*24
    result = fixture.run()
    assert result.id == second.id and result.error_code == 'client_job_mismatch'
    with fixture.repo.engine.connect() as conn:
        adopted = conn.execute(fixture.repo.tables.jobs.select().where(
            fixture.repo.tables.jobs.c.id == second.id)).mappings().one()
    assert adopted['submission_key'] == row['submission_key']
    assert adopted['submission_started'] == row['submission_started']
    assert adopted['submission_invalid'] is not True
    assert len(fixture.adds) == 1 and fixture.files == []
    fixture.repo.retry(2, second.id)
    fixture.properties.update(has_metadata=True, infohash_v2=FULL)
    assert fixture.run().state == 'importing'
    assert len(fixture.adds) == 1


@pytest.mark.parametrize('api', ['2.8.19', '2.9.3', '2.11.1', '2.15.2', 'broken'])
def test_old_metadata_api_refuses_without_fence_then_upgrade_submits_once(repo, tmp_path, api):
    fixture = OwnedDownload(*repo, tmp_path)
    fixture.api = api
    assert fixture.run().error_code == 'unsupported_client_version'
    assert fixture.adds == [] and fixture.files == []
    assert all(fixture.row()[key] is None for key in
               ('submission_started', 'submission_key', 'external_id'))
    fixture.repo.retry(1, fixture.job.id)
    fixture.api = '2.11.2'
    assert fixture.run().state == 'importing'
    assert len(fixture.adds) == 1
    assert fixture.adds[0]['urls'] == ORIGINAL
    assert next((tmp_path/'ingest').glob('*.pdf')).read_bytes() == fixture.book.read_bytes()


@pytest.mark.parametrize('change', ['engine', 'api'])
def test_client_compatibility_change_after_collision_check_refuses_before_fence(repo, tmp_path, change):
    fixture = OwnedDownload(*repo, tmp_path)
    original_transfer = fixture.transfer
    def transfer(url, policy, **kw):
        result = original_transfer(url, policy, **kw)
        if urlsplit(url).path.endswith('/torrents/info'):
            if change == 'engine':
                fixture.engine = '1.2.19.0'
            else:
                fixture.api = '2.9.3'
        return result
    fixture.transfer = transfer
    assert fixture.run().error_code == 'unsupported_client_version'
    assert fixture.adds == [] and fixture.files == []
    assert all(fixture.row()[key] is None for key in
               ('submission_started', 'submission_key', 'external_id'))
