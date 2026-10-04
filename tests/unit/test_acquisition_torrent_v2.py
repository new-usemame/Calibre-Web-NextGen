# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-v2 admission preserves released metainfo bytes and shared BEP 52 guards."""
import hashlib
import importlib

import pytest

from tests.unit.test_acquisition_torrent_hybrid import bencode, hybrid, PIECE
from tests.unit.test_acquisition_usenet import spec

pytestmark = pytest.mark.unit

# Exact original sessionless libtorrent 2.0.11 creator output. No native runtime
# dependency: native full hashes were independently recorded with these bytes.
RELEASED = [
    pytest.param(bytes.fromhex(
        "6431333a6372656174696f6e2064617465693137393131333739313665343a696e666f64393a66696c65207472656564"
        "31333a4f726967696e616c2e6570756264303a64363a6c656e67746869313834386531313a70696563657320726f6f74"
        "33323a24974b39ba60171d3f8b8d1fd5ce40357fecc316a1c75a6b10f0e4744d33d03965656531323a6d657461207665"
        "7273696f6e693265343a6e616d6531333a4f726967696e616c2e6570756231323a7069656365206c656e677468693136"
        "333834656531323a7069656365206c6179657273646565"
    ), "4a12da42135347be2fd7b045ff12fa6adfe2993c30f42a8b57854542a4d8b249", id="single"),
    pytest.param(bytes.fromhex(
        "6431333a6372656174696f6e2064617465693137393131333739313665343a696e666f64393a66696c65207472656564"
        "363a412e6570756264303a64363a6c656e67746869313834386531313a70696563657320726f6f7433323a24974b39ba"
        "60171d3f8b8d1fd5ce40357fecc316a1c75a6b10f0e4744d33d0396565363a422e6570756264303a64363a6c656e6774"
        "6869313834386531313a70696563657320726f6f7433323a24974b39ba60171d3f8b8d1fd5ce40357fecc316a1c75a6b"
        "10f0e4744d33d03965656531323a6d6574612076657273696f6e693265343a6e616d65353a426f6f6b7331323a706965"
        "6365206c656e677468693136333834656531323a7069656365206c6179657273646565"
    ), "8bfd8a945a2bdd4664f1d26e6ec2a1f81a9cf0f18e8acbe7ae860ed688a98ed8", id="multi"),
    pytest.param(bytes.fromhex(
        "6431333a6372656174696f6e2064617465693137393131333739313665343a696e666f64393a66696c65207472656564"
        "31323a4c6179657265642e6570756264303a64363a6c656e677468693233313437316531313a70696563657320726f6f"
        "7433323a74f69d9bb7df71b52d84153c9b8a532888dfb4056678981e12abeced42eff55665656531323a6d6574612076"
        "657273696f6e693265343a6e616d6531323a4c6179657265642e6570756231323a7069656365206c656e677468693332"
        "373638656531323a7069656365206c61796572736433323a74f69d9bb7df71b52d84153c9b8a532888dfb4056678981e"
        "12abeced42eff5563235363a2d3fe14814c2373aa6749eb2bff44c432b4e8aedc0ef623ee84ee64d96ec32530429b654"
        "79d39c4f99f8fd685709685fcf4e067da94ba12c91db930d305dd49ebb41b83ff3ac62f12714307933d4ee0cbfa2fef1"
        "844889c43be6f9f6e16af17814f8525154b0b42ef9b000c61eb95407bf61c0ea94a341f1ba23be85ffc79b672becd3ba"
        "3199f1ecbd1fd610275b2aa12d1ca51e293209a0d9616967ea98770366dd44f4e168f7e2afe3a5373b994d935716d464"
        "d65d6dc4ffb7dd8304e8fc1e82ce24417e71d80e4f058cd34f98c171745208d0e659fa2912d91d4fa84ab4454a6bd6b0"
        "42dd0641bb66d6f555d5fee5c1d054b34dffd7c31d9adcfbf0ad4c6f6565"
    ), "e0edbb4ebf7ceade3f2b41353b38f6c9720dfd55513bf66a7bde7bd51c44b336", id="layered"),
]


def parser():
    return importlib.import_module(spec.name + '.torrent')


def pure_v2(*, layered=False):
    descriptor = hybrid([([b'book.epub'], b'x' * (3 * PIECE + 7) if layered else b'owned'),
                         ([b'empty.txt'], b'')])
    del descriptor[b'info'][b'files']
    del descriptor[b'info'][b'pieces']
    return descriptor


@pytest.mark.parametrize('raw,expected', RELEASED)
def test_original_released_single_multi_and_layered_have_only_exact_v2(raw, expected):
    identities = parser().torrent_identities(raw)
    assert identities == (None, expected)
    assert parser().validate_torrent(raw) == expected[:40]


def test_hash_is_original_info_only_and_zero_length_file_has_no_root():
    descriptor = pure_v2()
    info = bencode(descriptor[b'info'])
    assert parser().torrent_identities(bencode(descriptor)) == (None, hashlib.sha256(info).hexdigest())
    descriptor[b'comment'] = b'outside info'
    assert parser().torrent_identities(bencode(descriptor)).v2 == hashlib.sha256(info).hexdigest()
    descriptor[b'info'][b'source'] = b'original source'
    expected = hashlib.sha256(bencode(descriptor[b'info'])).hexdigest()
    assert parser().torrent_identities(bencode(descriptor)) == (None, expected)
    descriptor[b'info'][b'file tree'][b'empty.txt'][b''][b'pieces root'] = bytes(32)
    with pytest.raises(parser().TransportError):
        parser().torrent_identities(bencode(descriptor))


@pytest.mark.parametrize('field,value', [
    (b'pieces', b'x' * 20), (b'length', 5),
    (b'files', [{b'length': 5, b'path': [b'book.epub']}]),
    (b'sha1', b'x' * 20), (b'md5sum', b'x' * 32), (b'attr', b'h'),
])
def test_partial_v1_view_or_file_hints_cannot_downgrade_to_pure_v2(field, value):
    descriptor = pure_v2()
    descriptor[b'info'][field] = value
    with pytest.raises(parser().TransportError):
        parser().torrent_identities(bencode(descriptor))


@pytest.mark.parametrize('mutation', ['meta_version', 'unknown_info', 'unknown_top', 'missing_layers'])
def test_pure_v2_requires_reviewed_envelope_and_known_version(mutation):
    descriptor = pure_v2()
    if mutation == 'meta_version':
        descriptor[b'info'][b'meta version'] = 3
    elif mutation == 'unknown_info':
        descriptor[b'info'][b'future extension'] = b'x'
    elif mutation == 'unknown_top':
        descriptor[b'url-list'] = [b'https://example.invalid/book']
    else:
        del descriptor[b'piece layers']
    with pytest.raises(parser().TransportError):
        parser().torrent_identities(bencode(descriptor))


@pytest.mark.parametrize('mutation', ['control', 'traversal', 'case_alias', 'nfc', 'name_alias', 'symlink'])
def test_pure_v2_shares_tree_path_alias_and_symlink_restrictions(mutation):
    descriptor = pure_v2()
    info = descriptor[b'info']
    tree = info[b'file tree']
    leaf = tree[b'book.epub']
    if mutation in ('control', 'traversal', 'nfc'):
        path = {'control': b'bad\x01.epub', 'traversal': b'..', 'nfc': 'e\u0301.epub'.encode()}[mutation]
        tree[path] = tree.pop(b'book.epub')
    elif mutation == 'case_alias':
        tree[b'BOOK.epub'] = leaf
    elif mutation == 'name_alias':
        info[b'name.utf-8'] = b'other'
    else:
        leaf[b''][b'attr'] = b'l'
    with pytest.raises(parser().TransportError):
        parser().torrent_identities(bencode(descriptor))


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'corrupt', 'count'])
def test_pure_v2_requires_exact_piece_layer_set_count_and_merkle_root(mutation):
    descriptor = pure_v2(layered=True)
    layers = descriptor[b'piece layers']
    root = next(iter(layers))
    if mutation == 'missing':
        del layers[root]
    elif mutation == 'extra':
        layers[bytes(32)] = bytes(32)
    elif mutation == 'count':
        layers[root] += bytes(32)
    else:
        layers[root] = bytes([layers[root][0] ^ 1]) + layers[root][1:]
    with pytest.raises(parser().TransportError):
        parser().torrent_identities(bencode(descriptor))


@pytest.mark.parametrize('mutation', ['trailing', 'duplicate', 'order', 'integer'])
def test_original_pure_v2_still_requires_exact_bencode_framing(mutation):
    raw = RELEASED[0].values[0]
    if mutation == 'trailing':
        raw += b'e'
    elif mutation == 'duplicate':
        raw = raw.replace(b'4:name', b'4:name1:x4:name')
    elif mutation == 'order':
        raw = raw.replace(b'4:info', b'1:z0:4:info')
    else:
        raw = raw.replace(b'i16384e', b'i016384e')
    with pytest.raises(parser().TransportError):
        parser().torrent_identities(raw)


def test_pure_v2_keeps_tracker_authority_secrets_and_dual_topic_refusal():
    raw = bencode(pure_v2())
    with pytest.raises(parser().TransportError, match='untrusted_torrent_tracker'):
        parser().torrent_identities(raw, tracker_origins=[])
    descriptor = pure_v2()
    descriptor[b'announce'] += b'?passkey=private-token'
    with pytest.raises(parser().TransportError, match='untrusted_torrent_tracker'):
        parser().torrent_identities(bencode(descriptor), secret='private-token')
    with pytest.raises(parser().TransportError, match='invalid_magnet'):
        parser().validate_magnet('magnet:?xt=urn:btmh:1220' + 'a' * 64
                                 + '&xt=urn:btih:' + 'b' * 40)
