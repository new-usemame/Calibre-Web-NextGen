# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded read-only fingerprints for contained completed ebook candidates."""
import hashlib
import json
import os
import secrets
import unicodedata

from .contracts import DIRECT_FORMATS
from .sabnzbd import ClientError, _safe_root, open_completed_file


def fingerprint_candidates(config, books, box, job_id, checkpoint, *, max_bytes):
    root, candidates, total = _safe_root(config), [], 0
    for path, media in books:
        checkpoint()
        before = path.stat()
        with os.fdopen(open_completed_file(config, path), 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ClientError('completed_file_changed')
            value, count = hashlib.sha256(), 0
            for chunk in iter(lambda: stream.read(256*1024), b''):
                checkpoint()
                count += len(chunk); total += len(chunk)
                if count > max_bytes or total > 512*1024*1024:
                    raise ClientError('bundle_files_limit')
                value.update(chunk)
            after = os.fstat(stream.fileno())
            if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or count != before.st_size):
                raise ClientError('completed_file_changed')
        relative = path.relative_to(root).as_posix()
        sha = value.hexdigest()
        identity = box.display_identity(json.dumps(['bundle-candidate-v1', job_id, relative, sha]))
        name = ''.join(' ' if unicodedata.category(c) in ('Cc', 'Cf') else c for c in path.name).strip()[:240]
        candidates.append(dict(id=identity, name=name or DIRECT_FORMATS[media][0],
            relative_path=relative, media_type=media, size=count, sha256=sha))
    return dict(generation=secrets.token_hex(16), candidates=candidates)
