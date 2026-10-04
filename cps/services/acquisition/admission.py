# SPDX-License-Identifier: GPL-3.0-or-later
"""Current database authorization and acquisition admission; no effects on import."""
from contextlib import contextmanager, closing
from pathlib import Path
import sqlite3
import re

from ... import constants
from .migration import VERSION
from .contracts import DIRECT_FORMATS, MOBI_MEDIA_TYPE


class AdmissionError(ValueError):
    pass


@contextmanager
def _read(database):
    if hasattr(database, 'connect'):
        with database.connect() as connection:
            yield connection.exec_driver_sql
    else:
        uri = Path(database).absolute().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
            yield connection.execute


def instance_state(database):
    try:
        with _read(database) as execute:
            marker = execute('SELECT version,status FROM acquisition_schema_migration').fetchall()
            if len(marker) != 1 or marker[0][0] != VERSION:
                return {'enabled': False, 'migration_status': 'unavailable'}
            status = marker[0][1]
            setting = execute('SELECT config_acquisition_enabled FROM settings LIMIT 1').fetchone()
            if status not in ('preserved','mapped'):
                return {'enabled': False, 'migration_status': 'needs_review'}
            return {'enabled': bool(setting and setting[0] == 1), 'migration_status': 'ready'}
    except Exception:
        return {'enabled': False, 'migration_status': 'unavailable'}


def instance_enabled(database):
    return instance_state(database)['enabled']


def account_role(database, owner_id):
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id <= 0:
        return None
    try:
        with _read(database) as execute:
            row = execute('SELECT role FROM user WHERE id=?', (owner_id,)).fetchone()
        if row is None or not isinstance(row[0], int) or row[0] & constants.ROLE_ANONYMOUS:
            return None
        return row[0]
    except Exception:
        return None


def account_allowed(database, owner_id):
    role = account_role(database, owner_id)
    return role is not None and bool(role & constants.ROLE_ACQUISITION_ACCESS)


def acquisition_offer(payload, allowed_media_types=None, *, allow_mobi=False):
    if (not isinstance(payload, dict) or payload.get('kind') != 'acquisition'
            or payload.get('media_type') not in tuple(DIRECT_FORMATS) + ('application/x-nzb','application/x-bittorrent')):
        raise AdmissionError('unsupported_offer')
    if payload['media_type'] == MOBI_MEDIA_TYPE and allow_mobi is not True:
        raise AdmissionError('unsupported_offer')
    if payload['media_type'] in ('application/x-nzb', 'application/x-bittorrent'):
        if (payload.get('transport') != ('nzb' if payload['media_type'] == 'application/x-nzb' else 'torrent')
                or not allowed_media_types or not set(allowed_media_types) & {'application/epub+zip', 'application/pdf'}):
            raise AdmissionError('format_disabled')
        return
    if allowed_media_types is not None and payload['media_type'] not in allowed_media_types:
        raise AdmissionError('format_disabled')


def create_request(repo, owner_id, *, connection_id, offer_id, idempotency_key, add_to_my_library=True):
    role = account_role(repo.engine, owner_id)
    if not instance_enabled(repo.engine) or role is None or not role & constants.ROLE_ACQUISITION_ACCESS:
        raise AdmissionError('acquisition_unavailable')
    if not isinstance(add_to_my_library, bool):
        raise AdmissionError('invalid_request')
    for value in (connection_id, offer_id, idempotency_key):
        if not isinstance(value, str) or not value.strip() or len(value)>128:
            raise AdmissionError('invalid_request')
    if not any(row.id==connection_id and row.adapter in ('opds', 'newznab') for row in repo.list_connections()):
        raise AdmissionError('unsupported_connection')
    formats=configured_media_types(repo.engine)
    connection_config = repo.connection_config(connection_id).config
    return repo.create_job(owner_id, offer_id, idempotency_key, connection_id=connection_id,
        requires_approval=not bool(role & constants.ROLE_ACQUISITION_AUTO_APPROVE),
        add_to_my_library=add_to_my_library, validate_offer=lambda payload: acquisition_offer(
            payload, formats, allow_mobi=connection_config.get('allow_mobi') is True))


def has_connections(database):
    with _read(database) as execute:
        return execute('SELECT 1 FROM acquisition_connection LIMIT 1').fetchone() is not None


def select_artifact(repo, owner_id, job_id, generation, candidate_id):
    """Current policy is read under the selection transaction's write fence."""
    if (not isinstance(generation, str) or not 1 <= len(generation) <= 64
            or not isinstance(candidate_id, str) or not re.fullmatch('[a-f0-9]{64}', candidate_id)):
        raise AdmissionError('invalid_request')

    def authorize(connection, parent, candidate):
        role = connection.exec_driver_sql('SELECT role FROM user WHERE id=?', (owner_id,)).scalar()
        setting = connection.exec_driver_sql('SELECT config_acquisition_enabled,config_upload_formats FROM settings LIMIT 1').first()
        marker = connection.exec_driver_sql('SELECT version,status FROM acquisition_schema_migration').all()
        if (type(role) is not int or role & constants.ROLE_ANONYMOUS
                or not role & constants.ROLE_ACQUISITION_ACCESS or setting is None or setting[0] != 1
                or len(marker) != 1 or marker[0][0] != VERSION or marker[0][1] not in ('preserved','mapped')):
            raise AdmissionError('acquisition_unavailable')
        if not isinstance(setting[1], str):
            raise AdmissionError('invalid_request')
        extensions = {x.strip().lower() for x in setting[1].split(',')}
        media = candidate['media_type']
        if media not in ('application/epub+zip', 'application/pdf'):
            raise AdmissionError('invalid_request')
        if '' not in extensions and media not in {_FORMAT_MEDIA_TYPES.get(x) for x in extensions}:
            raise AdmissionError('invalid_request')
        return not bool(role & constants.ROLE_ACQUISITION_AUTO_APPROVE)

    return repo.select_book(owner_id, job_id, generation, candidate_id, authorize_selection=authorize)


def account_grants(database):
    with _read(database) as execute:
        rows=execute('SELECT id,name,role FROM user ORDER BY name,id').fetchall()
    return [{'id':row[0],'name':row[1],
             'access':bool(row[2] & constants.ROLE_ACQUISITION_ACCESS),
             'auto_approve':bool(row[2] & constants.ROLE_ACQUISITION_AUTO_APPROVE)}
            for row in rows if not row[2] & constants.ROLE_ANONYMOUS]


def update_account_grants(database, actor_id, owner_id, access, auto_approve):
    if not isinstance(access,bool) or not isinstance(auto_approve,bool) or auto_approve and not access:
        raise AdmissionError('invalid_grants')
    uri=Path(database).absolute().as_uri()+'?mode=rw'
    with closing(sqlite3.connect(uri,uri=True,timeout=2)) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        actor=connection.execute('SELECT role FROM user WHERE id=?',(actor_id,)).fetchone()
        target=connection.execute('SELECT role FROM user WHERE id=?',(owner_id,)).fetchone()
        marker=connection.execute('SELECT version,status FROM acquisition_schema_migration').fetchall()
        enabled=connection.execute('SELECT config_acquisition_enabled FROM settings LIMIT 1').fetchone()
        if (not actor or not actor[0] & constants.ROLE_ADMIN or actor[0] & constants.ROLE_ANONYMOUS
                or not target or target[0] & constants.ROLE_ANONYMOUS or not enabled or enabled[0]!=1
                or len(marker)!=1 or marker[0][0]!=VERSION or marker[0][1] not in ('preserved','mapped')):
            raise AdmissionError('grants_unavailable')
        mask=target[0] & ~(constants.ROLE_ACQUISITION_ACCESS|constants.ROLE_ACQUISITION_AUTO_APPROVE)
        if access: mask|=constants.ROLE_ACQUISITION_ACCESS
        if auto_approve: mask|=constants.ROLE_ACQUISITION_AUTO_APPROVE
        connection.execute('UPDATE user SET role=? WHERE id=?',(mask,owner_id))


def job_allowed(database, job_id, owner_id):
    """Recheck direct grants or explicit approval before an external effect."""
    try:
        with _read(database) as execute:
            row=execute(
                'SELECT user.role,acquisition_job.approved_by,acquisition_job.state FROM acquisition_job '
                'JOIN user ON user.id=acquisition_job.owner_id '
                'WHERE acquisition_job.id=? AND acquisition_job.owner_id=?', (job_id,owner_id)
            ).fetchone()
        return bool(row and row[2] in ('queued','resolving','downloading','staged')
                    and not row[0] & constants.ROLE_ANONYMOUS
                    and row[0] & constants.ROLE_ACQUISITION_ACCESS
                    and (row[1] is not None or row[0] & constants.ROLE_ACQUISITION_AUTO_APPROVE))
    except Exception:
        return False


_FORMAT_MEDIA_TYPES = {extension: media for media, (_label, extension) in DIRECT_FORMATS.items()}


def configured_media_types(database):
    """Intersect implemented formats with the current configured upload policy.

    An empty upload-format string allows all formats in the existing uploader;
    direct MOBI additionally requires a catalog opt-in. Missing/unreadable config closes
    admission rather than silently broadening the administrator's policy.
    """
    try:
        with _read(database) as execute:
            row=execute('SELECT config_upload_formats FROM settings LIMIT 1').fetchone()
        if row is None or not isinstance(row[0],str): return frozenset()
        extensions={part.strip().lower() for part in row[0].split(',')}
        if '' in extensions: return frozenset(_FORMAT_MEDIA_TYPES.values())
        return frozenset(value for extension,value in _FORMAT_MEDIA_TYPES.items() if extension in extensions)
    except Exception:
        return frozenset()


def format_allowed(database, media_type):
    return media_type in configured_media_types(database)
