# SPDX-License-Identifier: GPL-3.0-or-later
"""SABnzbd client: upload descriptors, reconcile owned names, copy final books.

The indexer's credentials never reach SAB. Remote paths are administrator-defined
POSIX mappings. Nothing moves/deletes download-client data or extracts archives.
"""
from dataclasses import replace
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from .catalog import connection_config as transport_config, policy
from .contracts import MOBI_MEDIA_TYPE
from .http import TransportError, run_transfer
from .newznab import xml_document

MAX_COMPLETED_ENTRIES = 1000
MAX_COMPLETED_BOOKS = 20
MAX_COMPLETED_BYTES = 512 * 1024 * 1024
BOOK_SUFFIXES = ('.epub', '.pdf')


class ClientError(TransportError):
    pass


def connection_config(value):
    if not isinstance(value, dict):
        raise ClientError('invalid_connection')
    extra = {'category', 'remote_path', 'local_path'}
    if not extra <= set(value):
        raise ClientError('invalid_connection')
    config = transport_config({key: val for key, val in value.items() if key not in extra})
    if config['auth_kind'] != 'none' or not config['secret'] or len(config['secret']) > 512 or '?' in config['endpoint']:
        raise ClientError('invalid_authentication')
    category = value['category']
    if not isinstance(category, str) or not category.strip() or len(category) > 128 or ',' in category:
        raise ClientError('invalid_connection')
    for name in ('remote_path', 'local_path'):
        path = value[name]
        if not isinstance(path, str) or not path.startswith('/') or len(path) > 4096 or '\\' in path or '\x00' in path or '..' in PurePosixPath(path).parts or path == '/':
            raise ClientError('invalid_path_mapping')
    return dict(config, category=category, remote_path=value['remote_path'].rstrip('/'), local_path=value['local_path'].rstrip('/'))


def _safe_root(config):
    root = Path(config['local_path'])
    if root.is_symlink() or root.resolve() != root or not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
        raise ClientError('completed_path_unreadable')
    return root


def _completed_path(config, storage):
    root = _safe_root(config)
    if not isinstance(storage, str) or not storage.startswith('/') or '\\' in storage or '\x00' in storage or '..' in PurePosixPath(storage).parts:
        raise ClientError('unsafe_completed_path')
    try:
        relative = PurePosixPath(storage).relative_to(PurePosixPath(config['remote_path']))
    except ValueError:
        raise ClientError('unsafe_completed_path') from None
    if not relative.parts:
        raise ClientError('unsafe_completed_path')
    folder = root.joinpath(*relative.parts)
    if folder.resolve() != folder:
        raise ClientError('unsafe_completed_path')
    return root, folder


def _validate_reported_path(config, storage):
    """Validate an individually reported path without scanning its neighbors."""
    root, path = _completed_path(config, storage)
    try:
        path.relative_to(root)
        # Existing ancestors must be real directories. A missing companion is
        # harmless here; a candidate is required to exist by completed_books.
        current = root
        for part in path.relative_to(root).parts[:-1]:
            current = current / part
            try:
                info = current.lstat()
            except FileNotFoundError:
                return path
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or current.resolve() != current:
                raise ClientError('unsafe_completed_path')
        try:
            info = path.lstat()
        except FileNotFoundError:
            return path
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or path.resolve() != path:
            raise ClientError('unsafe_completed_path')
    except (OSError, ValueError):
        raise ClientError('unsafe_completed_path') from None
    return path


def _completed_media(path, config):
    suffix = path.suffix.lower()
    if suffix == '.mobi':
        return MOBI_MEDIA_TYPE if config.get('allow_mobi') is True else None
    if suffix not in BOOK_SUFFIXES:
        return None
    return 'application/epub+zip' if suffix == '.epub' else 'application/pdf'


def completed_books(config, storage, *, max_bytes=100 * 1024 * 1024, files_only=False):
    """Enumerate supported regular books from one owned completion path.

    Directory walks are limited and inspect every entry before returning, so
    an unsafe companion cannot be hidden behind an earlier usable book.
    """
    root, folder = _completed_path(config, storage)
    # A reported file is the owned result, even directly in a category folder.
    # Scanning its parent could select or reject unrelated sibling downloads.
    try:
        info = folder.lstat()
    except FileNotFoundError:
        if files_only:
            raise
        info = None
    if info is not None and stat.S_ISREG(info.st_mode):
        media = _completed_media(folder, config)
        if media is None or not 0 < info.st_size <= max_bytes:
            return ()
        return ((folder, media),)
    if info is not None and (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)):
        raise ClientError('unsafe_completed_path')
    if files_only and info is None:
        raise FileNotFoundError('Reported completed file is not yet present')
    if files_only or info is None or folder == root:
        raise ClientError('unsafe_completed_path')
    candidates = []
    count = 0
    too_many_books = False
    too_many_bytes = False
    actual_bytes = 0

    def fail_walk(error):
        raise error

    for directory, directories, files in os.walk(folder, followlinks=False, onerror=fail_walk):
        directories.sort()
        files.sort()
        for name in directories + files:
            path = Path(directory) / name
            count += 1
            if count > 1000:
                raise ClientError('completed_files_limit')
            try:
                info = path.lstat()
            except OSError:
                raise ClientError('unsafe_completed_path') from None
            if stat.S_ISLNK(info.st_mode) or path.resolve() != path:
                raise ClientError('unsafe_completed_path')
            if name in directories:
                if not stat.S_ISDIR(info.st_mode):
                    raise ClientError('unsafe_completed_path')
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ClientError('unsafe_completed_path')
            media = _completed_media(path, config)
            if media is None or not 0 < info.st_size <= max_bytes:
                continue
            candidates.append((path, media))
            too_many_books |= len(candidates) > MAX_COMPLETED_BOOKS
            actual_bytes += info.st_size
            too_many_bytes |= actual_bytes > MAX_COMPLETED_BYTES
    if too_many_books:
        raise ClientError('completed_books_limit')
    if too_many_bytes:
        raise ClientError('completed_size_limit')
    return tuple(sorted(candidates, key=lambda item: item[0].relative_to(root).as_posix()))


def completed_book(config, storage, *, max_bytes=100 * 1024 * 1024, files_only=False):
    """Choose the sole supported result, preserving the existing client API."""
    candidates = completed_books(config, storage, max_bytes=max_bytes, files_only=files_only)
    if len(candidates) != 1:
        raise ClientError('multiple_books' if candidates else 'no_usable_book')
    return candidates[0]


def open_completed_file(config, path):
    """Open every ancestor without following symlinks; reject FIFO/device races."""
    root = _safe_root(config)
    try:
        path.relative_to(root)
    except ValueError:
        raise ClientError('unsafe_completed_path') from None
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            next_directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ClientError('unsafe_completed_path')
        return fd
    except FileNotFoundError:
        raise
    except OSError:
        raise ClientError('unsafe_completed_path') from None
    finally:
        os.close(directory)


def validate_nzb(nzb):
    root = xml_document(nzb)
    if root.tag.split('}')[-1] != 'nzb' or not list(root):
        raise ClientError('invalid_nzb')


class SABClient:
    def __init__(self, config, *, transfer=run_transfer):
        self.config, self.transfer = config, transfer

    def call(self, mode, *, checkpoint=lambda: None, upload=None, **params):
        form = dict(params, mode=mode, output='json', apikey=self.config['secret'])
        try:
            document = self.transfer(self.config['endpoint'], replace(policy(self.config), max_redirects=0),
                form=form, upload=upload, max_bytes=2 * 1024 * 1024, checkpoint=checkpoint)
        except TransportError as error:
            raise ClientError(error.code, retry_after=error.retry_after) from None
        try:
            result = json.loads(document.body)
        except (ValueError, UnicodeError):
            raise ClientError('invalid_client_response') from None
        if not isinstance(result, dict):
            raise ClientError('invalid_client_response')
        if result.get('error') or result.get('status') is False:
            error = result.get('error', '')
            raise ClientError('needs_auth' if isinstance(error, str) and 'key' in error.lower() else 'client_error')
        return result

    def probe(self):
        cats = self.call('get_cats').get('categories')
        if not isinstance(cats, list) or self.config['category'] not in cats:
            raise ClientError('client_category_unavailable')
        # Queue/history require the full API key, not the restricted NZB key.
        self._slots('queue', limit='1')
        self._slots('history', limit='1')
        status = self.call('fullstatus').get('status', {})
        complete = status.get('completedir') if isinstance(status, dict) else None
        categories = self.call('get_config', section='categories', keyword=self.config['category']).get('config', {}).get('categories', [])
        category = next((row for row in categories if isinstance(row, dict) and row.get('name') == self.config['category']), None)
        if not isinstance(complete, str) or not complete.startswith('/') or category is None or not isinstance(category.get('dir'), str):
            raise ClientError('client_path_mapping_unverified')
        directory = PurePosixPath(category['dir'])
        complete_path = directory if directory.is_absolute() else PurePosixPath(complete) / directory
        if '..' in complete_path.parts:
            raise ClientError('invalid_path_mapping')
        try:
            complete_path.relative_to(PurePosixPath(self.config['remote_path']))
        except ValueError:
            raise ClientError('client_path_mapping_mismatch') from None
        _safe_root(self.config)
        return {'title': 'SABnzbd', 'protocol': 'sabnzbd', 'browse': False,
            'search_advertised': False, 'direct_download_advertised': False,
            'category': self.config['category'], 'completed_path_readable': True}

    def _slots(self, mode, *, checkpoint=lambda: None, **params):
        result = self.call(mode, checkpoint=checkpoint, **params).get(mode)
        if not isinstance(result, dict) or not isinstance(result.get('slots'), list) or any(not isinstance(row, dict) for row in result['slots']):
            raise ClientError('invalid_client_response')
        return result['slots']

    def find(self, name, external_id=None, *, checkpoint=lambda: None):
        # Filter server-side then verify exact ownership locally. A partial name
        # match or a history entry from another tool is never import authority.
        params = {'nzo_ids': external_id} if external_id else {'search': name}
        rows = []
        for mode in ('queue', 'history'):
            rows.extend(self._slots(mode, checkpoint=checkpoint, limit='100', **params))
        matches = [row for row in rows if row.get('nzo_id') == external_id] if external_id else [row for row in rows if row.get('filename', row.get('name', '')) in (name, name + '.nzb')]
        if any(row.get('cat', row.get('category')) != self.config['category'] for row in matches):
            raise ClientError('client_job_mismatch')
        identities = {row.get('nzo_id') for row in matches}
        if len(identities) > 1:
            raise ClientError('submission_ambiguous')
        if not matches:
            return None
        return matches[-1]

    def submit(self, name, nzb, *, checkpoint=lambda: None):
        validate_nzb(nzb)
        result = self.call('addfile', checkpoint=checkpoint, upload=(name + '.nzb', nzb),
            nzbname=name, cat=self.config['category'], pp='2', script='None', priority='0')
        identifiers = result.get('nzo_ids')
        if (result.get('status') is not True or not isinstance(identifiers, list)
                or len(identifiers) != 1 or not isinstance(identifiers[0], str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identifiers[0])):
            raise ClientError('submission_ambiguous')
        return identifiers[0]
