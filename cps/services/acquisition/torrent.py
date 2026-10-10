# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded v1/v2 metainfo and exact-topic magnets; never fetch a supplied URL."""
import base64
import hashlib
import re
import unicodedata
from typing import NamedTuple
from urllib.parse import parse_qsl, urlsplit

from .http import TransportError, query_secret_present


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
    if secret and query_secret_present(value, (secret,)):
        raise TransportError('untrusted_torrent_tracker')


def magnet_identities(value, *, tracker_origins=None, secret=None):
    """Keep original full digests; topic co-presence is not hybrid pair proof."""
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise TransportError('invalid_magnet')
    parsed = urlsplit(value)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.scheme != 'magnet' or parsed.netloc or parsed.path or parsed.fragment or len(pairs) > 32 or any(k not in ('xt', 'dn', 'tr') for k, _ in pairs):
        raise TransportError('invalid_magnet')
    for key, url in pairs:
        if key == 'tr': tracker(url, tracker_origins, secret)
    topics = [v for k, v in pairs if k == 'xt']
    if not 1 <= len(topics) <= 2:
        raise TransportError('invalid_magnet')
    v1 = v2 = None
    for topic in topics:
        if topic.startswith('urn:btih:') and v1 is None:
            value_hash = topic[9:]
            if re.fullmatch('[0-9a-fA-F]{40}', value_hash):
                v1 = value_hash.lower()
                continue
            if re.fullmatch('[A-Z2-7a-z]{32}', value_hash):
                v1 = base64.b32decode(value_hash.upper()).hex()
                continue
        if v2 is None and re.fullmatch('urn:btmh:1220[0-9a-fA-F]{64}', topic):
            v2 = topic[13:].lower()
            continue
        raise TransportError('invalid_magnet')
    return TorrentIdentities(v1, v2)


def validate_magnet(value, *, tracker_origins=None, secret=None):
    """Legacy scalar validation; full expected identity uses magnet_identities."""
    hashes = magnet_identities(value, tracker_origins=tracker_origins, secret=secret)
    return hashes.v1 or hashes.v2[:40]


def file_metadata(row, *, single=False):
    """Reviewed BEP47 hints, without symlink or single-file padding semantics."""
    if b'sha1' in row and (not isinstance(row[b'sha1'], bytes) or len(row[b'sha1']) != 20):
        raise TransportError('invalid_torrent')
    if b'attr' in row:
        flags = row[b'attr']
        if (not isinstance(flags, bytes) or len(flags) > 64 or b'l' in flags
                or single and any(flag not in b'hx' for flag in flags)):
            raise TransportError('invalid_torrent')


def _merkle_root(hashes, piece_length):
    """Return a BEP 52 SHA-256 root, padding only the incomplete right edge."""
    hashes = list(hashes)
    if not hashes:
        raise TransportError('invalid_torrent')
    width = 1
    while width < len(hashes):
        width <<= 1
    # Missing blocks are zero digests at the 16 KiB leaf level. At a higher
    # piece layer, an absent piece is the root of that all-zero subtree.
    padding = b'\0' * 32
    span = 16 * 1024
    while span < piece_length:
        padding = hashlib.sha256(padding + padding).digest()
        span <<= 1
    hashes.extend([padding] * (width - len(hashes)))
    while len(hashes) > 1:
        hashes = [hashlib.sha256(hashes[i] + hashes[i + 1]).digest()
                  for i in range(0, len(hashes), 2)]
    return hashes[0]


def _v2_files(info, piece_layers):
    """Validate the reviewed BEP 52 tree/layers and return ordered real files."""
    if (type(info.get(b'meta version')) is not int or info[b'meta version'] != 2
            or not isinstance(info.get(b'file tree'), dict)
            or not isinstance(piece_layers, dict)):
        raise TransportError('invalid_torrent')
    piece_length = info.get(b'piece length')
    if (type(piece_length) is not int or piece_length < 16 * 1024
            or piece_length & (piece_length - 1)):
        raise TransportError('invalid_torrent')

    def text_component(value):
        if not isinstance(value, bytes):
            raise TransportError('invalid_torrent')
        text = value.decode('utf-8')
        safe_name(text)
        if (unicodedata.normalize('NFC', text) != text
                or any(unicodedata.category(char) == 'Cc' for char in text)):
            raise TransportError('invalid_torrent')
        return text

    tree_files = []
    required_layers = {}
    seen_canonical = set()
    canonical_paths = {}

    def visit(node, prefix):
        if not isinstance(node, dict) or not node:
            raise TransportError('invalid_torrent')
        if b'' in node:
            if prefix == () or len(node) != 1:
                raise TransportError('invalid_torrent')
            leaf = node[b'']
            if (not isinstance(leaf, dict)
                    or set(leaf) - {b'length', b'pieces root', b'attr'}
                    or type(leaf.get(b'length')) is not int or leaf[b'length'] < 0):
                raise TransportError('invalid_torrent')
            length = leaf[b'length']
            attr = leaf.get(b'attr', b'')
            if not isinstance(attr, bytes) or len(attr) > 64 or any(c not in b'hx' for c in attr):
                raise TransportError('invalid_torrent')
            if length:
                root = leaf.get(b'pieces root')
                if not isinstance(root, bytes) or len(root) != 32:
                    raise TransportError('invalid_torrent')
                if length > piece_length:
                    layer = piece_layers.get(root)
                    count = (length + piece_length - 1) // piece_length
                    if not isinstance(layer, bytes) or len(layer) != count * 32:
                        raise TransportError('invalid_torrent')
                    hashes = [layer[i:i + 32] for i in range(0, len(layer), 32)]
                    if _merkle_root(hashes, piece_length) != root:
                        raise TransportError('invalid_torrent')
                    if root in required_layers and required_layers[root] != layer:
                        raise TransportError('invalid_torrent')
                    required_layers[root] = layer
                elif b'pieces root' not in leaf:
                    raise TransportError('invalid_torrent')
            elif b'pieces root' in leaf:
                raise TransportError('invalid_torrent')
            canonical = tuple(unicodedata.normalize('NFC', part).casefold() for part in prefix)
            if canonical in seen_canonical:
                raise TransportError('invalid_torrent')
            seen_canonical.add(canonical)
            tree_files.append((prefix, length))
            return
        for raw_part, child in node.items():
            part = text_component(raw_part)
            path = prefix + (part,)
            canonical = tuple(component.casefold() for component in path)
            if canonical in canonical_paths and canonical_paths[canonical] != path:
                raise TransportError('invalid_torrent')
            canonical_paths[canonical] = path
            visit(child, path)

    tree = info[b'file tree']
    if b'' in tree:
        raise TransportError('invalid_torrent')
    visit(tree, ())
    if len(tree_files) > 1000:
        raise TransportError('invalid_torrent')
    if set(piece_layers) != set(required_layers):
        raise TransportError('invalid_torrent')
    for root, layer in required_layers.items():
        if piece_layers[root] != layer:
            raise TransportError('invalid_torrent')

    if b'name.utf-8' in info:
        if info[b'name.utf-8'] != info.get(b'name'):
            raise TransportError('invalid_torrent')
    text_component(info[b'name'])
    return tree_files


def _hybrid_files(info, tree_files):
    """Require the fully validated v1 view to match the validated v2 files."""
    piece_length = info[b'piece length']
    v1_files = info.get(b'files')
    if v1_files is None:
        if type(info.get(b'length')) is not int:
            raise TransportError('invalid_torrent')
        v1_files = [{b'length': info[b'length'], b'path': [info[b'name']]}]
    if not isinstance(v1_files, list):
        raise TransportError('invalid_torrent')
    v1_real_files = []
    offset = 0
    for row in v1_files:
        attr = row.get(b'attr', b'')
        parts = tuple(part.decode('utf-8') for part in row[b'path'])
        if b'path.utf-8' in row:
            utf8_parts = tuple(part.decode('utf-8') for part in row[b'path.utf-8'])
            if utf8_parts != parts:
                raise TransportError('invalid_torrent')
        if b'p' in attr:
            if (attr != b'p' or parts[0:1] != ('.pad',) or len(parts) != 2
                    or not re.fullmatch(r'[1-9][0-9]{0,18}', parts[1])):
                raise TransportError('invalid_torrent')
            amount = row[b'length']
            expected = (-offset) % piece_length
            if expected == 0 or amount != expected or int(parts[1]) != amount:
                raise TransportError('invalid_torrent')
            offset += amount
            continue
        if any(flag not in b'hx' for flag in attr):
            raise TransportError('invalid_torrent')
        length = row[b'length']
        if length:
            if offset % piece_length:
                raise TransportError('invalid_torrent')
            for part in parts:
                if unicodedata.normalize('NFC', part) != part:
                    raise TransportError('invalid_torrent')
            v1_real_files.append((parts, length))
        else:
            v1_real_files.append((parts, length))
        offset += length
    if v1_real_files != tree_files:
        raise TransportError('invalid_torrent')


class TorrentIdentities(NamedTuple):
    v1: str | None
    v2: str | None = None


def torrent_identities(raw, *, tracker_origins=None, secret=None):
    """Validate once, then expose digests of the exact original info bytes."""
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
        if pos != len(raw) or not isinstance(info, dict) or not info_bytes:
            raise TransportError('invalid_torrent')
        # V2 structure is shared by pure-v2 and hybrid descriptors. Any v1
        # view (including single-file hints) still needs full v1 validation.
        has_v2 = any(key in info for key in (b'meta version', b'file tree')) or b'piece layers' in data
        allowed_info = {b'name', b'name.utf-8', b'pieces', b'piece length', b'length', b'files',
                        b'private', b'source', b'md5sum', b'sha1', b'attr'}
        if has_v2:
            allowed_info |= {b'meta version', b'file tree'}
        if set(info) - allowed_info:
            raise TransportError('invalid_torrent')
        allowed_top = {b'info', b'announce', b'announce-list', b'comment', b'comment.utf-8',
                       b'created by', b'creation date', b'encoding'}
        if has_v2:
            allowed_top.add(b'piece layers')
        if set(data) - allowed_top:
            raise TransportError('invalid_torrent')
        if has_v2 and (type(info.get(b'meta version')) is not int or info[b'meta version'] != 2
                       or b'file tree' not in info or b'piece layers' not in data):
            raise TransportError('invalid_torrent')
        if b'private' in info and (type(info[b'private']) is not int or info[b'private'] not in (0, 1)):
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
        has_v1 = any(key in info for key in (b'pieces', b'length', b'files', b'md5sum', b'sha1', b'attr'))
        if has_v2 and not has_v1:
            tree_files = _v2_files(info, data[b'piece layers'])
            if not any(length for _, length in tree_files):
                raise TransportError('invalid_torrent')
            return TorrentIdentities(None, hashlib.sha256(info_bytes).hexdigest())
        if not isinstance(info.get(b'pieces'), bytes) or len(info[b'pieces']) % 20 or not info[b'pieces'] or type(info.get(b'piece length')) is not int or info[b'piece length'] <= 0:
            raise TransportError('invalid_torrent')
        files = info.get(b'files')
        if files is None:
            if type(info.get(b'length')) is not int or info[b'length'] <= 0: raise TransportError('invalid_torrent')
            file_metadata(info, single=True)
            total_bytes = payload_bytes = info[b'length']
        else:
            if (not isinstance(files, list) or not 0 < len(files) <= 1000
                    or any(key in info for key in (b'length', b'sha1', b'attr'))):
                raise TransportError('invalid_torrent')
            paths = {}
            total_bytes = payload_bytes = 0
            for row in files:
                if (not isinstance(row, dict) or set(row) - {b'length', b'path', b'path.utf-8', b'md5sum', b'attr', b'sha1'}
                        or type(row.get(b'length')) is not int or row[b'length'] < 0):
                    raise TransportError('invalid_torrent')
                file_metadata(row)
                total_bytes += row[b'length']
                if b'p' not in row.get(b'attr', b''):
                    payload_bytes += row[b'length']
                for key in (b'path', b'path.utf-8'):
                    if key not in row and key != b'path': continue
                    parts = row[key]
                    if not isinstance(parts, list) or not parts: raise TransportError('invalid_torrent')
                    path = tuple(safe_name(v.decode('utf-8')) for v in parts)
                    if key == b'path':
                        padding = row.get(b'attr') == b'p'
                        # Released hybrid creators repeat .pad/<size> for equal
                        # alignment gaps. These synthetic rows have no payload;
                        # _hybrid_files still validates every exact offset/size.
                        if path in paths and not (has_v2 and padding and paths[path]):
                            raise TransportError('invalid_torrent')
                        paths[path] = padding
        # Piece hashes cover the concatenated v1 payload, including padding.
        if not payload_bytes or len(info[b'pieces']) // 20 != (total_bytes + info[b'piece length'] - 1) // info[b'piece length']:
            raise TransportError('invalid_torrent')
        if has_v2:
            _hybrid_files(info, _v2_files(info, data[b'piece layers']))
        return TorrentIdentities(hashlib.sha1(info_bytes).hexdigest(),
                                 hashlib.sha256(info_bytes).hexdigest() if has_v2 else None)
    except TransportError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, AttributeError):
        raise TransportError('invalid_torrent') from None


def validate_torrent(raw, *, tracker_origins=None, secret=None):
    """Legacy descriptor identity: v1 SHA1, or native best truncated v2 SHA256."""
    identities = torrent_identities(raw, tracker_origins=tracker_origins, secret=secret)
    return identities.v1 if identities.v1 is not None else identities.v2[:40]
