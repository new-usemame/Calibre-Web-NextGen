# SPDX-License-Identifier: GPL-3.0-or-later
"""Protocol and durable external-effect seams; no public services are contacted."""
import importlib
import importlib.util
from pathlib import Path
import sys

import pytest
from sqlalchemy import MetaData, create_engine

path = Path(__file__).resolve().parents[2] / 'cps/services/acquisition'
spec = importlib.util.spec_from_file_location('_usenet_tests', path / '__init__.py', submodule_search_locations=[str(path)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
s = importlib.import_module(spec.name + '.storage')
k = importlib.import_module(spec.name + '.secrets')

@pytest.fixture
def repo(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'app.db'))
    metadata = MetaData(); tables = s.define_tables(metadata); metadata.create_all(engine)
    now = [1000.0]
    repository = s.Repository(engine, tables, k.SecretBox(b'x' * 32), clock=lambda: now[0])
    yield repository, now
    engine.dispose()


def test_reject_is_terminal_and_only_pending_requests_can_be_rejected(repo):
    repository, _ = repo
    connection = repository.create_connection('Books', 'opds', {}, enabled=True)
    offer = repository.create_offer(1, connection.id, {'title': 'One book'})
    job = repository.create_job(1, offer, 'request')
    repository.reject(job.id, admin_actor=2)
    assert repository.get_job(1, job.id).state == 'rejected'
    assert repository.claim() is None
    with pytest.raises(s.Conflict):
        repository.approve(job.id, admin_actor=2)
    with pytest.raises(s.Conflict):
        repository.retry(1, job.id)


def test_edit_fences_old_selections_and_delete_preserves_request_history(repo):
    repository, _ = repo
    connection = repository.create_connection('Books', 'opds', {'secret': 'old'}, enabled=True)
    offer = repository.create_offer(1, connection.id, {'title': 'Book'})
    job = repository.create_job(1, offer, 'request')
    # A config edit with outstanding work must be refused, not strand the job.
    with pytest.raises(s.Conflict):
        repository.update_connection(connection.id, label='Renamed', config={'secret': 'new'})
    repository.reject(job.id, admin_actor=2)
    repository.update_connection(connection.id, label='Renamed', config={'secret': 'new'})
    assert repository.connection_config(connection.id, include_disabled=True).config == {'secret': 'new'}
    assert repository.list_connections(include_disabled=True)[0].revision == 2
    with pytest.raises(s.NotFound):
        repository.offer_payload(1, offer, connection.id)
    repository.delete_connection(connection.id)
    assert not repository.list_connections(include_disabled=True)
    assert repository.get_job(1, job.id).title == 'Book'


def test_caps_uses_advertised_search_and_books_category_without_guessing():
    n = importlib.import_module(spec.name + '.newznab')
    caps = b'<caps><server title="Local"/><searching><search available="yes" supportedParams="q"/><book-search available="no"/></searching><categories><category id="7000" name="Books"><subcat id="7020" name="EBook"/></category></categories></caps>'
    result = n.parse_caps(caps, '7020')
    assert result == {'title': 'Local', 'mode': 'search', 'category': '7020'}
    with pytest.raises(n.IndexerError, match='book_category_unavailable'):
        n.parse_caps(caps, '5000')
    with pytest.raises(n.IndexerError, match='needs_auth'):
        n.parse_caps(b'<error code="100" description="PRIVATE API KEY"/>', '7020')


def test_indexer_selections_are_opaque_and_torrents_cannot_be_sent_to_sab(repo):
    n = importlib.import_module(spec.name + '.newznab')
    repository, _ = repo
    client = repository.create_connection('Client', 'sabnzbd', {}, enabled=True)
    config = n.connection_config({'endpoint': 'https://indexer.example/api', 'secret': 'API_SECRET',
        'category': '7020', 'client_id': client.id})
    connection = repository.create_connection('Indexer', 'newznab', config, enabled=True)
    documents = [b'<caps><searching><search available="yes" supportedParams="q"/></searching><categories><category id="7000"><subcat id="7020"/></category></categories></caps>',
        b'<rss><channel><title>Books</title><item><title>Book EPUB</title><guid>same-release</guid><enclosure url="https://indexer.example/get?apikey=API_SECRET" type="application/x-nzb"/></item><item><title>Torrent</title><enclosure url="https://indexer.example/file.torrent" type="application/x-bittorrent"/></item></channel></rss>']
    h = importlib.import_module(spec.name + '.http')
    calls = []
    def transfer(url, policy, **kwargs):
        calls.append(url)
        return h.FetchedDocument(documents.pop(0), url, 'text/xml')
    presented = n.IndexerService(repository, transfer=transfer).browse(1, connection.id, query='book')
    import json
    assert 'API_SECRET' not in json.dumps(presented) and 'https://' not in json.dumps(presented)
    assert presented['publications'][0]['offers'][0]['format'] == 'NZB'
    assert not presented['publications'][1]['offers']
    assert presented['publications'][1]['unavailable_reason'] == 'torrent_client_required'
    payload = repository.offer_payload(1, presented['publications'][0]['offers'][0]['offer_id'], connection.id).offer
    assert payload['client_id'] == client.id and payload['client_revision'] == 1
    assert 'q=book' in calls[1] and 'cat=7020' in calls[1]


def test_completed_path_rejects_escape_symlinks_and_ambiguous_books(tmp_path):
    b = importlib.import_module(spec.name + '.sabnzbd')
    root = tmp_path / 'complete'; root.mkdir()
    job = root / 'owned'; job.mkdir()
    (job / 'book.pdf').write_bytes(b'%PDF-1.7\nfixture\n%%EOF\n')
    config = {'remote_path': '/downloads', 'local_path': str(root)}
    assert b.completed_book(config, '/downloads/owned')[0] == job / 'book.pdf'
    for storage in ('/downloads/../outside', '/other/owned', '/downloads'):
        with pytest.raises(b.ClientError):
            b.completed_book(config, storage)
    (job / 'second.pdf').write_bytes(b'%PDF-1.7\nsecond\n%%EOF\n')
    with pytest.raises(b.ClientError, match='multiple_books'):
        b.completed_book(config, '/downloads/owned')
    (job / 'second.pdf').unlink()
    (job / 'book.pdf').unlink()
    (job / 'book.pdf').symlink_to(tmp_path / 'outside.pdf')
    with pytest.raises(b.ClientError, match='unsafe_completed_path'):
        b.completed_book(config, '/downloads/owned')


def test_durable_submission_is_shared_but_request_history_is_private(repo):
    repository, _ = repo
    client = repository.create_connection('Client', 'sabnzbd', {}, enabled=True)
    indexer = repository.create_connection('Books', 'newznab', {}, enabled=True)
    payload = {'kind': 'acquisition', 'transport': 'nzb', 'client_id': client.id,
        'client_revision': 1, 'release_key': 'a' * 64, 'title': 'Book'}
    first = repository.create_job(1, repository.create_offer(1, indexer.id, payload), 'click', requires_approval=False)
    # A second click with a new offer/key resolves to the same per-account job.
    duplicate = repository.create_job(1, repository.create_offer(1, indexer.id, payload), 'second-click', requires_approval=False)
    assert duplicate.id == first.id
    second = repository.create_job(2, repository.create_offer(2, indexer.id, payload), 'click', requires_approval=False)
    claim = repository.claim(max_active=1)
    assert repository.begin_submission(claim.job.id, claim.token) is True
    repository.record_external(claim.job.id, claim.token, 'SABnzbd_nzo_owned')
    repository.advance(claim.job.id, claim.token, 'queued', 'failed', error_code='client_error')
    other = repository.claim(max_active=1)
    assert repository.begin_submission(other.job.id, other.token) is False
    assert repository.external_status(other.job.id, other.token)[0] == 'SABnzbd_nzo_owned'
    assert len(repository.list_jobs(1)) == 1 and len(repository.list_jobs(2)) == 1


def test_worker_resumes_sab_postprocessing_and_never_resubmits_after_lost_response(repo, tmp_path):
    n = importlib.import_module(spec.name + '.newznab')
    b = importlib.import_module(spec.name + '.sabnzbd')
    w = importlib.import_module(spec.name + '.worker')
    repository, now = repo
    complete = tmp_path / 'complete'; complete.mkdir()
    folder = complete / 'owned'; folder.mkdir()
    (folder / 'book.pdf').write_bytes(b'%PDF-1.7\nfixture\n%%EOF\n')
    client = repository.create_connection('Client', 'sabnzbd', b.connection_config({
        'endpoint': 'https://sab.example/api', 'secret': 'SAB_SECRET', 'category': 'books',
        'remote_path': '/downloads', 'local_path': str(complete)}), enabled=True)
    indexer = repository.create_connection('Indexer', 'newznab', n.connection_config({
        'endpoint': 'https://indexer.example/api', 'secret': 'SOURCE_SECRET', 'category': '7020', 'client_id': client.id}), enabled=True)
    offer = repository.create_offer(1, indexer.id, {'kind': 'acquisition', 'transport': 'nzb',
        'href': 'https://indexer.example/nzb', 'media_type': 'application/x-nzb',
        'client_id': client.id, 'client_revision': 1, 'release_key': 'b' * 64})
    job = repository.create_job(1, offer, 'click', requires_approval=False)
    submitted = []; remote = [None]
    class Client:
        def __init__(self, config, **kwargs): pass
        def find(self, name, external_id=None, **kwargs): return remote[0]
        def submit(self, name, nzb, **kwargs):
            submitted.append(name)
            remote[0] = {'nzo_id': 'owned', 'status': 'Extracting', 'loaded': True}
            raise b.ClientError('source_unreachable')  # accepted, response lost
    h = importlib.import_module(spec.name + '.http')
    ingest = tmp_path / 'ingest'; ingest.mkdir()
    def transfer(url, policy, **kwargs):
        return h.FetchedDocument(b'<nzb><file/></nzb>', url, 'application/x-nzb')
    worker = w.AcquisitionWorker(repository, tmp_path / 'staging', ingest,
        allowed=lambda _: True, transfer=transfer, client_factory=Client)
    assert worker.run_once().state == 'failed'
    repository.retry(1, job.id)
    assert worker.run_once().state == 'downloading'
    assert not list(ingest.iterdir()) and len(submitted) == 1
    now[0] += 31
    remote[0] = {'nzo_id': 'owned', 'status': 'Completed', 'loaded': False, 'storage': '/downloads/owned'}
    restarted = w.AcquisitionWorker(repository, tmp_path / 'staging', ingest,
        allowed=lambda _: True, transfer=lambda *a, **k: pytest.fail('Restart fetched NZB again'), client_factory=Client)
    assert restarted.run_once().state == 'importing'
    assert len(submitted) == 1 and len(list(ingest.glob('*.pdf'))) == 1
    assert repository.get_receipt(1, job.id) is None  # completed download is not imported


def test_concurrent_config_edit_cannot_invalidate_accepted_request(repo):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import event
    from threading import Event
    repository, _ = repo
    connection = repository.create_connection('Books', 'opds', {'secret': 'old'}, enabled=True)
    offer = repository.create_offer(1, connection.id, {'title': 'Book'})
    attempted = Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = []
        def edit():
            attempted.set()
            return repository.update_connection(connection.id, label='Changed', config={'secret': 'new'})
        def at_insert(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith('INSERT INTO acquisition_job'):
                future.append(executor.submit(edit))
                assert attempted.wait(2)
                # Without the reservation the edit can commit before the job
                # insert. With it the edit waits, then sees the accepted job.
                from concurrent.futures import TimeoutError
                try:
                    future[-1].result(timeout=0.15)
                except TimeoutError:
                    pass
        event.listen(repository.engine, 'before_cursor_execute', at_insert)
        try:
            job = repository.create_job(1, offer, 'click', requires_approval=False)
        finally:
            event.remove(repository.engine, 'before_cursor_execute', at_insert)
        with pytest.raises(s.Conflict):
            future[0].result(timeout=5)
    claim = repository.claim()
    assert claim.job.id == job.id
    assert repository.material(job.id, claim.token).config['secret'] == 'old'


def test_stale_patch_cannot_restore_a_rotated_credential(repo):
    repository, _ = repo
    connection = repository.create_connection('Books', 'opds', {'secret': 'old'})
    repository.update_connection(connection.id, label='Books', config={'secret': 'new'}, expected_revision=1)
    with pytest.raises(s.Conflict):
        repository.update_connection(connection.id, label='Rename', config={'secret': 'old'}, expected_revision=1)
    assert repository.connection_config(connection.id, include_disabled=True).config['secret'] == 'new'


def test_completed_file_open_fences_parent_symlink_and_fifo_swap(tmp_path):
    import os
    b = importlib.import_module(spec.name + '.sabnzbd')
    root = tmp_path / 'completed'; root.mkdir()
    folder = root / 'owned'; folder.mkdir()
    file = folder / 'book.pdf'; file.write_bytes(b'%PDF-1.7\nfixture\n%%EOF\n')
    config = {'local_path': str(root), 'remote_path': '/downloads'}
    selected, _ = b.completed_book(config, '/downloads/owned')
    # Selection must never open a shared source; copy owns all byte reads.
    file.unlink(); os.mkfifo(file)
    with pytest.raises(b.ClientError, match='unsafe_completed_path'):
        b.open_completed_file(config, selected)
    file.unlink(); folder.rmdir()
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'book.pdf').write_bytes(b'private')
    folder.symlink_to(outside, target_is_directory=True)
    with pytest.raises(b.ClientError, match='unsafe_completed_path'):
        b.open_completed_file(config, selected)


def test_sab_history_storage_can_point_to_its_single_completed_file(tmp_path):
    b = importlib.import_module(spec.name + '.sabnzbd')
    root = tmp_path / 'complete'; root.mkdir()
    folder = root / 'owned'; folder.mkdir()
    book = folder / 'book.pdf'; book.write_bytes(b'%PDF-1.7\nfixture\n%%EOF\n')
    assert b.completed_book({'remote_path': '/downloads', 'local_path': str(root)}, '/downloads/owned/book.pdf')[0] == book


def test_search_response_after_connection_edit_cannot_create_current_selections(repo):
    n = importlib.import_module(spec.name + '.newznab')
    h = importlib.import_module(spec.name + '.http')
    repository, _ = repo
    client = repository.create_connection('Client', 'sabnzbd', {}, enabled=True)
    config = n.connection_config({'endpoint': 'https://old.example/api', 'secret': 'OLDKEY', 'category': '7020', 'client_id': client.id})
    connection = repository.create_connection('Indexer', 'newznab', config, enabled=True)
    def transfer(url, policy, **kwargs):
        if 't=caps' in url:
            body = b'<caps><searching><search available="yes" supportedParams="q"/></searching><categories><category id="7000"><subcat id="7020"/></category></categories></caps>'
        else:
            repository.update_connection(connection.id, label='Updated', config=dict(config, endpoint='https://new.example/api', secret='NEWKEY'))
            repository.set_connection_enabled(connection.id, True)
            body = b'<rss><channel><item><title>Book</title><enclosure url="https://old.example/nzb" type="application/x-nzb"/></item></channel></rss>'
        return h.FetchedDocument(body, url, 'text/xml')
    with pytest.raises(s.NotFound):
        n.IndexerService(repository, transfer=transfer).browse(1, connection.id, query='book')


def test_stale_enable_cannot_undo_disable_on_configuration_edit(repo):
    repository, _ = repo
    c = repository.create_connection('Books', 'opds', {'secret': 'old'}, enabled=True)
    repository.update_connection(c.id, label='New', config={'secret': 'new'})
    with pytest.raises(s.Conflict):
        repository.set_connection_enabled(c.id, True, expected_revision=1)
    assert repository.list_connections(include_disabled=True)[0].enabled is False


def test_indexer_next_page_keeps_search_private_and_uses_advertised_offset(repo):
    n = importlib.import_module(spec.name + '.newznab'); h = importlib.import_module(spec.name + '.http')
    repository, _ = repo
    client = repository.create_connection('Client', 'sabnzbd', {}, enabled=True)
    c = repository.create_connection('Indexer', 'newznab', n.connection_config({
        'endpoint': 'https://indexer.example/api', 'secret': 'KEY', 'category': '7020', 'client_id': client.id}), enabled=True)
    calls = []
    def transfer(url, policy, **kwargs):
        calls.append(url)
        body = b'<caps><searching><search available="yes" supportedParams="q"/></searching><categories><category id="7020"/></categories></caps>' if 't=caps' in url else b'<rss xmlns:n="http://www.newznab.com/DTD/2010/feeds/attributes/"><channel><n:response offset="0" total="60"/><item><title>Book</title></item></channel></rss>'
        return h.FetchedDocument(body, url, 'text/xml')
    service = n.IndexerService(repository, transfer=transfer)
    first = service.browse(1, c.id, query='private title')
    next_page = first['pagination'][0]
    assert 'private title' not in str(next_page) and 'https://' not in str(next_page)
    service.browse(1, c.id, selection=next_page['selection'])
    assert 'offset=1' in calls[-1] and 'q=private+title' in calls[-1]


@pytest.mark.parametrize('scenario, code', [
    ('missing', 'client_job_missing'), ('failed', 'client_job_failed'),
    ('empty', 'no_usable_book'), ('bad-key', 'needs_auth'), ('down', 'source_unreachable'),
])
def test_external_failure_never_publishes_or_submits_a_second_download(repo, tmp_path, scenario, code):
    n = importlib.import_module(spec.name + '.newznab'); b = importlib.import_module(spec.name + '.sabnzbd')
    w = importlib.import_module(spec.name + '.worker')
    repository, _ = repo
    complete = tmp_path / 'complete'; complete.mkdir(); (complete / 'owned').mkdir()
    client = repository.create_connection('Client', 'sabnzbd', b.connection_config({
        'endpoint': 'https://sab.example/api', 'secret': 'PRIVATE', 'category': 'books',
        'remote_path': '/downloads', 'local_path': str(complete)}), enabled=True)
    source = repository.create_connection('Indexer', 'newznab', n.connection_config({
        'endpoint': 'https://indexer.example/api', 'secret': 'PRIVATE', 'category': '7020', 'client_id': client.id}), enabled=True)
    offer = repository.create_offer(1, source.id, {'kind': 'acquisition', 'transport': 'nzb',
        'media_type': 'application/x-nzb', 'client_id': client.id, 'client_revision': 1, 'release_key': 'c'*64})
    job = repository.create_job(1, offer, 'click', requires_approval=False)
    claim = repository.claim(); repository.begin_submission(job.id, claim.token)
    repository.record_external(job.id, claim.token, 'owned'); repository.release(job.id, claim.token)
    class Client:
        def __init__(self, *args, **kwargs): pass
        def submit(self, *args, **kwargs): pytest.fail('A persisted submission was duplicated')
        def find(self, *args, **kwargs):
            if scenario in ('bad-key', 'down'): raise b.ClientError(code)
            if scenario == 'missing': return None
            return {'nzo_id': 'owned', 'status': 'Failed' if scenario == 'failed' else 'Completed',
                'loaded': False, 'storage': '/downloads/owned'}
    ingest = tmp_path / 'ingest'; ingest.mkdir()
    worker = w.AcquisitionWorker(repository, tmp_path / 'staging', ingest, allowed=lambda _: True,
        transfer=lambda *a, **k: pytest.fail('A persisted submission fetched its descriptor again'), client_factory=Client)
    result = worker.run_once()
    assert result.state == 'failed' and result.error_code == code
    assert not list(ingest.iterdir()) and repository.get_receipt(1, job.id) is None


def test_indexer_small_page_never_skips_releases(repo):
    n = importlib.import_module(spec.name + '.newznab'); h = importlib.import_module(spec.name + '.http')
    repository, _ = repo
    client = repository.create_connection('Client', 'sabnzbd', {}, enabled=True)
    c = repository.create_connection('Indexer', 'newznab', n.connection_config({
        'endpoint': 'https://indexer.example/api', 'secret': 'KEY', 'category': '7020', 'client_id': client.id}), enabled=True)
    def transfer(url, policy, **kwargs):
        body = b'<caps><limits max="25" default="25"/><searching><search available="yes" supportedParams="q"/></searching><categories><category id="7020"/></categories></caps>' if 't=caps' in url else b'<rss xmlns:n="http://www.newznab.com/DTD/2010/feeds/attributes/"><channel><n:response total="75"/>' + b'<item><title>Book</title></item>'*25 + b'</channel></rss>'
        return h.FetchedDocument(body, url, 'text/xml')
    page = n.IndexerService(repository, transfer=transfer).browse(1, c.id, query='book')
    payload = repository.offer_payload(1, page['pagination'][0]['selection'], c.id).offer
    assert payload['offset'] == 25


def test_definite_sab_rejection_can_retry_without_poisoning_the_submission_fence(repo, tmp_path):
    n = importlib.import_module(spec.name + '.newznab'); b = importlib.import_module(spec.name + '.sabnzbd')
    w = importlib.import_module(spec.name + '.worker'); h = importlib.import_module(spec.name + '.http')
    repository, _ = repo
    client = repository.create_connection('Client', 'sabnzbd', b.connection_config({
        'endpoint': 'https://sab.example/api', 'secret': 'PRIVATE', 'category': 'books',
        'remote_path': '/downloads', 'local_path': str(tmp_path)}), enabled=True)
    source = repository.create_connection('Indexer', 'newznab', n.connection_config({
        'endpoint': 'https://indexer.example/api', 'secret': 'PRIVATE', 'category': '7020', 'client_id': client.id}), enabled=True)
    offer = repository.create_offer(1, source.id, {'kind': 'acquisition', 'transport': 'nzb',
        'href': 'https://indexer.example/nzb', 'media_type': 'application/x-nzb',
        'client_id': client.id, 'client_revision': 1, 'release_key': 'd'*64})
    job = repository.create_job(1, offer, 'click', requires_approval=False)
    submissions = []
    class Client:
        def __init__(self, *args, **kwargs): pass
        def submit(self, name, *args, **kwargs):
            submissions.append(name)
            if len(submissions) == 1: raise b.ClientError('needs_auth')
            return 'owned'
        def find(self, *args, **kwargs): return {'nzo_id': 'owned', 'status': 'Downloading'}
    ingest = tmp_path / 'ingest'; ingest.mkdir()
    worker = w.AcquisitionWorker(repository, tmp_path / 'staging', ingest, allowed=lambda _: True,
        transfer=lambda url, *a, **k: h.FetchedDocument(b'<nzb><file/></nzb>', url, 'text/xml'), client_factory=Client)
    assert worker.run_once().error_code == 'needs_auth'
    claim = repository.claim()  # failed requests aren't dispatched automatically
    assert claim is None
    repository.retry(1, job.id)
    assert worker.run_once().state == 'downloading'
    assert len(submissions) == 2 and not list(ingest.iterdir())


@pytest.mark.parametrize('adapter', ['indexer', 'client'])
def test_protocol_probe_preserves_transport_auth_failure_without_private_details(repo, adapter):
    n = importlib.import_module(spec.name + '.newznab'); b = importlib.import_module(spec.name + '.sabnzbd')
    h = importlib.import_module(spec.name + '.http'); repository, _ = repo
    def transfer(*args, **kwargs): raise h.TransportError('needs_auth')
    config = {'endpoint': 'https://service.example/api', 'secret': 'PRIVATE', 'auth_kind': 'none',
        'username': '', 'credential_origins': [], 'private_origins': [], 'private_networks': []}
    error = n.IndexerError if adapter == 'indexer' else b.ClientError
    with pytest.raises(error, match='^needs_auth$'):
        if adapter == 'indexer': n.IndexerService(repository, transfer=transfer)._document(config, t='caps')
        else: b.SABClient(config, transfer=transfer).call('get_cats')


def test_another_account_reuses_persisted_sab_job_without_fetching_descriptor_again(repo, tmp_path):
    n = importlib.import_module(spec.name + '.newznab'); b = importlib.import_module(spec.name + '.sabnzbd')
    w = importlib.import_module(spec.name + '.worker'); repository, _ = repo
    client = repository.create_connection('Client', 'sabnzbd', b.connection_config({
        'endpoint':'https://sab.example/api','secret':'PRIVATE','category':'books',
        'remote_path':'/downloads','local_path':str(tmp_path)}), enabled=True)
    source = repository.create_connection('Indexer','newznab',n.connection_config({
        'endpoint':'https://indexer.example/api','secret':'PRIVATE','category':'7020','client_id':client.id}), enabled=True)
    payload={'kind':'acquisition','transport':'nzb','media_type':'application/x-nzb','href':'https://indexer.example/nzb',
        'client_id':client.id,'client_revision':1,'release_key':'e'*64}
    first=repository.create_job(1,repository.create_offer(1,source.id,payload),'first',requires_approval=False)
    claim=repository.claim();repository.begin_submission(first.id,claim.token);repository.record_external(first.id,claim.token,'owned')
    repository.advance(first.id,claim.token,'queued','failed',error_code='client_error')
    second=repository.create_job(2,repository.create_offer(2,source.id,payload),'second',requires_approval=False)
    class Client:
        def __init__(self,*args,**kwargs): pass
        def submit(self,*args,**kwargs): pytest.fail('Shared download submitted again')
        def find(self,name,external_id=None,**kwargs):
            assert external_id=='owned'
            return {'nzo_id':'owned','status':'Downloading'}
    worker=w.AcquisitionWorker(repository,tmp_path/'staging',tmp_path,allowed=lambda _:True,
        client_factory=Client,transfer=lambda *a,**k:pytest.fail('Shared descriptor fetched again'))
    assert worker.run_once().id==second.id
    assert repository.get_job(2,second.id).state=='downloading'
