# SPDX-License-Identifier: GPL-3.0-or-later
"""Released download-client adapters sharing the SAB durable/import contract."""
import base64
from dataclasses import replace
import json
from pathlib import PurePosixPath
import re
import stat
from urllib.parse import urlencode, urlsplit, urlunsplit

from .catalog import connection_config as transport_config, policy
from .http import TransportError, run_transfer
from .sabnzbd import (MAX_COMPLETED_BOOKS, MAX_COMPLETED_BYTES, MAX_COMPLETED_ENTRIES,
    BOOK_SUFFIXES, SABClient, ClientError, _safe_root, _validate_reported_path,
    completed_books, validate_nzb)
from .torrent import validate_torrent, magnet_identities, torrent_identities

CLIENT_KINDS = ('sabnzbd', 'nzbget', 'qbittorrent', 'transmission')
USENET_KINDS = ('sabnzbd', 'nzbget')
TORRENT_KINDS = ('qbittorrent', 'transmission')


def connection_config(adapter, value):
    if adapter not in CLIENT_KINDS or not isinstance(value, dict): raise ClientError('invalid_connection')
    if adapter == 'sabnzbd':
        from .sabnzbd import connection_config as sab_config
        return sab_config(value)
    extra = {'category', 'remote_path', 'local_path'}
    if not extra <= set(value): raise ClientError('invalid_connection')
    config = transport_config({k: v for k, v in value.items() if k not in extra})
    if config['auth_kind'] not in (('none', 'basic') if adapter == 'transmission' else ('basic',)) or '?' in config['endpoint']:
        raise ClientError('invalid_authentication')
    if adapter != 'transmission' and not config['username']: raise ClientError('invalid_authentication')
    category = value['category']
    if not isinstance(category, str) or not category.strip() or len(category) > 128 or any(c in category for c in ',\r\n\x00'):
        raise ClientError('invalid_connection')
    for name in ('remote_path', 'local_path'):
        path = value[name]
        if not isinstance(path, str) or not path.startswith('/') or path == '/' or len(path) > 4096 or any(c in path for c in '\\\x00') or '..' in PurePosixPath(path).parts:
            raise ClientError('invalid_path_mapping')
    if config['auth_kind'] == 'none': config.update(username='', secret='')
    # All custom session credentials are scoped to this endpoint alone.
    config['credential_origins'] = [config['endpoint']]
    return dict(config, category=category, remote_path=value['remote_path'].rstrip('/'), local_path=value['local_path'].rstrip('/'))


def parsed(document):
    try: return json.loads(document.body)
    except (ValueError, UnicodeError): raise ClientError('invalid_client_response') from None


def rows(value):
    if not isinstance(value, list) or len(value) > 10000 or any(not isinstance(row, dict) for row in value):
        raise ClientError('invalid_client_response')
    return value


def path_matches(config, path):
    if not isinstance(path, str) or not path.startswith('/') or '\\' in path or '..' in PurePosixPath(path).parts: raise ClientError('client_path_mapping_mismatch')
    try: PurePosixPath(path).relative_to(PurePosixPath(config['remote_path']))
    except ValueError: raise ClientError('client_path_mapping_mismatch') from None


def torrent_books(config, directory, files, *, max_bytes=100*1024*1024):
    """Enumerate supported files from this torrent's reported file list only."""
    if not isinstance(directory, str) or not directory.startswith('/') or '\\' in directory or '..' in PurePosixPath(directory).parts:
        raise ClientError('unsafe_completed_path')
    reported_files = rows(files)
    if len(reported_files) > MAX_COMPLETED_ENTRIES:
        raise ClientError('completed_files_limit')
    supported_suffixes = BOOK_SUFFIXES + (('.mobi',) if config.get('allow_mobi') is True else ())
    by_name = {}
    for row in reported_files:
        name = row.get('name')
        if not isinstance(name, str) or not name or any(c in name for c in '\\\x00') or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/')):
            raise ClientError('unsafe_completed_path')
        remote = str(PurePosixPath(directory) / name)
        # Check containment of every file, including non-book companions.
        path_matches(config, remote)
        _validate_reported_path(config, remote)
        if PurePosixPath(name).suffix.lower() in supported_suffixes:
            # A duplicated torrent file record still identifies only one local
            # candidate. Conflicting advertised sizes make that identity unsafe.
            advertised = row.get('size', row.get('length'))
            if advertised is not None and (type(advertised) is not int or advertised < 0):
                raise ClientError('invalid_client_response')
            if name in by_name and by_name[name] != advertised:
                raise ClientError('invalid_client_response')
            by_name[name] = advertised

    candidates = []
    advertised_bytes = 0
    for name, advertised in by_name.items():
        remote = str(PurePosixPath(directory) / name)
        if advertised is not None:
            advertised_bytes += advertised
        candidates.extend(completed_books(config, remote, max_bytes=max_bytes, files_only=True))
    if len(candidates) > MAX_COMPLETED_BOOKS:
        raise ClientError('completed_books_limit')
    actual_bytes = 0
    for path, _ in candidates:
        try:
            info = path.lstat()
        except OSError:
            raise ClientError('unsafe_completed_path') from None
        if not stat.S_ISREG(info.st_mode) or path.resolve() != path:
            raise ClientError('unsafe_completed_path')
        actual_bytes += info.st_size
    if advertised_bytes > MAX_COMPLETED_BYTES or actual_bytes > MAX_COMPLETED_BYTES:
        raise ClientError('completed_size_limit')
    return tuple(sorted(candidates, key=lambda item: item[0].as_posix()))


def torrent_book(config, directory, files, *, max_bytes=100*1024*1024):
    """Choose the sole supported torrent result for existing callers."""
    candidates = torrent_books(config, directory, files, max_bytes=max_bytes)
    if len(candidates) != 1:
        raise ClientError('multiple_books' if candidates else 'no_usable_book')
    return candidates[0]


class NZBGetClient:
    def __init__(self, config, *, transfer=run_transfer): self.config, self.transfer = config, transfer

    def call(self, method, params=(), *, checkpoint=lambda: None):
        document = self.transfer(self.config['endpoint'], replace(policy(self.config), max_redirects=0),
            body=json.dumps({'method': method, 'params': list(params), 'id': 1}).encode(),
            headers={'Content-Type': 'application/json'},
            # Released NZBGet has no queue/history filter or pagination.
            max_bytes=(32 if method in ('listgroups', 'history') else 2)*1024*1024, checkpoint=checkpoint)
        result = parsed(document)
        if not isinstance(result, dict): raise ClientError('invalid_client_response')
        if result.get('error'):
            error = result['error']; code = error.get('code') if isinstance(error, dict) else None
            raise ClientError('needs_auth' if code in (401, 403, -32604) else 'client_error')
        if 'result' not in result: raise ClientError('invalid_client_response')
        return result['result']

    def probe(self):
        version = self.call('version')
        if not isinstance(version, str) or not re.match(r'^(21|22|23|24|25|26)\.', version): raise ClientError('unsupported_client_version')
        self.call('listgroups', [0])
        settings = {r.get('Name'): r.get('Value') for r in rows(self.call('config'))}
        prefix = next((key[:-4] for key, value in settings.items() if isinstance(key, str) and re.fullmatch(r'Category[0-9]+\.Name', key) and value == self.config['category']), None)
        if prefix is None: raise ClientError('client_category_unavailable')
        destination = settings.get(prefix+'DestDir') or settings.get('DestDir')
        path_matches(self.config, destination); _safe_root(self.config)
        return {'title': 'NZBGet', 'protocol': 'nzbget', 'api_version': version, 'browse': False, 'completed_path_readable': True}

    def submit(self, name, descriptor, *, checkpoint=lambda: None):
        validate_nzb(descriptor)
        version = self.call('version', checkpoint=checkpoint)
        if not isinstance(version, str) or not re.match(r'^(21|22|23|24|25|26)\.', version): raise ClientError('unsupported_client_version')
        params = [name+'.nzb', base64.b64encode(descriptor).decode(), self.config['category'], 0, False, False, name, 0, 'ALL']
        if tuple(map(int, re.match(r'(\d+)\.(\d+)', version).groups())) >= (25,2): params.append(False)
        result = self.call('append', params + [[]], checkpoint=checkpoint)
        if type(result) is not int or result <= 0: raise ClientError('client_error')
        return str(result)

    def find(self, name, external_id=None, *, checkpoint=lambda: None):
        queue = rows(self.call('listgroups', [0], checkpoint=checkpoint))
        # A known queued job cannot be in final history yet. Avoid retrieving
        # a busy server's entire retained history on every active poll.
        queued = external_id and any(str(row.get('NZBID')) == external_id for row in queue)
        history = [] if queued else rows(self.call('history', [False], checkpoint=checkpoint))
        matches = [(row, final) for values, final in ((queue, False), (history, True)) for row in values
            if str(row.get('NZBID')) == external_id or external_id is None and row.get('NZBName', row.get('Name')) == name]
        if len({row.get('NZBID') for row, _ in matches}) > 1: raise ClientError('submission_ambiguous')
        if not matches: return None
        row, final = matches[-1]
        if row.get('Category') != self.config['category'] or row.get('NZBName', row.get('Name')) != name: raise ClientError('client_job_mismatch')
        status = row.get('Status', '')
        return {'nzo_id': str(row['NZBID']), 'status': 'Completed' if final and status in ('SUCCESS/ALL', 'SUCCESS/UNPACK', 'SUCCESS/HEALTH', 'SUCCESS/PAR', 'WARNING/SCRIPT') else 'Failed' if final else 'Downloading',
            'storage': row.get('FinalDir') or row.get('DestDir')}


class QBitClient:
    def __init__(self, config, *, transfer=run_transfer):
        self.config, self.transfer, self.cookie = config, transfer, None
        self._prepared_submission = None
        self._expected_magnet_v2 = None

    def url(self, method, **query):
        parts = urlsplit(self.config['endpoint']); path = parts.path.rstrip('/')
        if path.endswith('/api/v2'): path = path[:-7]
        return urlunsplit((parts.scheme, parts.netloc, path+'/api/v2/'+method, urlencode(query), ''))

    def login(self, checkpoint):
        cfg = dict(self.config, auth_kind='none')
        result = self.transfer(self.url('auth/login'), replace(policy(cfg), max_redirects=0),
            form={'username': self.config['username'], 'password': self.config['secret']},
            headers={'Referer': self.config['endpoint']}, accepted_statuses=(200,204), accept_empty=True, checkpoint=checkpoint, max_bytes=4096)
        cookie = result.headers.get('set-cookie', '')
        sid = re.search(r'(?:^|;\s*)((?:SID|QBT_SID_[0-9]{1,5})=[A-Za-z0-9_+/=-]{1,256})(?:;|$)', cookie)
        if (result.status == 200 and result.body.strip() != b'Ok.') or sid is None: raise ClientError('needs_auth')
        self.cookie = sid.group(1)

    def call(self, method, *, checkpoint=lambda: None, form=None, upload=None, **query):
        if self.cookie is None: self.login(checkpoint)
        for attempt in range(2):
            cfg = dict(self.config, auth_kind='none')
            result = self.transfer(self.url(method, **query), replace(policy(cfg), max_redirects=0),
                form=form, upload=upload, upload_field='torrents', headers={'Cookie': self.cookie, 'Referer': self.config['endpoint']},
                accepted_statuses=(200,204,401,403,400,409,415), accept_empty=True, checkpoint=checkpoint, max_bytes=2*1024*1024)
            if result.status in (401,403):
                self.cookie = None
                if attempt == 0: self.login(checkpoint); continue
                raise ClientError('needs_auth')
            if result.status not in (200,204): raise ClientError('client_error')
            return result

    def probe(self):
        version = self.call('app/webapiVersion').body.decode('ascii', errors='replace').strip()
        if not re.fullmatch(r'2\.[0-9]{1,2}\.[0-9]{1,3}', version) or not (2,8,0) <= tuple(map(int,version.split('.'))) <= (2,15,1): raise ClientError('unsupported_client_version')
        categories = parsed(self.call('torrents/categories'))
        if not isinstance(categories, dict) or self.config['category'] not in categories: raise ClientError('client_category_unavailable')
        category = categories[self.config['category']]
        destination = category.get('savePath') if isinstance(category, dict) else None
        if not destination:
            preferences = parsed(self.call('app/preferences'))
            if not isinstance(preferences, dict): raise ClientError('invalid_client_response')
            destination = preferences.get('save_path')
        path_matches(self.config, destination); _safe_root(self.config)
        rows(parsed(self.call('torrents/info', limit='1')))
        return {'title': 'qBittorrent', 'protocol': 'qbittorrent', 'api_version': version, 'browse': False, 'completed_path_readable': True}

    def prepare_submission(self, descriptor, *, checkpoint=lambda: None, refresh=False):
        """Resolve read-only engine/API facts before the worker's durable POST fence."""
        prepared = self._prepared_submission
        if not refresh and prepared is not None and type(descriptor) is prepared[0] and descriptor == prepared[1]:
            return prepared[2]
        hashes = magnet_identities(descriptor) if isinstance(descriptor, str) else torrent_identities(descriptor)
        identity = hashes.v1
        if hashes.v2 is not None:
            build = parsed(self.call('app/buildInfo', checkpoint=checkpoint))
            engine = build.get('libtorrent') if isinstance(build, dict) else None
            if not isinstance(engine, str) or not re.fullmatch(r'(?:1\.2|2\.0)\.[0-9]{1,3}(?:\.[0-9]{1,3})?', engine):
                raise ClientError('unsupported_client_version')
            # Released LT2 uses get_best(): hybrid IDs are truncated v2.
            # LT1 uses v1. Resolve this before the fence or any submission.
            if engine.startswith('2.0.'):
                identity = hashes.v2[:40]
            elif identity is None:
                raise ClientError('unsupported_client_version')
            if isinstance(descriptor, str):
                # has_metadata first appears in released WebUI API 2.11.2.
                version = self.call('app/webapiVersion', checkpoint=checkpoint).body.decode('ascii', errors='replace').strip()
                if not re.fullmatch(r'2\.[0-9]{1,2}\.[0-9]{1,3}', version) or not (2,11,2) <= tuple(map(int, version.split('.'))) <= (2,15,1):
                    raise ClientError('unsupported_client_version')
        self._expected_magnet_v2 = hashes.v2 if isinstance(descriptor, str) else None
        self._prepared_submission = (type(descriptor), descriptor, identity)
        return identity

    def submit_fenced(self, descriptor, before_submit, *, checkpoint=lambda: None):
        return self.submit(None, descriptor, checkpoint=checkpoint, before_submit=before_submit)

    def submit(self, name, descriptor, *, checkpoint=lambda: None, before_submit=None):
        identity = self.prepare_submission(descriptor, checkpoint=checkpoint)
        # A pre-existing unrelated torrent cannot be adopted or have its policy changed.
        if rows(parsed(self.call('torrents/info', hashes=identity, checkpoint=checkpoint))): raise ClientError('torrent_already_exists')
        if self._expected_magnet_v2 is not None:
            # Recheck after collision lookup rather than trust cached engine/API
            # facts across a daemon restart. The external API is not atomic.
            self.prepare_submission(descriptor, checkpoint=checkpoint, refresh=True)
        # Read-only collision checks precede the worker's durable POST fence.
        # A shared attempt adopted at this boundary must never be submitted again.
        if before_submit is not None:
            name = before_submit()
            if name is None: return None
        form = {'category': self.config['category'], 'savepath': self.config['remote_path'], 'tags': name, 'autoTMM': 'false'}
        if isinstance(descriptor, str): form['urls'] = descriptor
        result = self.call('torrents/add', form=form, upload=None if isinstance(descriptor, str) else (name+'.torrent', descriptor), checkpoint=checkpoint)
        if result.body.strip() == b'Fails.': raise ClientError('client_error')
        if result.body.strip() != b'Ok.':
            try: acknowledgement = json.loads(result.body)
            except (ValueError, UnicodeError): raise ClientError('submission_ambiguous') from None
            if (not isinstance(acknowledgement, dict) or acknowledgement.get('success_count') != 1
                    or acknowledgement.get('failure_count') != 0 or acknowledgement.get('pending_count') != 0
                    or acknowledgement.get('added_torrent_ids') != [identity]):
                raise ClientError('submission_ambiguous')
        # The metainfo/magnet establishes this ID. Persist it before polling.
        # A failed post-accept login must never erase the durable submission fence.
        return identity

    def find(self, name, external_id=None, *, checkpoint=lambda: None):
        query = {'hashes': external_id} if external_id else {'tag': name, 'category': self.config['category']}
        matches = rows(parsed(self.call('torrents/info', checkpoint=checkpoint, **query)))
        matches = [row for row in matches if row.get('hash') == external_id] if external_id else [row for row in matches if name in str(row.get('tags', '')).split(', ')]
        if len(matches) > 1: raise ClientError('submission_ambiguous')
        if not matches: return None
        row = matches[0]
        if row.get('category') != self.config['category'] or name not in [v.strip() for v in str(row.get('tags', '')).split(',')]: raise ClientError('client_job_mismatch')
        identity = row.get('hash')
        if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{40}', identity): raise ClientError('invalid_client_response')
        expected = self._expected_magnet_v2
        if expected is not None:
            if identity != expected[:40]:
                raise ClientError('client_job_mismatch')
            properties = parsed(self.call('torrents/properties', hash=identity, checkpoint=checkpoint))
            if not isinstance(properties, dict):
                raise ClientError('invalid_client_response')
            # A pending torrent already knows its topic; that is not metadata.
            # Fail closed before files, including on prefix-collision recovery.
            has_metadata = properties.get('has_metadata')
            if 'has_metadata' in properties and type(has_metadata) is not bool:
                raise ClientError('invalid_client_response')
            property_id = properties.get('hash')
            if not isinstance(property_id, str) or not re.fullmatch('[0-9a-f]{40}', property_id):
                raise ClientError('invalid_client_response')
            if property_id != identity:
                raise ClientError('client_job_mismatch')
            observed = properties.get('infohash_v2')
            if observed is not None and observed != '' and (not isinstance(observed, str)
                    or not re.fullmatch('[0-9a-fA-F]{64}', observed)):
                raise ClientError('invalid_client_response')
            if observed and observed.lower() != expected:
                raise ClientError('client_job_mismatch')
            if has_metadata is not True or not observed:
                return {'nzo_id': identity, 'status': 'Downloading'}
        files = rows(parsed(self.call('torrents/files', hash=identity, checkpoint=checkpoint)))
        complete = row.get('state') in ('uploading', 'stalledUP', 'queuedUP', 'pausedUP', 'stoppedUP', 'forcedUP') and row.get('progress') == 1 and row.get('amount_left') == 0 and files and all(f.get('progress') == 1 for f in files)
        failed = row.get('state') in ('error', 'missingFiles')
        return {'nzo_id': identity, 'status': 'Failed' if failed else 'Completed' if complete else 'Downloading', 'directory': row.get('save_path'), 'files': files}


class TransmissionClient:
    FIELDS = ['hashString', 'labels', 'downloadDir', 'status', 'error', 'percentDone', 'leftUntilDone', 'metadataPercentComplete', 'files']
    def __init__(self, config, *, transfer=run_transfer): self.config, self.transfer, self.session_id = config, transfer, None

    def call(self, method, arguments=None, *, checkpoint=lambda: None):
        body = json.dumps({'method': method, 'arguments': arguments or {}}).encode()
        for attempt in range(2):
            headers = {'Content-Type': 'application/json'}
            if self.session_id: headers['X-Transmission-Session-Id'] = self.session_id
            response = self.transfer(self.config['endpoint'], replace(policy(self.config), max_redirects=0),
                body=body, headers=headers, accepted_statuses=(200,409), accept_empty=True, checkpoint=checkpoint,
                max_bytes=(8 if method == 'torrent-get' and 'ids' not in (arguments or {}) else 2)*1024*1024)
            if response.status == 409:
                session = response.headers.get('x-transmission-session-id', '')
                if attempt or not re.fullmatch('[A-Za-z0-9_-]{1,256}', session): raise ClientError('needs_auth')
                self.session_id = session; continue
            result = parsed(response)
            if not isinstance(result, dict) or result.get('result') != 'success' or not isinstance(result.get('arguments'), dict): raise ClientError('client_error')
            return result['arguments']

    def probe(self):
        result = self.call('session-get')
        if result.get('rpc-version') not in (17, 18, 19): raise ClientError('unsupported_client_version')
        version = result['rpc-version']
        self.call('torrent-get', {'fields': ['hashString']})
        result = self.call('free-space', {'path': self.config['remote_path']})
        if result.get('path') != self.config['remote_path'] or type(result.get('size-bytes')) is not int or result['size-bytes'] < 0: raise ClientError('client_path_mapping_unverified')
        _safe_root(self.config)
        return {'title': 'Transmission', 'protocol': 'transmission', 'api_version': version, 'browse': False, 'completed_path_readable': True}

    def prepare_submission(self, descriptor, *, checkpoint=lambda: None):
        if isinstance(descriptor, str):
            identity = magnet_identities(descriptor).v1
            if identity is None:
                raise ClientError('unsupported_client_version')
            return identity
        identity = torrent_identities(descriptor).v1
        # The supported Transmission releases cannot ingest a pure-v2 file.
        # Refuse before the worker records an irreversible submission attempt.
        if identity is None:
            raise ClientError('unsupported_client_version')
        return identity

    def submit_fenced(self, descriptor, before_submit, *, checkpoint=lambda: None):
        return self.submit(None, descriptor, checkpoint=checkpoint, before_submit=before_submit)

    def submit(self, name, descriptor, *, checkpoint=lambda: None, before_submit=None):
        identity = self.prepare_submission(descriptor, checkpoint=checkpoint)
        existing = self.call('torrent-get', {'ids': [identity], 'fields': ['hashString']}, checkpoint=checkpoint)
        if rows(existing.get('torrents')): raise ClientError('torrent_already_exists')
        if before_submit is not None:
            name = before_submit()
            if name is None: return None
        arguments = {'download-dir': self.config['remote_path'], 'labels': [self.config['category'], name], 'paused': False}
        arguments['filename' if isinstance(descriptor, str) else 'metainfo'] = descriptor if isinstance(descriptor, str) else base64.b64encode(descriptor).decode()
        result = self.call('torrent-add', arguments, checkpoint=checkpoint)
        added = result.get('torrent-added')
        if not isinstance(added, dict) or added.get('hashString', '').lower() != identity: raise ClientError('submission_ambiguous')
        return identity

    def find(self, name, external_id=None, *, checkpoint=lambda: None):
        if not external_id:
            # Discover ownership cheaply; fetch file details for one owned hash.
            values = rows(self.call('torrent-get', {'fields': ['hashString', 'labels']}, checkpoint=checkpoint).get('torrents'))
            matches = [row for row in values if isinstance(row.get('labels'), list) and name in row['labels']]
            if len(matches) > 1: raise ClientError('submission_ambiguous')
            if not matches: return None
            external_id = matches[0].get('hashString')
            if not isinstance(external_id, str) or not re.fullmatch('[0-9a-f]{40}', external_id): raise ClientError('invalid_client_response')
        values = rows(self.call('torrent-get', {'fields': self.FIELDS, 'ids': [external_id]}, checkpoint=checkpoint).get('torrents'))
        matches = [row for row in values if row.get('hashString') == external_id]
        if len(matches) > 1: raise ClientError('submission_ambiguous')
        if not matches: return None
        row = matches[0]
        if not isinstance(row.get('labels'), list) or name not in row['labels'] or self.config['category'] not in row['labels']: raise ClientError('client_job_mismatch')
        files = rows(row.get('files'))
        complete = row.get('status') in (0, 5, 6) and row.get('metadataPercentComplete') == 1 and row.get('percentDone') == 1 and row.get('leftUntilDone') == 0 and files and all(type(f.get('length')) is int and f['length'] >= 0 and f.get('bytesCompleted') == f['length'] for f in files)
        return {'nzo_id': row.get('hashString'), 'status': 'Failed' if row.get('error') == 3 else 'Completed' if complete else 'Downloading', 'directory': row.get('downloadDir'), 'files': files}


CLIENTS = {'sabnzbd': SABClient, 'nzbget': NZBGetClient, 'qbittorrent': QBitClient, 'transmission': TransmissionClient}
