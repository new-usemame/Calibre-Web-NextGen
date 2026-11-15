# SPDX-License-Identifier: GPL-3.0-or-later
"""Current policy and identity share the new capability's write reservation."""
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.unit.test_acquisition_api import api


@pytest.mark.parametrize('revoke', ['account', 'global_format', 'client_disabled',
                                   'client_revision', 'source_disabled', 'source_revision'])
def test_revocation_before_permit_transaction_cannot_issue_or_publish_client_book(api, tmp_path, monkeypatch, revoke):
    """An admin/settings write after checkpoint must precede the capability check."""
    from cps import constants
    from cps.services.acquisition import admission, newznab, sabnzbd
    from cps.services.acquisition.worker import AcquisitionWorker
    _client, repo, _actor, _module, _source, _offer, _database = api
    with repo.engine.begin() as conn:
        conn.execute(text('UPDATE user SET role=:role WHERE id=2'), {
            'role': constants.ROLE_ACQUISITION_ACCESS | constants.ROLE_ACQUISITION_AUTO_APPROVE})
        conn.execute(text("UPDATE settings SET config_upload_formats='epub,pdf,mobi'"))
    download = repo.create_connection('Completed books', 'sabnzbd', sabnzbd.connection_config({
        'endpoint':'https://client.example.invalid/api', 'secret':'FIXTURE',
        'category':'books', 'remote_path':'/downloads', 'local_path':str(tmp_path),
        'allow_mobi':True}), enabled=True)
    source = repo.create_connection('Source', 'newznab', newznab.connection_config({
        'endpoint':'https://indexer.example.invalid/api', 'secret':'FIXTURE',
        'category':'7020', 'client_id':download.id}), enabled=True)
    offer = repo.create_offer(2, source.id, {'kind':'acquisition', 'transport':'nzb',
        'href':'https://indexer.example.invalid/descriptor', 'media_type':'application/x-nzb',
        'client_id':download.id, 'client_revision':download.revision, 'release_key':'b'*64})
    job = repo.create_job(2, offer, 'permit-boundary', requires_approval=False)
    claim = repo.claim()
    ingest = tmp_path/'ingest'; ingest.mkdir()
    worker = AcquisitionWorker(repo, tmp_path/'staging', ingest,
        allowed=lambda owner: admission.account_allowed(repo.engine, owner),
        enabled=lambda: admission.instance_enabled(repo.engine),
        execution_allowed=lambda job: admission.job_allowed(repo.engine, job.id, job.owner_id),
        media_allowed=lambda media: admission.format_allowed(repo.engine, media),
        transfer=lambda *args, **kwargs: pytest.fail('A staged recovery must not transfer'))
    private = worker._directory(job.id, claim.token)
    data = (Path(__file__).resolve().parents[1]/'fixtures/sample_books/test_original_direct.mobi').read_bytes()
    (private/'source.part').write_bytes(data)
    repo.advance(job.id, claim.token, 'queued', 'resolving')
    repo.advance(job.id, claim.token, 'resolving', 'downloading')
    repo.advance(job.id, claim.token, 'downloading', 'staged',
        source_sha256=hashlib.sha256(data).hexdigest(), staging_key=claim.token)
    with repo.engine.begin() as conn:
        conn.execute(repo.tables.jobs.update().where(repo.tables.jobs.c.id==job.id).values(lease_expires=0))
    prepare = repo.prepare_publication
    def revoke_then_prepare(*args, **kwargs):
        with repo.engine.begin() as conn:
            if revoke == 'account': conn.execute(text('UPDATE user SET role=0 WHERE id=2'))
            elif revoke == 'global_format': conn.execute(text("UPDATE settings SET config_upload_formats='epub,pdf'"))
            else:
                connection = download if revoke.startswith('client_') else source
                changes = {'enabled':False} if revoke.endswith('_disabled') else {'revision':connection.revision+1}
                # Raw revision movement simulates a race; supported config edits
                # already refuse a connection with nonterminal jobs.
                conn.execute(repo.tables.connections.update().where(
                    repo.tables.connections.c.id==connection.id).values(**changes))
        return prepare(*args, **kwargs)
    monkeypatch.setattr(repo, 'prepare_publication', revoke_then_prepare)
    result = worker.run_once()
    assert result.state in ('failed', 'staged'), result
    assert not list(ingest.iterdir())
    with repo.engine.connect() as conn:
        assert conn.execute(repo.tables.jobs.select().where(
            repo.tables.jobs.c.id==job.id)).mappings().one()['publication_proof_hash'] is None
