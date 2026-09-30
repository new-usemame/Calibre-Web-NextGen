# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded v1 torrent metainfo and magnets; never fetch a client-supplied URL."""
import base64
import hashlib
import re
from urllib.parse import parse_qsl, urlsplit, unquote

from .http import TransportError


def safe_name(value):
    if not isinstance(value, str) or not value or value in ('.', '..') or any(c in value for c in '/\\\x00') or len(value) > 255:
        raise TransportError('unsafe_torrent')
    return value


def tracker_origin(value):
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise TransportError('untrusted_torrent_tracker')
    try:
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https', 'udp') or not parts.hostname or parts.username is not None or parts.password is not None or '\\' in parts.netloc:
            raise ValueError()
        port = parts.port or (443 if parts.scheme == 'https' else 80 if parts.scheme == 'http' else 0)
        if not port: raise ValueError()
        return parts.scheme, parts.hostname.encode('idna').decode('ascii').lower(), port
    except (ValueError, UnicodeError): raise TransportError('untrusted_torrent_tracker') from None


def tracker(value, allowed, secret):
    address = tracker_origin(value)
    if allowed is not None and address not in {tracker_origin(v) for v in allowed}:
        raise TransportError('untrusted_torrent_tracker')
    if secret:
        decoded = value
        for _ in range(8):
            if secret in decoded: raise TransportError('untrusted_torrent_tracker')
            next_value = unquote(decoded)
            if next_value == decoded: break
            decoded = next_value
        else: raise TransportError('untrusted_torrent_tracker')


def validate_magnet(value, *, tracker_origins=None, secret=None):
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) <= 32 for c in value):
        raise TransportError('invalid_magnet')
    parsed = urlsplit(value)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.scheme != 'magnet' or parsed.netloc or parsed.path or parsed.fragment or len(pairs) > 32 or any(k not in ('xt', 'dn', 'tr') for k, _ in pairs):
        raise TransportError('invalid_magnet')
    for key, url in pairs:
        if key == 'tr': tracker(url, tracker_origins, secret)
    hashes = [v[9:] for k, v in pairs if k == 'xt' and v.startswith('urn:btih:')]
    if len(hashes) != 1 or sum(k == 'xt' for k, _ in pairs) != 1:
        raise TransportError('invalid_magnet')
    value_hash = hashes[0]
    if re.fullmatch('[0-9a-fA-F]{40}', value_hash): return value_hash.lower()
    if re.fullmatch('[A-Z2-7a-z]{32}', value_hash): return base64.b32decode(value_hash.upper()).hex()
    raise TransportError('invalid_magnet')


def validate_torrent(raw, *, tracker_origins=None, secret=None):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 512 * 1024:
        raise TransportError('invalid_torrent')
    pos = 0; nodes = 0; info_bytes = None
    def read(depth=0):
        nonlocal pos, nodes, info_bytes
        nodes += 1
        if depth > 16 or nodes > 16000 or pos >= len(raw): raise TransportError('invalid_torrent')
        kind = raw[pos:pos+1]
        if kind == b'i':
            end = raw.find(b'e', pos+1); text = raw[pos+1:end]
            if end < 0 or not re.fullmatch(b'0|-?[1-9][0-9]{0,18}', text): raise TransportError('invalid_torrent')
            pos = end+1; return int(text)
        if kind in (b'l', b'd'):
            pos += 1; result = [] if kind == b'l' else {}; previous = None
            while pos < len(raw) and raw[pos:pos+1] != b'e':
                if kind == b'l': result.append(read(depth+1))
                else:
                    key = read(depth+1)
                    if not isinstance(key, bytes) or previous is not None and key <= previous: raise TransportError('invalid_torrent')
                    previous = key; start = pos; result[key] = read(depth+1)
                    if depth == 0 and key == b'info': info_bytes = raw[start:pos]
            if pos >= len(raw): raise TransportError('invalid_torrent')
            pos += 1; return result
        end = raw.find(b':', pos); text = raw[pos:end]
        if end < 0 or not re.fullmatch(b'0|[1-9][0-9]{0,6}', text): raise TransportError('invalid_torrent')
        size = int(text); pos = end+1
        if pos+size > len(raw): raise TransportError('invalid_torrent')
        value = raw[pos:pos+size]; pos += size; return value
    try:
        data = read(); info = data[b'info']
        if pos != len(raw) or not isinstance(info, dict) or not info_bytes or set(data) - {b'info', b'announce', b'announce-list', b'comment', b'comment.utf-8', b'created by', b'creation date', b'encoding'}:
            raise TransportError('invalid_torrent')
        # Only v1 file semantics may reach a client. A hybrid's v2 file tree
        # or single-file symlink fields must not bypass the paths checked below.
        if set(info) - {b'name', b'name.utf-8', b'pieces', b'piece length', b'length', b'files', b'private', b'source', b'md5sum'}:
            raise TransportError('invalid_torrent')
        urls = [data[b'announce']] if b'announce' in data else []
        tiers = data.get(b'announce-list', [])
        if not isinstance(tiers, list) or len(tiers) > 32: raise TransportError('invalid_torrent')
        for tier in tiers:
            if not isinstance(tier, list) or len(tier) > 32: raise TransportError('invalid_torrent')
            urls.extend(tier)
        if len(urls) > 64: raise TransportError('invalid_torrent')
        for url in urls: tracker(url.decode('utf-8'), tracker_origins, secret)
        safe_name(info[b'name'].decode('utf-8'))
        if b'name.utf-8' in info: safe_name(info[b'name.utf-8'].decode('utf-8'))
        if not isinstance(info.get(b'pieces'), bytes) or len(info[b'pieces']) % 20 or not info[b'pieces'] or type(info.get(b'piece length')) is not int or info[b'piece length'] <= 0:
            raise TransportError('invalid_torrent')
        files = info.get(b'files')
        if files is None:
            if type(info.get(b'length')) is not int or info[b'length'] <= 0: raise TransportError('invalid_torrent')
        else:
            if not isinstance(files, list) or not 0 < len(files) <= 1000 or b'length' in info: raise TransportError('invalid_torrent')
            paths = set()
            for row in files:
                if (not isinstance(row, dict) or set(row) - {b'length', b'path', b'path.utf-8', b'md5sum', b'attr'}
                        or type(row.get(b'length')) is not int or row[b'length'] < 0
                        or b'attr' in row and (not isinstance(row[b'attr'], bytes) or b'l' in row[b'attr'])):
                    raise TransportError('invalid_torrent')
                for key in (b'path', b'path.utf-8'):
                    if key not in row and key != b'path': continue
                    parts = row[key]
                    if not isinstance(parts, list) or not parts: raise TransportError('invalid_torrent')
                    path = tuple(safe_name(v.decode('utf-8')) for v in parts)
                    if key == b'path':
                        if path in paths: raise TransportError('invalid_torrent')
                        paths.add(path)
        return hashlib.sha1(info_bytes).hexdigest()
    except TransportError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, AttributeError):
        raise TransportError('invalid_torrent') from None
