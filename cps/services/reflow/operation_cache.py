"""Atomic shared request claims: concurrent jobs never purchase one request twice.

A pending claim has no expiry: elapsed time is not proof that a provider did not
bill. Claims carry no user/job identity or previous user's cost in public results.
"""
import json
import os
import uuid
from pathlib import Path


class PriorRequestPending(Exception):
    pass


class OperationCache:
    def __init__(self, directory):
        self.directory = Path(directory) / 'source-operations-v1'

    def _path(self, key):
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('invalid request identity')
        return self.directory / key[:2] / (key + '.json')

    def claim(self, key):
        path = self._path(key);path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        try:
            with path.open('x', encoding='utf-8') as handle:
                json.dump({'state':'pending','claim':token,'request_sha256':key}, handle)
                handle.flush();os.fsync(handle.fileno())
            return token, None
        except FileExistsError:
            try: saved = json.loads(path.read_text(encoding='utf-8'))
            except (ValueError, OSError): saved = {}
            if saved.get('request_sha256') == key and saved.get('state') in ('answered','rejected'):
                return None, saved
            raise PriorRequestPending('An earlier matching request is in progress or unresolved; no new request was sent.')

    def finish(self, key, token, response=None, rejected=False, failure_code=None):
        path = self._path(key)
        current = json.loads(path.read_text(encoding='utf-8'))
        if current.get('claim') != token: raise ValueError('request claim ownership changed')
        temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as handle:
                json.dump({'state':'rejected' if rejected else 'answered',
                           'request_sha256':key,'response':response,
                           **({'failure_code':failure_code} if failure_code else {})}, handle)
                handle.flush();os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists(): temporary.unlink()

    def release_unsent(self, key, token):
        path = self._path(key)
        if json.loads(path.read_text(encoding='utf-8')).get('claim') != token:
            raise ValueError('request claim ownership changed')
        path.unlink()
