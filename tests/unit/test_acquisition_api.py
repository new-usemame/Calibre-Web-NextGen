# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Flask routing and SQLite owner/admission boundaries, with no remote fetch."""
import sqlite3
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import MetaData, create_engine, text


@pytest.fixture
def api(tmp_path,monkeypatch,request):
    from cps import constants, ub
    from cps.api import api_v1
    import cps.api as blueprint
    from cps.api import acquisition as module
    from cps.services.acquisition.storage import Repository,define_tables
    from cps.services.acquisition.secrets import SecretBox,load_or_create_key
    database=tmp_path/'app.db'; engine=create_engine('sqlite:///'+str(database))
    tables=define_tables(MetaData()); tables.jobs.metadata.create_all(engine)
    access=constants.ROLE_ACQUISITION_ACCESS
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE user (id INTEGER PRIMARY KEY,name TEXT,role INTEGER)'))
        connection.execute(text('INSERT INTO user VALUES (1,\'Admin\',:admin),(2,\'Reader\',:access),(3,\'Other\',:access)'),{'admin':constants.ROLE_ADMIN|access,'access':access})
        connection.execute(text('CREATE TABLE settings (config_acquisition_enabled INTEGER,config_upload_formats TEXT)'))
        connection.execute(text("INSERT INTO settings VALUES (1,'epub,pdf')"))
        connection.execute(text('CREATE TABLE acquisition_schema_migration (version INTEGER,status TEXT)'))
        connection.execute(text("INSERT INTO acquisition_schema_migration VALUES (1,'preserved')"))
    box=SecretBox(load_or_create_key(tmp_path/'acquisition.key'))
    repo=Repository(engine,tables,box)
    connection=repo.create_connection('Test catalog','opds',{'endpoint':'https://catalog.invalid','auth_kind':'none','secret':'','username':'','credential_origins':[],'private_origins':[],'private_networks':[]},enabled=True)
    offer=repo.create_offer(2,connection.id,{'kind':'acquisition','href':'https://catalog.invalid/book.epub?token=PRIVATE','media_type':'application/epub+zip','title':'Known book'})
    actor=SimpleNamespace(id=2,is_authenticated=True,is_anonymous=False)
    monkeypatch.setattr(module,'current_user',actor);monkeypatch.setattr(blueprint,'current_user',actor)
    monkeypatch.setattr(ub,'app_DB_path',str(database))
    monkeypatch.setattr(blueprint,'config',SimpleNamespace(config_allow_reverse_proxy_header_login=False,config_anonbrowse=0))
    monkeypatch.setattr(module,'worker_available',lambda:{'available':True,'reasons':[]})
    # Rate limits are off unless a test asks for them: they are a shared
    # module-level singleton, so leaving them on would couple tests together.
    app=Flask(__name__);app.config.update(SECRET_KEY='fixture-secret',
        RATELIMIT_ENABLED=getattr(request,'param',False),WTF_CSRF_ENABLED=False)
    if module.limiter is not None: module.limiter.init_app(app)
    app.register_blueprint(api_v1)
    yield app.test_client(),repo,actor,module,connection,offer,database
    engine.dispose()


def post_job(api,**overrides):
    client,repo,actor,module,connection,offer,database=api
    body=dict(connection_id=connection.id,offer_id=offer,idempotency_key='click',add_to_my_library=True)
    body.update(overrides)
    return client.post('/api/v1/acquisition/jobs',json=body)


def test_real_routes_request_approval_and_do_not_leak_private_offer(api):
    client,repo,actor,module,connection,offer,database=api
    response=post_job(api)
    assert response.status_code==202
    payload=response.get_json(); assert payload['state']=='awaiting_approval'
    assert 'PRIVATE' not in response.get_data(as_text=True) and 'offer_id' not in payload
    assert client.get('/api/v1/acquisition/jobs').get_json()['jobs'][0]['id']==payload['id']
    assert post_job(api).get_json()['id']==payload['id']


@pytest.mark.parametrize('change',['off','revoked','deleted','anonymous','hybrid'])
def test_admission_rechecks_current_account_and_instance(api,change):
    client,repo,actor,module,connection,offer,database=api
    with repo.engine.begin() as con:
        if change=='off':con.execute(text('UPDATE settings SET config_acquisition_enabled=0'))
        if change=='revoked':con.execute(text('UPDATE user SET role=0 WHERE id=2'))
        if change=='deleted':con.execute(text('DELETE FROM user WHERE id=2'))
        if change=='hybrid':con.execute(text("UPDATE acquisition_schema_migration SET status='needs_review'"))
    if change=='anonymous': actor.is_authenticated=False
    assert post_job(api).status_code==(401 if change=='anonymous' else 404)
    assert repo.list_jobs(2)==()


@pytest.mark.parametrize('action',['status','cancel','retry','offer'])
def test_account_partition_for_jobs_and_selections(api,action):
    client,repo,actor,module,connection,offer,database=api
    identifier=post_job(api).get_json()['id'];actor.id=3
    if action=='status':response=client.get('/api/v1/acquisition/jobs/'+identifier)
    elif action=='offer':response=post_job(api)
    else:response=client.post('/api/v1/acquisition/jobs/'+identifier+'/'+action,json={})
    assert response.status_code==404
    assert client.get('/api/v1/acquisition/jobs').get_json()=={'jobs':[]}


def test_no_acceptance_when_worker_or_ingest_unavailable(api,monkeypatch):
    client,repo,actor,module,connection,offer,database=api
    monkeypatch.setattr(module,'worker_available',lambda:{'available':False,'reasons':['ingest_service_unavailable']})
    assert post_job(api).status_code==503 and repo.list_jobs(2)==()


def test_approval_requires_admin_and_current_requester_access(api):
    client,repo,actor,module,connection,offer,database=api
    identifier=post_job(api).get_json()['id']
    endpoint='/api/v1/admin/acquisition/jobs/'+identifier+'/approve'
    assert client.post(endpoint,json={}).status_code==403
    actor.id=1
    assert client.get('/api/v1/admin/acquisition/jobs').get_json()['jobs'][0]['owner_id']==2
    with repo.engine.begin() as con:con.execute(text('UPDATE user SET role=0 WHERE id=2'))
    assert client.post(endpoint,json={}).status_code==403
    from cps import constants
    with repo.engine.begin() as con:con.execute(text('UPDATE user SET role=:role WHERE id=2'),{'role':constants.ROLE_ACQUISITION_ACCESS})
    assert client.post(endpoint,json={}).get_json()['state']=='queued'


def test_auto_approval_is_derived_from_current_role_not_request_body(api):
    from cps import constants
    client,repo,actor,module,connection,offer,database=api
    assert post_job(api,requires_approval=False).status_code==400
    with repo.engine.begin() as con:con.execute(text('UPDATE user SET role=:role WHERE id=2'),{'role':constants.ROLE_ACQUISITION_ACCESS|constants.ROLE_ACQUISITION_AUTO_APPROVE})
    assert post_job(api).get_json()['state']=='queued'


@pytest.mark.parametrize('kind',['navigation','search'])
def test_catalog_selections_cannot_be_submitted_as_downloads(api,kind):
    client,repo,actor,module,connection,offer,database=api
    selection=repo.create_offer(2,connection.id,{'kind':kind,'href':'https://catalog.invalid','media_type':'application/epub+zip'})
    assert post_job(api,offer_id=selection).status_code==400 and repo.list_jobs(2)==()


def test_real_blueprint_origin_guard_precedes_job_mutation(api):
    client,repo,actor,module,connection,offer,database=api
    response=client.post('/api/v1/acquisition/jobs',json={},headers={'Origin':'https://evil.invalid'})
    assert response.status_code==403 and repo.list_jobs(2)==()


def test_global_csrf_middleware_applies_to_acquisition_routes(api):
    from flask_wtf.csrf import CSRFProtect
    client,repo,actor,module,connection,offer,database=api
    client.application.config['WTF_CSRF_ENABLED']=True
    CSRFProtect(client.application)
    assert post_job(api).status_code==400 and repo.list_jobs(2)==()


def test_grant_updates_are_scoped_and_preserve_unrelated_roles(api):
    from cps import constants
    client,repo,actor,module,connection,offer,database=api;actor.id=1
    with repo.engine.begin() as con:con.execute(text('UPDATE user SET role=:role WHERE id=2'),{'role':constants.ROLE_BROWSE_GLOBAL|constants.ROLE_DOWNLOAD})
    response=client.patch('/api/v1/admin/acquisition/users/2',json={'access':True,'auto_approve':True})
    assert response.status_code==200
    with repo.engine.connect() as con:role=con.execute(text('SELECT role FROM user WHERE id=2')).scalar()
    assert role==constants.ROLE_BROWSE_GLOBAL|constants.ROLE_DOWNLOAD|constants.ROLE_ACQUISITION_ACCESS|constants.ROLE_ACQUISITION_AUTO_APPROVE
    with repo.engine.begin() as con:con.execute(text('UPDATE settings SET config_acquisition_enabled=0'))
    assert client.get('/api/v1/admin/acquisition/users').status_code==404


def test_config_and_probe_failures_never_echo_dependency_secrets(api,monkeypatch,capsys):
    client,repo,actor,module,connection,offer,database=api;actor.id=1
    def fail(*args,**kwargs):raise RuntimeError('https://private.invalid/?token=PRIVATE')
    monkeypatch.setattr(module.CatalogService,'probe',fail)
    response=client.post('/api/v1/admin/acquisition/connections/'+connection.id+'/probe',json={})
    assert response.status_code==503 and 'PRIVATE' not in response.get_data(as_text=True)
    captured=capsys.readouterr()
    assert 'PRIVATE' not in captured.out+captured.err


def test_missing_existing_key_is_not_reported_as_empty_connections(api):
    client,repo,actor,module,connection,offer,database=api;actor.id=1
    database.with_name('acquisition.key').unlink()
    assert client.get('/api/v1/admin/acquisition/connections').status_code==503
    assert not database.with_name('acquisition.key').exists()


def test_worker_execution_distinguishes_revoked_direct_grant_from_admin_approval(api):
    from cps import constants
    from cps.services.acquisition import admission
    client,repo,actor,module,connection,offer,database=api
    with repo.engine.begin() as con:con.execute(text('UPDATE user SET role=:role WHERE id=2'),{'role':constants.ROLE_ACQUISITION_ACCESS|constants.ROLE_ACQUISITION_AUTO_APPROVE})
    identifier=post_job(api).get_json()['id']
    assert admission.job_allowed(repo.engine,identifier,2)
    with repo.engine.begin() as con:con.execute(text('UPDATE user SET role=:role WHERE id=2'),{'role':constants.ROLE_ACQUISITION_ACCESS})
    assert not admission.job_allowed(repo.engine,identifier,2)
    with repo.engine.begin() as con:con.execute(text('UPDATE acquisition_job SET approved_by=1 WHERE id=:id'),{'id':identifier})
    assert admission.job_allowed(repo.engine,identifier,2)
    assert not admission.job_allowed(repo.engine,identifier,3)
    with repo.engine.begin() as con:con.execute(text('UPDATE user SET role=0 WHERE id=2'))
    assert not admission.job_allowed(repo.engine,identifier,2)


def test_limiter_breach_returns_json_without_recording_job(api,monkeypatch):
    from werkzeug.exceptions import TooManyRequests
    client,repo,actor,module,connection,offer,database=api
    def breach():raise TooManyRequests()
    monkeypatch.setattr(module.limiter,'check',breach)
    response=post_job(api)
    assert response.status_code==429 and response.get_json()['error']['code']=='rate_limit_exceeded'
    assert repo.list_jobs(2)==()


def test_admin_feature_enable_refuses_hybrid_without_resolving_it(api):
    client,repo,actor,module,connection,offer,database=api;actor.id=1
    with repo.engine.begin() as con:
        con.execute(text("UPDATE acquisition_schema_migration SET status='needs_review'"))
        con.execute(text('UPDATE settings SET config_acquisition_enabled=0'))
    response=client.patch('/api/v1/admin/acquisition',json={'enabled':True})
    assert response.status_code==409 and response.get_json()['error']['code']=='needs_review'
    with repo.engine.connect() as con:
        assert con.execute(text('SELECT config_acquisition_enabled FROM settings')).scalar()==0
        assert con.execute(text('SELECT status FROM acquisition_schema_migration')).scalar()=='needs_review'

from cps.api.acquisition import worker_available as actual_worker_available


@pytest.mark.parametrize('service',['up','down','unknown','error'])
def test_runtime_liveness_does_not_treat_unknown_ingest_as_working(api,monkeypatch,service):
    from cps import schedule,web,cwa_functions
    client,repo,actor,module,connection,offer,database=api
    monkeypatch.setattr(schedule,'acquisition_scheduler_available',lambda:True)
    monkeypatch.setattr(cwa_functions,'get_ingest_dir',lambda:str(database.parent))
    monkeypatch.setattr(module,'config',SimpleNamespace(config_calibre_dir=str(database.parent)))
    def check():
        if service=='error':raise OSError('service probe unavailable')
        return {'cwa-ingest-service':service}
    monkeypatch.setattr(web,'_check_s6_service_status',check)
    status=actual_worker_available()
    assert status['available'] is (service=='up')
    if service!='up':assert 'ingest_service_unavailable' in status['reasons']


def test_admin_enable_refreshes_scheduler_and_reports_actual_unavailability(api,monkeypatch):
    from cps import schedule
    client,repo,actor,module,connection,offer,database=api;actor.id=1
    cfg=SimpleNamespace(config_acquisition_enabled=False)
    def save():
        with repo.engine.begin() as con:con.execute(text('UPDATE settings SET config_acquisition_enabled=:enabled'),{'enabled':int(cfg.config_acquisition_enabled)})
    cfg.save=save;monkeypatch.setattr(module,'config',cfg)
    calls=[];monkeypatch.setattr(schedule,'register_acquisition_task',lambda:calls.append('refresh'))
    monkeypatch.setattr(module,'worker_available',lambda:{'available':False,'reasons':['scheduler_unavailable']})
    response=client.patch('/api/v1/admin/acquisition',json={'enabled':True})
    assert response.status_code==200 and response.get_json()['enabled']
    assert response.get_json()['runtime']['available'] is False and calls==['refresh']
    actor.id=2;assert post_job(api).status_code==503


def test_admin_reads_do_not_create_initial_acquisition_key(api):
    client,repo,actor,module,connection,offer,database=api;actor.id=1
    with repo.engine.begin() as con:
        con.execute(repo.tables.offers.delete());con.execute(repo.tables.connections.delete())
    database.with_name('acquisition.key').unlink()
    assert client.get('/api/v1/admin/acquisition').status_code==200
    assert client.get('/api/v1/admin/acquisition/connections').get_json()=={'connections':[]}
    assert not database.with_name('acquisition.key').exists()


def test_completed_job_exposes_only_book_ids_allowed_by_normal_detail_lookup(api,monkeypatch):
    from cps import calibre_db
    from cps.api import books
    from cps.services.acquisition.storage import ImportOutcome
    client,repo,actor,module,connection,offer,database=api
    identifier=post_job(api).get_json()['id']
    # Receipt source/hash validity is independently covered by ingest tests.
    monkeypatch.setattr(module.runtime,'open_repository',lambda *args,**kwargs:__import__('contextlib').nullcontext(repo))
    monkeypatch.setattr(repo,'get_receipt',lambda *args:ImportOutcome('a'*64,'b'*64,(17,18)))
    monkeypatch.setattr(books,'_can_browse_global',lambda:True)
    calls=[]
    def visible(book_id,read_column,**kwargs):
        calls.append((book_id,kwargs));return (object(),False,False) if book_id==18 else None
    monkeypatch.setattr(calibre_db,'get_book_read_archived',visible)
    response=client.get('/api/v1/acquisition/jobs/'+identifier)
    assert response.status_code==200 and response.get_json()['result']['book_ids']==[18]
    assert all(options['allow_show_global'] and options['allow_public_shelf_books'] for _,options in calls)


def test_unknown_adapter_is_not_browsable_even_with_known_connection_id(api):
    client,repo,actor,module,connection,offer,database=api
    with repo.engine.begin() as con:con.execute(repo.tables.connections.update().values(adapter='unsupported'))
    response=client.get('/api/v1/acquisition/catalog',query_string={'connection':connection.id})
    assert response.status_code==404
    assert client.get('/api/v1/acquisition').get_json()['connections']==[]


@pytest.mark.parametrize('change',['off','revoked','hybrid'])
def test_me_acquisition_capability_tracks_current_database_not_cached_user(api,monkeypatch,change):
    from cps import ub
    from cps.api import auth
    client,repo,actor,module,connection,offer,database=api
    user=ub.User();user.id=2;user.name='Reader';user.locale='en';user.theme=1;user.role=0
    monkeypatch.setattr(auth,'current_user',user)
    assert client.get('/api/v1/auth/me').get_json()['acquisition_access'] is True
    with repo.engine.begin() as con:
        if change=='off':con.execute(text('UPDATE settings SET config_acquisition_enabled=0'))
        if change=='revoked':con.execute(text('UPDATE user SET role=0 WHERE id=2'))
        if change=='hybrid':con.execute(text("UPDATE acquisition_schema_migration SET status='needs_review'"))
    assert client.get('/api/v1/auth/me').get_json()['acquisition_access'] is False


def test_job_response_uses_existing_private_cache_protection(api):
    from cps import protect_user_specific_catalog_responses
    client,repo,actor,module,connection,offer,database=api
    client.application.after_request(protect_user_specific_catalog_responses)
    response=post_job(api)
    assert response.headers['Cache-Control']=='private, no-store'
    assert {'Cookie','Authorization'} <= set(response.vary)


def test_retry_does_not_bypass_revoked_direct_acquisition_grant(api):
    client,repo,actor,module,connection,offer,database=api
    identifier=post_job(api).get_json()['id']
    with repo.engine.begin() as con:con.execute(text("UPDATE acquisition_job SET state='failed' WHERE id=:id"),{'id':identifier})
    response=client.post('/api/v1/acquisition/jobs/'+identifier+'/retry',json={})
    assert response.status_code==403
    assert repo.get_job(2,identifier).state=='failed'


def test_catalog_worker_owns_repository_without_using_request_context_in_thread(api,monkeypatch):
    # Reach the offload seam the way the route reaches it. `_run_private_blocking`
    # does `from ..services.parallel import run_blocking`, which resolves
    # sys.modules['cps.services.parallel']. `from cps.services import parallel`
    # resolves the *package attribute* instead, and the two are not always the
    # same object: tests/unit/conftest.py evicts and re-imports the cps package
    # tree to undo other files' stub installers, so the attribute can still point
    # at a stale copy of the module while sys.modules holds a newer one.
    #
    # Patching the stale copy fails silently and reads as flakiness. The real
    # run_blocking still offloads, so browse still runs off the request thread
    # and the request still returns 200 with the right body -- only the
    # instrumentation goes missing. Measured under `-n 4 --dist=loadfile`: the
    # patched module was id 4507532832 while the route used id 4631330880.
    import importlib,threading
    from concurrent.futures import ThreadPoolExecutor
    from flask import has_request_context
    parallel=importlib.import_module('cps.services.parallel')
    client,repo,actor,module,connection,offer,database=api
    submitted=[];observed={}
    request_thread=threading.get_ident()
    def run(work):
        with ThreadPoolExecutor(max_workers=1) as executor:
            submitted.append(True)
            return executor.submit(work).result()
    def browse(service,owner,identifier,**options):
        # Recorded, not asserted, in here: an assertion inside a callback that
        # never runs is an assertion that never fails, which is how the missing
        # patch stayed invisible. The checks after the request fail loudly when
        # browse is skipped altogether.
        observed['thread']=threading.get_ident();observed['context']=has_request_context()
        assert owner==2 and identifier==connection.id
        assert service.repository.connection_config(identifier).config['endpoint']=='https://catalog.invalid'
        return {'title':'Fixture catalog','publications':[]}
    monkeypatch.setattr(parallel,'run_blocking',run)
    monkeypatch.setattr(module.CatalogService,'browse',browse)
    response=client.get('/api/v1/acquisition/catalog',query_string={'connection':connection.id})
    assert response.status_code==200 and response.get_json()['title']=='Fixture catalog'
    assert observed.get('thread') is not None,'CatalogService.browse never ran'
    assert observed['context'] is False,'catalog work ran inside the request context'
    assert observed['thread']!=request_thread,'catalog work ran on the request thread'
    assert submitted==[True],'the route bypassed services.parallel.run_blocking'


@pytest.mark.parametrize('formats,allowed',[('pdf',False),('epub',True),('',True),('mobi',False)])
def test_request_and_execution_format_policy_follow_current_configuration(api,formats,allowed):
    from cps.services.acquisition import admission
    client,repo,actor,module,connection,offer,database=api
    with repo.engine.begin() as con:con.execute(text('UPDATE settings SET config_upload_formats=:formats'),{'formats':formats})
    assert admission.format_allowed(repo.engine,'application/epub+zip') is allowed
    response=post_job(api)
    assert response.status_code==(202 if allowed else 400)
    assert bool(repo.list_jobs(2)) is allowed


def test_catalog_does_not_offer_formats_disabled_by_administrator(api,monkeypatch):
    client,repo,actor,module,connection,offer,database=api
    with repo.engine.begin() as con:con.execute(text("UPDATE settings SET config_upload_formats='pdf'"))
    monkeypatch.setattr(module.CatalogService,'browse',lambda *args,**kwargs:{'publications':[{'title':'Book','offers':[{'format':'EPUB','offer_id':'one'},{'format':'PDF','offer_id':'two'}]}],'groups':[]})
    result=client.get('/api/v1/acquisition/catalog',query_string={'connection':connection.id}).get_json()
    assert result['publications'][0]['offers']==[{'format':'PDF','offer_id':'two'}]


def test_other_accounts_catalog_cursor_is_rejected_before_any_remote_fetch(api,monkeypatch):
    client,repo,actor,module,connection,offer,database=api
    selection=repo.create_offer(2,connection.id,{'kind':'navigation','href':'https://catalog.invalid/next'})
    actor.id=3
    monkeypatch.setattr(module.CatalogService,'_fetch',lambda *args:pytest.fail('foreign cursor reached network'))
    response=client.get('/api/v1/acquisition/catalog',query_string={'connection':connection.id,'selection':selection})
    assert response.status_code==404


@pytest.mark.parametrize('key_state',['missing','corrupt'])
def test_runtime_refuses_queue_when_its_existing_key_is_unavailable(api,monkeypatch,key_state):
    from cps import schedule,web,cwa_functions
    client,repo,actor,module,connection,offer,database=api
    monkeypatch.setattr(schedule,'acquisition_scheduler_available',lambda:True)
    monkeypatch.setattr(cwa_functions,'get_ingest_dir',lambda:str(database.parent))
    monkeypatch.setattr(module,'config',SimpleNamespace(config_calibre_dir=str(database.parent)))
    monkeypatch.setattr(web,'_check_s6_service_status',lambda:{'cwa-ingest-service':'up'})
    key=database.with_name('acquisition.key')
    if key_state=='missing':key.unlink()
    else:key.write_bytes(b'corrupt-existing-key')
    status=actual_worker_available()
    assert not status['available']
    assert ('key_unavailable' if key_state=='missing' else 'repository_unavailable') in status['reasons']
    if key_state=='missing':assert not key.exists()
    else:assert key.read_bytes()==b'corrupt-existing-key'


def test_admin_can_configure_encrypted_connection_while_feature_stays_off(api,monkeypatch):
    client,repo,actor,module,connection,offer,database=api;actor.id=1
    with repo.engine.begin() as con:
        con.execute(repo.tables.offers.delete());con.execute(repo.tables.connections.delete())
        con.execute(text('UPDATE settings SET config_acquisition_enabled=0'))
    key=database.with_name('acquisition.key');key.unlink()
    monkeypatch.setattr(module.CatalogService,'probe',lambda *args:pytest.fail('saving configuration contacted source'))
    response=client.post('/api/v1/admin/acquisition/connections',json={'label':'My catalog','adapter':'opds','config':{'endpoint':'https://catalog.invalid/opds','auth_kind':'bearer','secret':'PRIVATE_NEW_KEY'}})
    assert response.status_code==201 and response.get_json()['enabled'] is False
    assert key.exists() and 'PRIVATE_NEW_KEY' not in response.get_data(as_text=True) and 'endpoint' not in response.get_json()
    identifier=response.get_json()['id']
    assert client.patch('/api/v1/admin/acquisition/connections/'+identifier,json={'enabled':True}).status_code==200
    actor.id=2;assert client.get('/api/v1/acquisition').status_code==404


def test_probe_is_explicit_read_only_catalog_operation(api,monkeypatch):
    client,repo,actor,module,connection,offer,database=api
    calls=[]
    def probe(service,configuration):
        calls.append(configuration['endpoint'])
        return {'title':'Catalog','protocol':'opds1','browse':True,'search_advertised':False,'direct_download_advertised':True}
    monkeypatch.setattr(module.CatalogService,'probe',probe)
    endpoint='/api/v1/admin/acquisition/connections/'+connection.id+'/probe'
    assert client.post(endpoint,json={}).status_code==403 and calls==[]
    actor.id=1;response=client.post(endpoint,json={})
    assert response.status_code==200 and response.get_json()['browse']
    assert calls==['https://catalog.invalid'] and repo.list_jobs(2)==()


def test_selection_capacity_returns_actionable_safe_rate_response(api,monkeypatch):
    from cps.services.acquisition.storage import SelectionLimit
    client,repo,actor,module,connection,offer,database=api
    def full(*args,**kwargs):raise SelectionLimit()
    monkeypatch.setattr(module.CatalogService,'browse',full)
    response=client.get('/api/v1/acquisition/catalog',query_string={'connection':connection.id})
    assert response.status_code==429
    error=response.get_json()['error']
    assert error['code']=='selections_full' and 'expire' in error['message']
    assert 'private upstream' not in response.get_data(as_text=True)


@pytest.mark.parametrize('api',[True],indirect=True)
def test_job_polling_cannot_starve_the_request_that_creates_a_job(api):
    """The job page polls the same route it posts to.

    /acquisition/jobs answers GET (the list the page polls every 3s) and POST
    (request this book). While both drew on one bucket, a couple of open tabs
    spent the allowance on polling and then "request this book" failed with a
    429 although nothing was wrong. Reads and writes must not share a bucket.
    """
    client,repo,actor,module,connection,offer,database=api
    module.limiter.storage.reset()
    codes=[client.get('/api/v1/acquisition/jobs').status_code for _ in range(200)]
    assert 429 in codes, 'the read allowance must still be bounded'
    assert client.get('/api/v1/acquisition/jobs').status_code==429

    created=post_job(api)
    assert created.status_code==202, (
        'job creation was starved by polling: %s' % created.get_data(as_text=True))
    assert repo.list_jobs(2), 'the job must actually have been recorded'


@pytest.mark.parametrize('api',[True],indirect=True)
def test_a_breached_read_allowance_still_answers_as_json(api):
    client,repo,actor,module,connection,offer,database=api
    module.limiter.storage.reset()
    breached=None
    for _ in range(400):
        response=client.get('/api/v1/acquisition/jobs')
        if response.status_code==429:
            breached=response
            break
    assert breached is not None
    assert breached.get_json()['error']['code']=='rate_limit_exceeded'


def test_admin_opts_a_home_catalog_in_and_cannot_open_loopback(api,monkeypatch):
    """The Book sources form's home-network switch, through the real route."""
    client,repo,actor,module,connection,offer,database=api;actor.id=1
    monkeypatch.setattr(module.CatalogService,'probe',lambda *args:pytest.fail('saving configuration contacted source'))
    url='/api/v1/admin/acquisition/connections'
    response=client.post(url,json={'label':'Home Calibre','adapter':'opds','config':{
        'endpoint':'http://192.168.1.5:8080/opds','allow_private_network':True}})
    assert response.status_code==201
    stored=repo.connection_config(response.get_json()['id'],include_disabled=True).config
    assert stored['private_origins']==['http://192.168.1.5:8080/opds'] and '192.168.0.0/16' in stored['private_networks']
    assert client.patch(url+'/'+response.get_json()['id'],json={'enabled':True}).status_code==200
    refused=client.post(url,json={'label':'Loopback','adapter':'opds','config':{
        'endpoint':'http://192.168.1.5:8080/opds','private_origins':['http://192.168.1.5:8080'],
        'private_networks':['127.0.0.0/8']}})
    assert refused.status_code==400 and refused.get_json()['error']['code']=='private_network_not_allowed'


def test_reject_route_remains_available_while_feature_is_paused(api):
    client,repo,actor,module,connection,offer,database=api
    job = post_job(api).get_json()
    actor.id = 1
    with repo.engine.begin() as conn:
        conn.execute(text('UPDATE settings SET config_acquisition_enabled=0'))
    response = client.post(f"/api/v1/admin/acquisition/jobs/{job['id']}/reject")
    assert response.status_code == 200
    assert repo.get_job(2,job['id']).state == 'rejected'
    assert repo.claim() is None


def test_admin_edit_preserves_redacted_credentials_and_delete_keeps_history(api):
    client,repo,actor,module,connection,offer,database=api
    actor.id = 1
    private = repo.connection_config(connection.id).config
    private.update(endpoint='https://catalog.invalid/feed?key=SECRET_QUERY', auth_kind='bearer', secret='SECRET_CREDENTIAL')
    repo.update_connection(connection.id,label=connection.label,config=private)
    path = f'/api/v1/admin/acquisition/connections/{connection.id}'
    response = client.get(path)
    config = response.get_json()['config']
    assert 'SECRET' not in response.get_data(as_text=True)
    assert config['has_secret'] and config['has_endpoint_query']
    response = client.patch(path,json={'label':'Renamed','config':{'endpoint':config['endpoint']}})
    assert response.status_code == 200
    kept = repo.connection_config(connection.id,include_disabled=True).config
    assert kept['secret'] == 'SECRET_CREDENTIAL' and 'SECRET_QUERY' in kept['endpoint']
    assert client.delete(path).status_code == 200
    assert client.get(path).status_code == 404


def test_stale_admin_form_cannot_send_rotated_key_to_old_endpoint(api):
    client,repo,actor,module,connection,offer,database=api
    actor.id=1
    original=repo.connection_config(connection.id).config
    repo.update_connection(connection.id,label='New',config=dict(original, endpoint='https://new.invalid/feed', secret='ROTATED', auth_kind='bearer'))
    response=client.patch(f'/api/v1/admin/acquisition/connections/{connection.id}',json={
        'expected_revision':1,'label':'Old form','config':{'endpoint':original['endpoint']}})
    assert response.status_code==409 and response.get_json()['error']['code']=='connection_changed'
    assert repo.connection_config(connection.id,include_disabled=True).config['endpoint']=='https://new.invalid/feed'


def test_removing_download_origin_revokes_only_its_derived_private_scope(api):
    client,repo,actor,module,connection,offer,database=api;actor.id=1
    sab=repo.create_connection('Client','sabnzbd',{},enabled=True)
    config=module.indexer_config({'endpoint':'http://prowlarr.local:9696/1/api','secret':'PRIVATE',
        'category':'7020','client_id':sab.id,'allow_private_network':True,
        'download_origins':['http://retired.local:8090']})
    config['private_origins'].append('http://intentional.local:8090')
    c=repo.create_connection('Indexer','newznab',config,enabled=True)
    path=f'/api/v1/admin/acquisition/connections/{c.id}'
    response=client.patch(path,json={'expected_revision':1,'config':{'download_origins':['http://replacement.local:8090']}})
    assert response.status_code==200
    current=repo.connection_config(c.id,include_disabled=True).config
    assert 'http://retired.local:8090' not in current['private_origins']
    assert 'http://replacement.local:8090' in current['private_origins']
    assert 'http://intentional.local:8090' in current['private_origins']
    assert current['private_networks']==config['private_networks']
    assert client.patch(path,json={'expected_revision':2,'config':{'download_origins':[]}}).status_code==200
    current=repo.connection_config(c.id,include_disabled=True).config
    assert current['private_origins']==['http://prowlarr.local:9696/1/api','http://intentional.local:8090']


def test_endpoint_origin_change_requires_explicit_credential_reentry(api):
    client, repo, actor, module, connection, offer, database = api
    actor.id = 1
    private = repo.connection_config(connection.id).config
    private.update(auth_kind='bearer', secret='EXISTING_KEY')
    repo.update_connection(connection.id, label=connection.label, config=private)
    path = f'/api/v1/admin/acquisition/connections/{connection.id}'
    revision = repo.list_connections(include_disabled=True)[0].revision
    for changes in ({'endpoint': 'https://foreign.invalid/feed'}, {'endpoint': 'https://foreign.invalid/feed', 'secret': ''}):
        response = client.patch(path, json={'expected_revision': revision, 'config': changes})
        assert response.status_code == 400
        assert response.get_json()['error']['code'] == 'credential_required_for_new_origin'
        assert repo.connection_config(connection.id, include_disabled=True).config == private
    assert client.patch(path, json={'expected_revision': revision,
        'config': {'endpoint': 'https://catalog.invalid/renamed'}}).status_code == 200
    revision += 1
    assert client.patch(path, json={'expected_revision': revision,
        'config': {'endpoint': 'https://foreign.invalid/feed', 'secret': 'REENTERED_KEY'}}).status_code == 200
    updated = repo.connection_config(connection.id, include_disabled=True).config
    assert updated['secret'] == 'REENTERED_KEY'
    assert updated['credential_origins'] == ['https://foreign.invalid/feed']
    assert client.patch(path, json={'config': {'endpoint': 'not-a-url'}}).status_code == 400


@pytest.mark.parametrize('adapter',['nzbget','qbittorrent','transmission'])
def test_new_client_admin_setup_encrypts_credentials_and_fences_edits(api,adapter):
    client,repo,actor,module,connection,offer,database=api; actor.id=1
    body={'label':'Client','adapter':adapter,'config':{'endpoint':'https://client.example/','auth_kind':'basic','username':'fixture','secret':'CLIENT_PASSWORD','category':'books','remote_path':'/downloads','local_path':str(database.parent)}}
    response=client.post('/api/v1/admin/acquisition/connections',json=body)
    assert response.status_code==201
    row=response.get_json(); assert not row['enabled']
    endpoint='/api/v1/admin/acquisition/connections/'+row['id']
    safe=client.get(endpoint).get_json(); assert safe['config']['has_secret']
    assert 'CLIENT_PASSWORD' not in client.get(endpoint).get_data(as_text=True)
    assert client.patch(endpoint,json={'config':{'category':'ebooks'},'expected_revision':1}).status_code==200
    assert repo.connection_config(row['id'],include_disabled=True).config['secret']=='CLIENT_PASSWORD'
    assert client.patch(endpoint,json={'config':{'endpoint':'https://other.example/'},'expected_revision':2}).get_json()['error']['code']=='credential_required_for_new_origin'


def test_account_locale_is_captured_for_localized_catalog_before_private_io(api, monkeypatch):
    """The account's saved locale must survive the request-to-worker seam."""
    import json
    from concurrent.futures import ThreadPoolExecutor
    from flask import has_request_context
    from werkzeug.local import LocalProxy
    from cps.services.acquisition.catalog import CatalogService
    from cps.services.acquisition.http import FetchedDocument
    client, repo, actor, module, connection, offer, database = api
    actor.locale = 'fr_CA'
    def request_actor():
        if not has_request_context(): raise RuntimeError('account read outside request')
        return actor
    monkeypatch.setattr(module, 'current_user', LocalProxy(request_actor))
    feed = {'metadata': {'title': {'fr': 'Livres', 'en': 'Books'}}, 'publications': [
        {'metadata': {'title': {'fr': 'Édition originale', 'en': 'Original edition'},
                      'author': {'name': {'fr': 'Une autrice', 'en': 'A Writer'}}},
         'links': [{'rel': 'download', 'href': '/original.epub?token=PRIVATE', 'type': 'application/epub+zip'}]}]}
    def transfer(url, policy, **kwargs):
        assert not has_request_context()
        return FetchedDocument(json.dumps(feed).encode(), url, 'application/opds+json')
    monkeypatch.setattr(module, 'CatalogService', lambda repo, **kw: CatalogService(repo, transfer=transfer, **kw))
    def blocking(fn):
        with ThreadPoolExecutor(max_workers=1) as executor: return executor.submit(fn).result(timeout=10)
    monkeypatch.setattr(module, '_run_private_blocking', blocking)
    response = client.get('/api/v1/acquisition/catalog', query_string={'connection': connection.id})
    assert response.status_code == 200, response.get_data(as_text=True)
    page = response.get_json(); book, = page['publications']
    assert (page['title'], book['title'], book['authors']) == ('Livres', 'Édition originale', ['Une autrice'])
    assert 'PRIVATE' not in response.get_data(as_text=True) and 'https://' not in response.get_data(as_text=True)
    assert not repo.list_jobs(actor.id)
