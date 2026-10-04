# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral contract for conservative BEP 52 hybrid metainfo support."""
import hashlib
import importlib

import pytest

from tests.unit.test_acquisition_usenet import spec

pytestmark = pytest.mark.unit
PIECE = 16 * 1024
TRACKER = b"http://tracker.example/announce"


def bencode(value):
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    return b"d" + b"".join(bencode(key) + bencode(value[key]) for key in sorted(value)) + b"e"


def merkle(hashes):
    layer = list(hashes)
    while len(layer) > 1:
        layer = [hashlib.sha256(layer[i] + layer[i + 1]).digest() for i in range(0, len(layer), 2)]
    return layer[0]


def v2_file(data, piece_length=PIECE):
    blocks = [data[start:start + 16 * 1024] for start in range(0, len(data), 16 * 1024)]
    leaves = [hashlib.sha256(block).digest() for block in blocks]
    piece_hashes = []
    width = piece_length // (16 * 1024)
    for start in range(0, len(leaves), width):
        group = leaves[start:start + width]
        group.extend([b"\0" * 32] * (width - len(group)))
        piece_hashes.append(merkle(group))
    root_hashes = piece_hashes if len(data) > piece_length else leaves
    if len(data) <= piece_length:
        root_width = 1
        while root_width < len(root_hashes):
            root_width *= 2
        root = merkle(root_hashes + [b"\0" * 32] * (root_width - len(root_hashes)))
        return {b"length": len(data), b"pieces root": root}, {}
    root_width = 1
    while root_width < len(root_hashes):
        root_width *= 2
    padding_root = merkle([bytes(32)] * width)
    root = merkle(root_hashes + [padding_root] * (root_width - len(root_hashes)))
    layers = {root: b"".join(piece_hashes)} if len(data) > piece_length else {}
    return {b"length": len(data), b"pieces root": root}, layers


def hybrid(files, *, piece_length=PIECE, extra_info=None, extra_top=None):
    """files is an ordered sequence of (path parts, data) real files."""
    tree = {}
    rows = []
    offset = 0
    piece_stream = bytearray()
    piece_layers = {}
    for parts, data in files:
        pad = (-offset) % piece_length if data else 0
        if pad:
            rows.append({b"length": pad, b"path": [b".pad", str(pad).encode()], b"attr": b"p"})
            piece_stream.extend(b"\0" * pad)
            offset += pad
        rows.append({b"length": len(data), b"path": parts})
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        descriptor, layers = v2_file(data, piece_length) if data else ({b"length": 0}, {})
        node[parts[-1]] = {b"": descriptor}
        piece_layers.update(layers)
        piece_stream.extend(data)
        offset += len(data)
    if len(files) > 1 and offset % piece_length:
        pad = piece_length - offset % piece_length
        rows.append({b"length": pad, b"path": [b".pad", str(pad).encode()], b"attr": b"p"})
        piece_stream.extend(b"\0" * pad)
    # Ordinary v1 pieces span the concatenated stream, including valid padding.
    pieces = b"".join(hashlib.sha1(piece_stream[i:i + piece_length]).digest()
                     for i in range(0, len(piece_stream), piece_length))
    info = {
        b"file tree": tree,
        b"files": rows,
        b"meta version": 2,
        b"name": b"bundle",
        b"piece length": piece_length,
        b"pieces": pieces,
    }
    if extra_info:
        info.update(extra_info)
    result = {b"info": info, b"piece layers": piece_layers, b"announce": TRACKER}
    if extra_top:
        result.update(extra_top)
    return result


def validate(value):
    return importlib.import_module(spec.name + ".torrent").validate_torrent(
        bencode(value), tracker_origins=[TRACKER.decode()])


def test_accepts_hybrid_using_exact_original_v1_info_bytes_identity():
    descriptor = hybrid([([b"book.epub"], b"original legal fixture bytes")])
    raw_info = bencode(descriptor[b"info"])
    assert validate(descriptor) == hashlib.sha1(raw_info).hexdigest()


def test_accepts_multifile_hybrid_with_valid_interfile_and_trailing_padding():
    descriptor = hybrid([([b"first.epub"], b"a" * PIECE),
                         ([b"second.epub"], b"b")])
    assert validate(descriptor) == hashlib.sha1(bencode(descriptor[b"info"])).hexdigest()


def test_equal_padding_paths_can_repeat_without_duplicate_real_files():
    descriptor = hybrid([([b'A.epub'], b'owned A'), ([b'B.epub'], b'owned B')])
    assert validate(descriptor) == hashlib.sha1(bencode(descriptor[b'info'])).hexdigest()


def test_accepts_empty_file_in_multifile_tree_without_claiming_a_payload_root():
    descriptor = hybrid([([b"book.epub"], b"owned bytes"), ([b"empty.txt"], b"")])
    assert validate(descriptor) == hashlib.sha1(bencode(descriptor[b"info"])).hexdigest()


def test_accepts_piece_layer_hashes_at_the_piece_length_tree_level():
    descriptor = hybrid([([b"large.epub"], b"x" * (2 * PIECE + 7))],
                        piece_length=2 * PIECE)
    assert validate(descriptor) == hashlib.sha1(bencode(descriptor[b"info"])).hexdigest()


@pytest.mark.parametrize('piece_length', [32768, 65536])
def test_unbalanced_piece_layer_uses_zero_subtrees_at_its_own_level(piece_length):
    data = b'Original bytes.' * ((2 * piece_length + 123) // 15 + 1)
    blocks = [hashlib.sha256(data[i:i + PIECE]).digest()
              for i in range(0, len(data), PIECE)]
    width = 1 << (len(blocks) - 1).bit_length()
    level = blocks + [bytes(32)] * (width - len(blocks))
    span = PIECE
    while span < piece_length:
        level = [hashlib.sha256(level[i] + level[i + 1]).digest()
                 for i in range(0, len(level), 2)]
        span *= 2
    layer = b''.join(level[:3])
    root = merkle(level)
    descriptor = hybrid([([b'large.epub'], data)], piece_length=piece_length)
    descriptor[b'info'][b'file tree'][b'large.epub'][b''][b'pieces root'] = root
    descriptor[b'piece layers'] = {root: layer}
    assert validate(descriptor) == hashlib.sha1(bencode(descriptor[b'info'])).hexdigest()
    # Hashing a bare zero digest at the piece level is the wrong tree.
    wrong_root = merkle([layer[i:i + 32] for i in range(0, len(layer), 32)] + [bytes(32)])
    descriptor[b'info'][b'file tree'][b'large.epub'][b''][b'pieces root'] = wrong_root
    descriptor[b'piece layers'] = {wrong_root: layer}
    with pytest.raises(ValueError):
        validate(descriptor)


@pytest.mark.parametrize("mutation", [
    "pure_v2", "missing_layer", "wrong_layer_root", "wrong_v1_path", "wrong_v1_length",
    "missing_v1_pieces", "misaligned_without_padding", "bad_padding_length",
    "metaversion", "symlink", "unknown_leaf_extension", "extraneous_layer",
    "mixed_leaf_directory", "root_file", "unsafe_path", "utf8_alias",
    "path_utf8_alias", "non_power_piece_length", "unknown_attr", "file_layer_count",
])
def test_rejects_hybrid_descriptor_mismatches_and_unsafe_tree_semantics(mutation):
    descriptor = hybrid([([b"book.epub"], b"x" * (PIECE + 7))])
    info = descriptor[b"info"]
    if mutation == "pure_v2":
        info.pop(b"files"); info.pop(b"pieces")
    elif mutation == "missing_layer":
        descriptor[b"piece layers"] = {}
    elif mutation == "wrong_layer_root":
        layer = next(iter(descriptor[b"piece layers"].values()))
        descriptor[b"piece layers"] = {b"z" * 32: layer}
    elif mutation == "wrong_v1_path":
        info[b"files"][0][b"path"] = [b"other.epub"]
    elif mutation == "wrong_v1_length":
        info[b"files"][0][b"length"] += 1
    elif mutation == "missing_v1_pieces":
        info.pop(b"pieces")
    elif mutation == "misaligned_without_padding":
        descriptor = hybrid([([b"one"], b"a"), ([b"two"], b"b")])
        info = descriptor[b"info"]
        info[b"files"] = [row for row in info[b"files"] if row.get(b"attr") != b"p"]
        info[b"pieces"] = hashlib.sha1(b"ab").digest()
    elif mutation == "bad_padding_length":
        info[b"files"].insert(1, {b"length": 1, b"path": [b".pad", b"1"], b"attr": b"p"})
    elif mutation == "metaversion":
        info[b"meta version"] = 3
    elif mutation == "symlink":
        info[b"file tree"][b"book.epub"][b""][b"attr"] = b"l"
    elif mutation == "unknown_leaf_extension":
        info[b"file tree"][b"book.epub"][b""][b"symlink path"] = [b"target"]
    elif mutation == "extraneous_layer":
        descriptor[b"piece layers"][b"e" * 32] = b"f" * 32
    elif mutation == "mixed_leaf_directory":
        info[b"file tree"][b"book.epub"][b"extra"] = {b"": {b"length": 1}}
    elif mutation == "root_file":
        info[b"file tree"][b""] = {b"length": 1}
    elif mutation == "unsafe_path":
        info[b"file tree"].clear()
        info[b"file tree"][b".."] = {b"": {b"length": 1, b"pieces root": b"x" * 32}}
    elif mutation == "utf8_alias":
        info[b"name.utf-8"] = b"different"
    elif mutation == "path_utf8_alias":
        info[b"files"][0][b"path.utf-8"] = [b"different.epub"]
    elif mutation == "non_power_piece_length":
        info[b"piece length"] = PIECE + 1
    elif mutation == "unknown_attr":
        info[b"file tree"][b"book.epub"][b""][b"attr"] = b"z"
    elif mutation == "file_layer_count":
        root = next(iter(descriptor[b"piece layers"]))
        descriptor[b"piece layers"][root] += b"x" * 32
    with pytest.raises(importlib.import_module(spec.name + ".torrent").TransportError):
        validate(descriptor)
