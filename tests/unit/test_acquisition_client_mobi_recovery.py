"""Staged client MOBI recovery preserves actual format before normal publication."""
import hashlib,importlib
import pytest
from pathlib import Path
from tests.unit.test_acquisition_usenet import repo,spec


@pytest.mark.parametrize('source_kind,policy', [
    (kind, policy) for kind in ('mobi', 'mobi_pdf_name', 'pdf_mobi_marker')
    for policy in ('enabled', 'client_off', 'global_off')
] + [('invalid_mobi', 'enabled')])
def test_recovered_staged_client_mobi_keeps_mobi_handoff_and_rechecks_caps_without_redownload(repo,tmp_path,policy,source_kind):
    repository,now=repo
    b=importlib.import_module(spec.name+'.sabnzbd');n=importlib.import_module(spec.name+'.newznab');w=importlib.import_module(spec.name+'.worker')
    client=repository.create_connection('Client','sabnzbd',b.connection_config({'endpoint':'https://client.example.invalid/api','secret':'FIXTURE','category':'books','remote_path':'/downloads','local_path':str(tmp_path),'allow_mobi':policy != 'client_off'}),enabled=True)
    source=repository.create_connection('Source','newznab',n.connection_config({'endpoint':'https://indexer.example.invalid/api','secret':'FIXTURE','category':'7020','client_id':client.id}),enabled=True)
    payload={'kind':'acquisition','transport':'nzb','href':'https://indexer.example.invalid/descriptor','media_type':'application/x-nzb','client_id':client.id,'client_revision':client.revision,'release_key':'a'*64}
    job=repository.create_job(1,repository.create_offer(1,source.id,payload),'staged',requires_approval=False)
    ingest=tmp_path/'ingest';ingest.mkdir();worker=w.AcquisitionWorker(repository,tmp_path/'staging',ingest,allowed=lambda _:True,transfer=lambda *a,**k:(_ for _ in ()).throw(AssertionError('recovery downloaded again')))
    claim=repository.claim();assert claim.job.id==job.id
    private=worker._directory(job.id,claim.token)
    original=(Path(__file__).resolve().parents[1]/'fixtures/sample_books/test_original_direct.mobi').read_bytes()
    if source_kind == 'mobi_pdf_name':
        original = b'%PDF-1.7'.ljust(32, b'\0') + original[32:]
    elif source_kind == 'pdf_mobi_marker':
        original = b'%PDF-1.7\n'.ljust(60, b' ') + b'BOOKMOBI\n%%EOF'
    elif source_kind == 'invalid_mobi':
        original = bytes(60) + b'BOOKMOBI'
    (private/'source.part').write_bytes(original)
    repository.advance(job.id,claim.token,'queued','resolving');repository.advance(job.id,claim.token,'resolving','downloading');repository.advance(job.id,claim.token,'downloading','staged',source_sha256=hashlib.sha256(original).hexdigest(),staging_key=claim.token)
    worker.media_allowed = lambda media: policy != 'global_off'
    now[0]+=61
    result=worker.run_once()
    if source_kind == 'invalid_mobi':
        assert result.state == 'failed' and result.error_code == 'invalid_mobi', result
        assert not list(ingest.iterdir())
        return
    if policy == 'global_off' or (policy == 'client_off' and source_kind != 'pdf_mobi_marker'):
        assert result.state == 'failed' and result.error_code == 'format_not_allowed', result
        assert not list(ingest.iterdir())
        return
    assert result.state=='importing',result
    extension = 'pdf' if source_kind == 'pdf_mobi_marker' else 'mobi'
    published=list(ingest.glob('cwng-acquisition-*.'+extension))
    assert len(published)==1 and published[0].read_bytes()==original
    assert not list(ingest.glob('*.epub'))
