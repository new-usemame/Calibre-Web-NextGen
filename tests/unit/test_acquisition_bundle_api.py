# SPDX-License-Identifier: GPL-3.0-or-later
"""Actual choice routes enforce current owner, grants, approval and formats."""
import pytest
from sqlalchemy import text
from tests.unit.test_acquisition_api import api


def bundle(api, *, approved=False):
    client, repo, actor, module, connection, offer, database = api
    download = repo.create_connection('Download client', 'sabnzbd', {}, enabled=True)
    payload = {'kind':'acquisition','transport':'nzb','media_type':'application/x-nzb',
        'client_id':download.id,'client_revision':1,'release_key':'a'*64}
    job = repo.create_job(2, repo.create_offer(2,connection.id,payload),'bundle', requires_approval=approved)
    if approved: repo.approve(job.id,admin_actor=1)
    claim = repo.claim()
    repo.advance(job.id, claim.token,'queued','resolving')
    repo.advance(job.id, claim.token,'resolving','downloading')
    repo.begin_submission(job.id, claim.token)
    repo.record_external(job.id, claim.token,'owned')
    repo.await_choices(job.id,claim.token,{'generation':'generation1','candidates':[
        dict(id='1'*64,name='English.epub',relative_path='private/English.epub',media_type='application/epub+zip',size=10,sha256='1'*64),
        dict(id='2'*64,name='German.pdf',relative_path='private/German.pdf',media_type='application/pdf',size=10,sha256='2'*64)]})
    return job


def choose(client, job, candidate='1'*64):
    return client.post(f'/api/v1/acquisition/jobs/{job.id}/books', json={'generation':'generation1','candidate_id':candidate})


def test_real_bundle_routes_keep_names_private_and_recheck_approval(api):
    client, repo, actor, module, connection, offer, database = api
    job = bundle(api, approved=True)
    response = client.get(f'/api/v1/acquisition/jobs/{job.id}/books')
    assert response.status_code==200
    assert [c['name'] for c in response.json['candidates']]==['English.epub','German.pdf']
    assert 'private/' not in response.text and 'sha256' not in response.text
    first = choose(client,job)
    assert first.status_code==202 and first.json['state']=='queued' and first.json['id']==job.id
    second = choose(client,job,'2'*64)
    assert second.status_code==202 and second.json['state']=='awaiting_approval' and second.json['id']!=job.id
    assert choose(client,job,'2'*64).json['id']==second.json['id']
    actor.id=3
    assert client.get(f'/api/v1/acquisition/jobs/{job.id}/books').status_code==404
    assert choose(client,job).status_code==404


@pytest.mark.parametrize('change', ['revoked','off','stale','format','paused'])
def test_bundle_selection_current_policy(api,change,monkeypatch):
    client, repo, actor, module, connection, offer, database = api
    job = bundle(api)
    with repo.engine.begin() as conn:
        if change=='revoked': conn.execute(text('UPDATE user SET role=0 WHERE id=2'))
        if change=='off': conn.execute(text('UPDATE settings SET config_acquisition_enabled=0'))
        if change=='format': conn.execute(text("UPDATE settings SET config_upload_formats='pdf'"))
    if change=='paused': monkeypatch.setattr(module,'worker_available',lambda:{'available':False,'reasons':['paused']})
    if change=='stale': response=client.post(f'/api/v1/acquisition/jobs/{job.id}/books',json={'generation':'old','candidate_id':'1'*64})
    else: response=choose(client,job)
    expected={'revoked':404,'off':404,'stale':409,'format':400,'paused':503}
    assert response.status_code==expected[change]
    assert repo.get_job(2,job.id).state=='awaiting_selection'
