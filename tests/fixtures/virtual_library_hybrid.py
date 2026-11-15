# SPDX-License-Identifier: GPL-3.0-or-later
"""Original local hybrid metainfo inputs; no tracker or peer session."""
import hashlib


def bencode(value):
    if isinstance(value, int):
        return b'i' + str(value).encode() + b'e'
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return str(len(value)).encode() + b':' + value
    if isinstance(value, list):
        return b'l' + b''.join(bencode(v) for v in value) + b'e'
    return b'd' + b''.join(bencode(k) + bencode(value[k]) for k in sorted(value)) + b'e'


def resource_hashes(payload, piece_length):
    leaves = [hashlib.sha256(payload[i:i+16384]).digest() for i in range(0, len(payload), 16384)]
    assert leaves
    level = leaves + [bytes(32)] * ((1 << (len(leaves)-1).bit_length()) - len(leaves))
    roots = None
    width = 16384
    while True:
        if width == piece_length and len(payload) > piece_length:
            roots = b''.join(level[:(len(payload)+piece_length-1)//piece_length])
        if len(level) == 1:
            return level[0], roots
        level = [hashlib.sha256(level[i]+level[i+1]).digest() for i in range(0, len(level), 2)]
        width *= 2


def metainfo(resources, *, name, announce, piece_length=16384, multi=False, pure_v2=False):
    """resources is an ordered name/bytes list in UTF-8 file-tree order."""
    assert resources == sorted(resources, key=lambda row: row[0].encode())
    tree = {}
    layers = {}
    files = []
    payload = bytearray()
    for filename, content in resources:
        root, layer = resource_hashes(content, piece_length)
        tree[filename] = {'': {'length': len(content), 'pieces root': root}}
        if layer is not None:
            layers[root] = layer
        payload.extend(content)
        if multi:
            files.append({'length': len(content), 'path': [filename]})
            padding = (-len(content)) % piece_length
            if padding:
                files.append({'length': padding, 'path': ['.pad', str(padding)], 'attr': b'p'})
                payload.extend(bytes(padding))
    info = {'name': name, 'meta version': 2, 'piece length': piece_length, 'file tree': tree}
    if not pure_v2:
        info['pieces'] = b''.join(hashlib.sha1(payload[i:i+piece_length]).digest()
                                  for i in range(0, len(payload), piece_length))
    if multi and not pure_v2:
        info['files'] = files
    elif not multi:
        assert len(resources) == 1 and resources[0][0] == name
        if not pure_v2:
            info['length'] = len(resources[0][1])
    raw_info = bencode(info)
    return bencode({'announce': announce, 'info': info, 'piece layers': layers}), {
        'v1': None if pure_v2 else hashlib.sha1(raw_info).hexdigest(),
        'v2': hashlib.sha256(raw_info).hexdigest(),
        'v2_truncated': hashlib.sha256(raw_info).hexdigest()[:40],
        'piece_layer_count': len(layers),
    }
