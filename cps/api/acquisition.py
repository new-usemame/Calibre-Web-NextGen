# SPDX-License-Identifier: GPL-3.0-or-later
"""Gated generic OPDS acquisition API; secrets and source URLs stay server-side."""
from dataclasses import asdict
from functools import wraps
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from flask import jsonify, request
from werkzeug.exceptions import HTTPException

from . import api_v1, log
from .. import config, constants, limiter, ub
from ..cw_login import current_user
from ..services.acquisition import admission, runtime
from ..services.acquisition.catalog import CatalogService, CatalogError, connection_config
from ..services.acquisition.contracts import DIRECT_FORMATS
from ..services.acquisition.http import TransportError, origin
from ..services.acquisition.opds import CatalogParseError
from ..services.acquisition.newznab import IndexerError, IndexerService, connection_config as indexer_config
from ..services.acquisition.sabnzbd import ClientError, SABClient, connection_config as client_config
from ..services.acquisition.clients import CLIENTS, CLIENT_KINDS, connection_config as other_client_config
from ..services.acquisition.storage import Conflict, ConnectionChanged, NotFound, StorageError


def _error(code, status):
    messages = {'unauthorized':'Sign in to continue', 'forbidden':'This action is not permitted',
        'not_found':'Acquisition is unavailable', 'invalid_request':'Check the supplied fields',
        'conflict':'This request cannot be changed in its current state',
        'acquisition_unavailable':'Acquisition is not ready; ask an administrator to check its status',
        'needs_review':'Legacy acquisition permissions need administrator review',
        'source_unavailable':'The catalog could not be read', 'rate_limit_exceeded':'Too many requests; try again later',
        'private_network_not_allowed':'Only a home or local network range can be allowed. '
            'Loopback, link-local and cloud metadata addresses cannot.',
        'invalid_network_policy':'Check the network settings for this catalog',
        'selections_full':'Too many active catalog selections. Wait for older selections to expire, then try again.'}
    return jsonify({'error':{'code':code,'message':messages.get(code,'Acquisition request failed')}}),status


# Codes connection_config raises for administrator input. They describe the
# submitted form, not an unreachable source, so they answer 400 rather than
# falling through to the generic CatalogError -> 502 "could not be read".
CONFIG_ERRORS = frozenset({'private_network_not_allowed', 'invalid_network_policy',
    'private_origin_and_network_required', 'invalid_connection', 'invalid_authentication'})


def _owner():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return None
    return int(current_user.id)


def _key():
    return 'acquisition:' + str(_owner() or 'anonymous') + ':' + (request.remote_addr or '')


# flask-limiter gives each decorated endpoint its own bucket, so these are not
# one shared allowance across the API. What *did* share a bucket was GET and
# POST on a single route: /acquisition/jobs both lists jobs and creates them,
# and the job page polls the list every 3s (20/min per open tab). Two tabs
# plus a little browsing spent the allowance, and because the writes drew on
# the same bucket, "request this book" started failing with 429 while nothing
# was wrong. per_method splits reads from writes so polling can no longer
# starve a deliberate action, and reads get the larger share because polling
# is what generates the volume.
# Writes keep the allowance they already had, so nothing that worked before
# gets tighter; reads are the ones that needed room.
READ_RATE = '120/minute'        # status polling: 20/min for each open tab
WRITE_RATE = '60/minute'        # deliberate actions: create, cancel, retry, approve
REMOTE_READ_RATE = '60/minute'  # browsing a catalog reaches the source server


def _rate(read, write):
    def chosen():
        return read if request.method in ('GET', 'HEAD') else write
    return chosen


def _endpoint(*, admin=False, available=False, read=READ_RATE, write=WRITE_RATE):
    def decorate(function):
        @wraps(function)
        def wrapped(*args,**kwargs):
            from ..user_library import mark_response_user_specific
            mark_response_user_specific()
            owner = _owner()
            if owner is None:
                return _error('unauthorized',401)
            role = admission.account_role(ub.app_DB_path,owner)
            if admin:
                if role is None or not role & constants.ROLE_ADMIN:
                    return _error('forbidden',403)
            elif not admission.instance_enabled(ub.app_DB_path) or not admission.account_allowed(ub.app_DB_path,owner):
                return _error('not_found',404)
            try:
                if limiter is not None:
                    limiter.check()
                if available and not worker_available()['available']:
                    return _error('acquisition_unavailable',503)
                return function(*args,**kwargs)
            except NotFound:
                return _error('not_found',404)
            except Conflict as error:
                if getattr(error,'code',None)=='selections_full':
                    return _error('selections_full',429)
                return _error('connection_changed' if getattr(error, 'code', None) == 'connection_changed' else 'conflict',409)
            except admission.AdmissionError:
                return _error('invalid_request',400)
            except (IndexerError, ClientError) as error:
                return _error(error.code, 502)
            except (CatalogError, CatalogParseError, TransportError):
                return _error('source_unavailable',502)
            except StorageError:
                return _error('acquisition_unavailable',503)
            except HTTPException as error:
                if error.code == 429:
                    return _error('rate_limit_exceeded',429)
                raise
            except Exception as error:
                log.warning("Acquisition dependency unavailable (%s)", type(error).__name__)
                # Config/credential/HTTP dependencies can include private URLs
                # in their exception chain. Do not pass them to generic logging.
                return _error('acquisition_unavailable',503)
        if limiter is None:
            return wrapped
        return limiter.limit(_rate(read,write),key_func=_key,per_method=True)(wrapped)
    return decorate



def _run_private_blocking(operation):
    # gevent's threadpool reports uncaught worker exceptions to stderr before
    # a waiting request can sanitize them. Keep errors as private return values
    # inside the worker; route error handling remains on the request thread.
    from ..services.parallel import run_blocking
    def invoke():
        try:
            return True,operation()
        except Exception as error:
            return False,error
    successful,value=run_blocking(invoke)
    if not successful:
        raise value from None
    return value


def worker_available():
    """Runtime preconditions only: a catalog probe does not prove import success.

    First slice requires the existing s6 ingest service. Unknown/non-s6 runtimes
    are unavailable rather than accepting jobs into an unverified queue.
    """
    from ..schedule import acquisition_scheduler_available
    from ..cwa_functions import get_ingest_dir
    from ..web import _check_s6_service_status
    reasons=[]
    if not acquisition_scheduler_available(): reasons.append('scheduler_unavailable')
    if admission.instance_state(ub.app_DB_path)['migration_status'] != 'ready': reasons.append('migration_unavailable')
    if not admission.configured_media_types(ub.app_DB_path): reasons.append('formats_disabled')
    try:
        key=Path(ub.app_DB_path).parent/'acquisition.key'
        key_present=key.is_file() and not key.is_symlink()
    except (TypeError,OSError):
        key_present=False
    if not key_present:
        reasons.append('key_unavailable')
    else:
        database=ub.app_DB_path
        def repository_ready():
            try:
                with runtime.open_repository(database):
                    return True
            except Exception:
                return False
        if not _run_private_blocking(repository_ready): reasons.append('repository_unavailable')
    try:
        ingest=Path(get_ingest_dir())
        if not ingest.is_dir() or not os.access(ingest,os.W_OK|os.X_OK): reasons.append('ingest_unwritable')
    except (OSError,TypeError): reasons.append('ingest_unavailable')
    try:
        if not config.config_calibre_dir or not Path(config.config_calibre_dir).is_dir(): reasons.append('library_unavailable')
    except (OSError,TypeError): reasons.append('library_unavailable')
    try:
        running=_run_private_blocking(_check_s6_service_status).get('cwa-ingest-service') == 'up'
    except Exception:
        running=False
    if not running: reasons.append('ingest_service_unavailable')
    return {'available':not reasons,'reasons':reasons}



CONFIGURATORS = {'opds': connection_config, 'newznab': indexer_config, 'sabnzbd': client_config,
    **{kind: (lambda value, kind=kind: other_client_config(kind, value)) for kind in ('nzbget', 'qbittorrent', 'transmission')}}


def _require_connection(repo, connection_id, *, include_disabled=False, catalog=False):
    kinds = ('opds', 'newznab') if catalog else tuple(CONFIGURATORS)
    rows = [row for row in repo.list_connections(include_disabled=include_disabled)
        if row.id == connection_id and row.adapter in kinds]
    if not rows:
        raise NotFound('Connection unavailable')
    return rows[0]


def _validated_config(repo, adapter, value):
    validated = CONFIGURATORS[adapter](value)
    if adapter == 'newznab':
        client = _require_connection(repo, validated['client_id'], include_disabled=True)
        if client.adapter not in CLIENT_KINDS:
            raise admission.AdmissionError('invalid_request')
    return validated


def _safe_config(config):
    # Even administrator responses never echo provider tokens in endpoint queries.
    def address(value):
        parts = urlsplit(value)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))
    result = {key: value for key, value in config.items() if key != 'secret'}
    result['has_secret'] = bool(config.get('secret'))
    result['has_endpoint_query'] = bool(urlsplit(config['endpoint']).query)
    result['endpoint'] = address(config['endpoint'])
    for key in ('credential_origins', 'private_origins'):
        result[key] = [address(value) for value in config.get(key, [])]
    return result


def _json(allowed):
    value=request.get_json(silent=True)
    if not isinstance(value,dict) or set(value)-set(allowed):
        raise admission.AdmissionError('invalid_request')
    return value


def _job(repo,job):
    result={key:value for key,value in asdict(job).items() if key not in ('offer_id','owner_id')}
    receipt=repo.get_receipt(job.owner_id,job.id)
    # Book IDs are authoritative; visibility remains enforced by book endpoints.
    if receipt is not None:
        from .books import _can_browse_global
        from .. import calibre_db
        visible=[]
        for identifier in receipt.book_ids:
            if calibre_db.get_book_read_archived(identifier, getattr(config,'config_read_column',0),
                    allow_show_archived=True,allow_show_hidden=True,
                    allow_show_global=_can_browse_global(),allow_public_shelf_books=True):
                visible.append(identifier)
        result['result']={'book_ids':visible,'disposition':receipt.disposition}
    return result


@api_v1.route('/admin/acquisition',methods=['GET','PATCH'])
@_endpoint(admin=True)
def acquisition_admin_settings():
    if request.method=='PATCH':
        body=_json({'enabled'})
        if not isinstance(body.get('enabled'),bool): raise admission.AdmissionError('invalid_request')
        state=admission.instance_state(ub.app_DB_path)
        if body['enabled'] and state['migration_status']!='ready':
            return _error('needs_review' if state['migration_status']=='needs_review' else 'acquisition_unavailable',409)
        previous=config.config_acquisition_enabled
        config.config_acquisition_enabled=body['enabled']
        try: config.save()
        except Exception:
            config.config_acquisition_enabled=previous
            raise
        from ..schedule import register_acquisition_task
        register_acquisition_task()
    return jsonify(dict(admission.instance_state(ub.app_DB_path),runtime=worker_available()))


@api_v1.route('/admin/acquisition/connections',methods=['GET','POST'])
@_endpoint(admin=True)
def acquisition_admin_connections():
    if request.method=='GET':
        # Fresh disabled installs have no key and no connections; no key creation.
        if not (Path(ub.app_DB_path).parent/'acquisition.key').exists() and not admission.has_connections(ub.app_DB_path):
            return jsonify({'connections':[]})
        with runtime.open_repository(ub.app_DB_path) as repo:
            return jsonify({'connections':[asdict(row) for row in repo.list_connections(include_disabled=True) if row.adapter in CONFIGURATORS]})
    if admission.instance_state(ub.app_DB_path)['migration_status']!='ready':
        return _error('needs_review',409)
    body=_json({'label','adapter','config'})
    adapter = body.get('adapter', 'opds')
    if adapter not in CONFIGURATORS: raise admission.AdmissionError('invalid_request')
    if not isinstance(body.get('label'),str) or not body['label'].strip() or len(body['label'])>200:
        raise admission.AdmissionError('invalid_request')
    with runtime.open_repository(ub.app_DB_path,initialize_key=True) as repo:
        try:
            validated = _validated_config(repo, adapter, body.get('config'))
        except (CatalogError, TransportError) as error:
            code = error.code if isinstance(error, TransportError) else str(error)
            return _error(code if code in CONFIG_ERRORS else 'invalid_request', 400)
        row=repo.create_connection(body['label'],adapter,validated,enabled=False)
        return jsonify(asdict(row)),201


@api_v1.route('/admin/acquisition/connections/<connection_id>',methods=['GET','PATCH','DELETE'])
@_endpoint(admin=True)
def acquisition_admin_connection(connection_id):
    with runtime.open_repository(ub.app_DB_path) as repo:
        row = _require_connection(repo, connection_id, include_disabled=True)
        if request.method == 'DELETE':
            repo.delete_connection(connection_id, expected_revision=row.revision)
            return jsonify({'ok': True})
        material = repo.connection_config(connection_id,include_disabled=True)
        if request.method == 'GET':
            return jsonify(dict(asdict(row), config=_safe_config(material.config)))
        body = _json({'enabled', 'label', 'config', 'expected_revision'})
        if not body or ('enabled' in body and set(body) - {'expected_revision'} != {'enabled'}):
            raise admission.AdmissionError('invalid_request')
        if 'expected_revision' in body:
            if type(body['expected_revision']) is not int or body['expected_revision'] < 1:
                raise admission.AdmissionError('invalid_request')
            if body['expected_revision'] != row.revision:
                raise ConnectionChanged('Connection changed since loading the form')
        if 'enabled' in body:
            if not isinstance(body['enabled'], bool): raise admission.AdmissionError('invalid_request')
            if body['enabled'] and admission.instance_state(ub.app_DB_path)['migration_status']!='ready':
                return _error('needs_review',409)
            repo.set_connection_enabled(connection_id,body['enabled'], expected_revision=row.revision)
        else:
            if admission.instance_state(ub.app_DB_path)['migration_status'] != 'ready':
                return _error('needs_review',409)
            label = body.get('label', row.label)
            if not isinstance(label, str) or not label.strip() or len(label) > 200:
                raise admission.AdmissionError('invalid_request')
            changes = body.get('config', {})
            if not isinstance(changes, dict): raise admission.AdmissionError('invalid_request')
            merged = dict(material.config, **changes)
            # Query credentials are redacted in GET. An unchanged displayed
            # endpoint preserves its sealed query rather than erasing it.
            if changes.get('endpoint') == _safe_config(material.config)['endpoint'] and urlsplit(material.config['endpoint']).query:
                merged['endpoint'] = material.config['endpoint']
            # Reconcile derived redirect allowances when an admin removes them.
            # Preserve the endpoint allowance and unrelated advanced scopes.
            if row.adapter == 'newznab' and 'download_origins' in changes:
                derived = {origin(value) for value in material.config.get('download_origins', [])}
                endpoint_origin = origin(material.config['endpoint'])
                merged['private_origins'] = [value for value in merged.get('private_origins', [])
                    if origin(value) == endpoint_origin or origin(value) not in derived]
            # Friendly LAN checkbox replaces the expanded policy as on creation.
            if 'allow_private_network' in changes:
                merged.pop('private_origins', None)
                merged.pop('private_networks', None)
            try:
                origin_changed = origin(merged['endpoint']) != origin(material.config['endpoint'])
                if (material.config.get('secret') and origin_changed
                        and not (isinstance(changes.get('secret'), str) and changes['secret'].strip())):
                    return _error('credential_required_for_new_origin', 400)
                if (origin_changed and merged.get('auth_kind') in ('basic', 'bearer')
                        and 'credential_origins' not in changes):
                    # Re-entered credentials are for the newly chosen server;
                    # inherited redirect scopes do not gain that new credential.
                    merged['credential_origins'] = [merged['endpoint']]
                validated = _validated_config(repo, row.adapter, merged)
            except (CatalogError, TransportError) as error:
                code = error.code if isinstance(error, TransportError) else str(error)
                return _error(code if code in CONFIG_ERRORS else 'invalid_request',400)
            repo.update_connection(connection_id,label=label,config=validated,expected_revision=row.revision)
    return jsonify({'ok':True})


@api_v1.route('/admin/acquisition/connections/<connection_id>/probe',methods=['POST'])
@_endpoint(admin=True)
def acquisition_admin_probe(connection_id):
    database=ub.app_DB_path
    def probe():
        with runtime.open_repository(database) as repo:
            row = _require_connection(repo,connection_id,include_disabled=True)
            value = repo.connection_config(connection_id,include_disabled=True).config
            if row.adapter in CLIENT_KINDS:
                return CLIENTS[row.adapter](value).probe()
            if row.adapter == 'newznab':
                result = IndexerService(repo).probe(value)
                client = _require_connection(repo, value['client_id'], include_disabled=True)
                CLIENTS[client.adapter](repo.connection_config(client.id, include_disabled=True).config).probe()
                result['client_verified'] = True
                return result
            return CatalogService(repo).probe(value)
    return jsonify(_run_private_blocking(probe))


@api_v1.route('/acquisition',methods=['GET'])
@_endpoint()
def acquisition_bootstrap():
    role=admission.account_role(ub.app_DB_path,_owner()) or 0
    with runtime.open_repository(ub.app_DB_path) as repo:
        return jsonify({'connections':[asdict(row) for row in repo.list_connections() if row.adapter in ('opds', 'newznab')],
            'can_acquire':bool(role & constants.ROLE_ACQUISITION_AUTO_APPROVE),'runtime':worker_available()})


@api_v1.route('/acquisition/catalog',methods=['GET'])
@_endpoint(read=REMOTE_READ_RATE)
def acquisition_catalog():
    if set(request.args)-{'connection','selection','q'}: raise admission.AdmissionError('invalid_request')
    database,owner,connection_id=ub.app_DB_path,_owner(),request.args.get('connection')
    selection,query=request.args.get('selection'),request.args.get('q')
    # Capture account presentation state before leaving the request context.
    language = getattr(current_user, 'locale', None) or 'en'
    def browse():
        with runtime.open_repository(database) as repo:
            row = _require_connection(repo,connection_id,catalog=True)
            service = IndexerService(repo) if row.adapter == 'newznab' else CatalogService(repo, preferred_language=language)
            result=service.browse(owner,connection_id,selection=selection,query=query)
            allowed=admission.configured_media_types(repo.engine)
            formats={description[0] for media,description in DIRECT_FORMATS.items() if media in allowed}
            client_formats=bool(formats & {'EPUB','PDF'})
            for section in [result]+result.get('groups',[]):
                for publication in section.get('publications',[]):
                    publication['offers']=[offer for offer in publication.get('offers',[]) if offer.get('format') in formats or offer.get('format') in ('NZB', 'Torrent') and client_formats]
            return result
    return jsonify(_run_private_blocking(browse))


@api_v1.route('/acquisition/jobs',methods=['GET','POST'])
@_endpoint()
def acquisition_jobs():
    if request.method=='POST' and not worker_available()['available']:
        return _error('acquisition_unavailable',503)
    with runtime.open_repository(ub.app_DB_path) as repo:
        if request.method=='GET': return jsonify({'jobs':[_job(repo,row) for row in repo.list_jobs(_owner())]})
        body=_json({'connection_id','offer_id','idempotency_key','add_to_my_library'})
        job=admission.create_request(repo,_owner(),connection_id=body.get('connection_id'),
            offer_id=body.get('offer_id'),idempotency_key=body.get('idempotency_key'),
            add_to_my_library=body.get('add_to_my_library',True))
        return jsonify(_job(repo,job)),202


@api_v1.route('/acquisition/jobs/<job_id>',methods=['GET'])
@_endpoint()
def acquisition_job(job_id):
    with runtime.open_repository(ub.app_DB_path) as repo: return jsonify(_job(repo,repo.get_job(_owner(),job_id)))


@api_v1.route('/acquisition/jobs/<job_id>/books',methods=['GET','POST'])
@_endpoint()
def acquisition_bundle_books(job_id):
    if request.method == 'POST' and not worker_available()['available']:
        return _error('acquisition_unavailable',503)
    with runtime.open_repository(ub.app_DB_path) as repo:
        if request.method == 'GET':
            return jsonify(repo.bundle_choices(_owner(),job_id))
        body = _json({'generation','candidate_id'})
        job = admission.select_artifact(repo,_owner(),job_id,body.get('generation'),body.get('candidate_id'))
        return jsonify(_job(repo,job)),202


@api_v1.route('/acquisition/jobs/<job_id>/cancel',methods=['POST'])
@_endpoint()
def acquisition_cancel(job_id):
    with runtime.open_repository(ub.app_DB_path) as repo:
        repo.request_cancel(_owner(),job_id)
        return jsonify(_job(repo,repo.get_job(_owner(),job_id)))


@api_v1.route('/acquisition/jobs/<job_id>/retry',methods=['POST'])
@_endpoint(available=True)
def acquisition_retry(job_id):
    from sqlalchemy import select
    with runtime.open_repository(ub.app_DB_path) as repo:
        job=repo.get_job(_owner(),job_id)
        if job.state=='failed':
            with repo.engine.connect() as connection:
                approved=connection.execute(select(repo.tables.jobs.c.approved_by).where(
                    repo.tables.jobs.c.id==job_id,repo.tables.jobs.c.owner_id==_owner())).scalar()
            role=admission.account_role(repo.engine,_owner()) or 0
            if approved is None and not role & constants.ROLE_ACQUISITION_AUTO_APPROVE:
                return _error('forbidden',403)
        repo.retry(_owner(),job_id)
        return jsonify(_job(repo,repo.get_job(_owner(),job_id))),202


@api_v1.route('/admin/acquisition/jobs/<job_id>/approve',methods=['POST'])
@_endpoint(admin=True,available=True)
def acquisition_approve(job_id):
    from sqlalchemy import select
    if not admission.instance_enabled(ub.app_DB_path): return _error('acquisition_unavailable',503)
    with runtime.open_repository(ub.app_DB_path) as repo:
        with repo.engine.connect() as connection:
            owner=connection.execute(select(repo.tables.jobs.c.owner_id).where(repo.tables.jobs.c.id==job_id)).scalar()
        if owner is None: raise NotFound('Request unavailable')
        if not admission.account_allowed(repo.engine,owner): return _error('forbidden',403)
        repo.approve(job_id,admin_actor=_owner())
        return jsonify(_job(repo,repo.get_job(owner,job_id))),202


@api_v1.route('/admin/acquisition/jobs/<job_id>/reject',methods=['POST'])
@_endpoint(admin=True)
def acquisition_reject(job_id):
    # Refusal remains available even while the feature/worker is paused.
    with runtime.open_repository(ub.app_DB_path) as repo:
        repo.reject(job_id, admin_actor=_owner())
    return jsonify({'ok': True})


@api_v1.route('/admin/acquisition/jobs',methods=['GET'])
@_endpoint(admin=True)
def acquisition_admin_jobs():
    from sqlalchemy import select
    with runtime.open_repository(ub.app_DB_path) as repo:
        with repo.engine.connect() as connection:
            rows=connection.execute(select(repo.tables.jobs.c.owner_id,repo.tables.jobs.c.id).where(
                repo.tables.jobs.c.state=='awaiting_approval').order_by(repo.tables.jobs.c.created_at).limit(100)).all()
        return jsonify({'jobs':[dict(_job(repo,repo.get_job(owner,identifier)),owner_id=owner) for owner,identifier in rows]})


@api_v1.route('/admin/acquisition/users',methods=['GET'])
@_endpoint(admin=True)
def acquisition_admin_users():
    if not admission.instance_enabled(ub.app_DB_path): return _error('not_found',404)
    return jsonify({'users':admission.account_grants(ub.app_DB_path)})


@api_v1.route('/admin/acquisition/users/<int:owner_id>',methods=['PATCH'])
@_endpoint(admin=True)
def acquisition_admin_user(owner_id):
    if not admission.instance_enabled(ub.app_DB_path): return _error('not_found',404)
    body=_json({'access','auto_approve'})
    admission.update_account_grants(ub.app_DB_path,_owner(),owner_id,body.get('access'),body.get('auto_approve'))
    return jsonify({'ok':True})
