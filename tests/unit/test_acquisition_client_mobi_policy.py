"""Held actual admission/discovery policy oracle; no public service contacted."""
from types import SimpleNamespace
import pytest
from sqlalchemy import text
from tests.unit.test_acquisition_api import api

@pytest.mark.parametrize('adapter',['sabnzbd','nzbget','qbittorrent','transmission'])
@pytest.mark.parametrize('opt_in',[False,True])
def test_only_current_bound_client_mobi_optin_admits_descriptor_under_mobi_only_global_policy(api,tmp_path,adapter,opt_in):
    client,repo,actor,module,_,_,_=api
    cfg={'endpoint':'https://client.example.invalid/api','auth_kind':'none' if adapter=='sabnzbd' else 'basic','secret':'FIXTURE_SECRET','category':'books','remote_path':'/downloads','local_path':str(tmp_path),'allow_mobi':opt_in}
    if adapter!='sabnzbd':cfg['username']='fixture'
    download=repo.create_connection('Owned client',adapter,module.CONFIGURATORS[adapter](cfg),enabled=True)
    source_cfg=module.CONFIGURATORS['newznab']({'endpoint':'https://indexer.example.invalid/api','secret':'FIXTURE_INDEXER','category':'7020','client_id':download.id})
    source=repo.create_connection('Source','newznab',source_cfg,enabled=True)
    torrent=adapter in ('qbittorrent','transmission')
    payload={'kind':'acquisition','transport':'torrent' if torrent else 'nzb','href':'https://indexer.example.invalid/descriptor','media_type':'application/x-bittorrent' if torrent else 'application/x-nzb','client_id':download.id,'client_revision':download.revision,'release_key':'a'*64}
    offer=repo.create_offer(2,source.id,payload)
    with repo.engine.begin() as con:con.execute(text("UPDATE settings SET config_upload_formats='mobi'"))
    response=client.post('/api/v1/acquisition/jobs',json={'connection_id':source.id,'offer_id':offer,'idempotency_key':'mobi-only'})
    assert response.status_code==(202 if opt_in else 400),response.get_data(as_text=True)
    assert bool(repo.list_jobs(2)) is opt_in

@pytest.mark.parametrize('adapter',['sabnzbd','nzbget','qbittorrent','transmission'])
@pytest.mark.parametrize('opt_in',[False,True])
def test_catalog_mobi_only_global_policy_exposes_only_descriptor_supported_by_bound_client(api,tmp_path,monkeypatch,adapter,opt_in):
    client,repo,actor,module,_,_,_=api
    config={'endpoint':'https://client.example.invalid/api','auth_kind':'none' if adapter=='sabnzbd' else 'basic','secret':'FIXTURE_SECRET','category':'books','remote_path':'/downloads','local_path':str(tmp_path),'allow_mobi':opt_in}
    if adapter!='sabnzbd':config['username']='fixture'
    download=repo.create_connection('Owned client',adapter,module.CONFIGURATORS[adapter](config),enabled=True)
    source=repo.create_connection('Source','newznab',module.CONFIGURATORS['newznab']({'endpoint':'https://indexer.example.invalid/api','secret':'FIXTURE_INDEXER','category':'7020','client_id':download.id}),enabled=True)
    torrent=adapter in ('qbittorrent','transmission');label='Torrent' if torrent else 'NZB'
    monkeypatch.setattr(module,'IndexerService',lambda *a,**k:SimpleNamespace(browse=lambda *a,**k:{'publications':[{'offers':[{'format':label,'offer_id':'owned'}]}]}))
    monkeypatch.setattr(module,'_run_private_blocking',lambda operation:operation())
    with repo.engine.begin() as con:con.execute(text("UPDATE settings SET config_upload_formats='mobi'"))
    response=client.get('/api/v1/acquisition/catalog',query_string={'connection':source.id})
    assert response.status_code==200,response.get_data(as_text=True)
    assert response.json['publications'][0]['offers']==([{'format':label,'offer_id':'owned'}] if opt_in else [])


@pytest.mark.parametrize('policy', ['enabled', 'client_default_off', 'global_off', 'client_disabled', 'client_revision'])
def test_real_mixed_bundle_choice_requires_current_mobi_client_and_global_caps(api, tmp_path, policy):
    client, repo, actor, module, source, _offer, _database = api
    config = module.CONFIGURATORS['sabnzbd']({'endpoint':'https://client.example.invalid/api',
        'secret':'FIXTURE', 'category':'books', 'remote_path':'/downloads',
        'local_path':str(tmp_path), 'allow_mobi':policy != 'client_default_off'})
    download = repo.create_connection('Owned client', 'sabnzbd', config, enabled=True)
    payload = {'kind':'acquisition', 'transport':'nzb', 'media_type':'application/x-nzb',
        'client_id':download.id, 'client_revision':download.revision, 'release_key':'a'*64}
    job = repo.create_job(2, repo.create_offer(2, source.id, payload), 'mixed', requires_approval=False)
    claim = repo.claim()
    repo.advance(job.id, claim.token, 'queued', 'resolving')
    repo.advance(job.id, claim.token, 'resolving', 'downloading')
    repo.begin_submission(job.id, claim.token)
    repo.record_external(job.id, claim.token, 'owned')
    repo.await_choices(job.id, claim.token, {'generation':'mixed1', 'candidates':[
        dict(id='1'*64, name='Original.mobi', relative_path='private/Original.mobi',
            media_type='application/x-mobipocket-ebook', size=10, sha256='1'*64),
        dict(id='2'*64, name='Reader.epub', relative_path='private/Reader.epub',
            media_type='application/epub+zip', size=10, sha256='2'*64)]})
    with repo.engine.begin() as conn:
        conn.execute(text("UPDATE settings SET config_upload_formats='epub,pdf,mobi'"))
        if policy == 'global_off': conn.execute(text("UPDATE settings SET config_upload_formats='epub,pdf'"))
        if policy == 'client_disabled': conn.execute(repo.tables.connections.update().where(
            repo.tables.connections.c.id == download.id).values(enabled=False))
        if policy == 'client_revision': conn.execute(repo.tables.connections.update().where(
            repo.tables.connections.c.id == download.id).values(revision=download.revision+1))
    listed = client.get(f'/api/v1/acquisition/jobs/{job.id}/books')
    if policy not in ('client_disabled', 'client_revision'):
        assert listed.status_code == 200, listed.text
        assert [c['format'] for c in listed.json['candidates']] == ['MOBI', 'EPUB']
        assert 'private/' not in listed.text and 'sha256' not in listed.text
    result = client.post(f'/api/v1/acquisition/jobs/{job.id}/books',
        json={'generation':'mixed1', 'candidate_id':'1'*64})
    expected = 202 if policy == 'enabled' else 409 if policy in ('client_disabled','client_revision') else 400
    assert result.status_code == expected, result.text
    assert repo.get_job(2, job.id).state == ('awaiting_approval' if policy == 'enabled' else 'awaiting_selection')
