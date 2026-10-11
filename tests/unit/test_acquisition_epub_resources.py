# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact resource identity; ZIP packaging may differ, edition content may not."""
import hashlib
import stat
import zipfile
from pathlib import Path

import pytest
from tests.unit.test_acquisition_calibre_transaction import helper

CONTAINER = b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
RESOURCES = {'mimetype': b'application/epub+zip', 'META-INF/container.xml': CONTAINER,
             'OPS/book.opf': b'<package xml:lang="en"><metadata/></package>',
             'OPS/chapter.xhtml': b'<html><body>Same original edition</body></html>',
             'OPS/font.bin': bytes(range(256))}


def package(path, resources=None, *, repack=False, extra=(), compression=None):
    resources = dict(RESOURCES if resources is None else resources)
    names = list(resources)
    if repack:
        names = ['mimetype'] + list(reversed([n for n in names if n != 'mimetype']))
    with zipfile.ZipFile(path, 'w') as archive:
        archive.comment = b'changed packaging comment' if repack else b''
        for name in names:
            info = zipfile.ZipInfo(name, (2025 if repack else 2024, 1, 2, 3, 4, 6))
            info.compress_type = (zipfile.ZIP_STORED if name == 'mimetype' else
                                  (compression if compression is not None else
                                   zipfile.ZIP_DEFLATED if repack else zipfile.ZIP_STORED))
            info.comment = b'member comment' if repack else b''
            archive.writestr(info, resources[name])
        for info, payload in extra:
            archive.writestr(info, payload)
    return path


def test_packaging_equality_preserves_every_resource_and_has_no_write_side_effect(helper, tmp_path):
    original = package(tmp_path/'original.epub')
    repacked = package(tmp_path/'repacked.epub', repack=True, extra=[('empty/', b'')])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert hashlib.sha256(original.read_bytes()).digest() != hashlib.sha256(repacked.read_bytes()).digest()
    digest = helper.epub_resource_digest(original)
    assert isinstance(digest, str) and len(digest) == 64 and int(digest, 16) >= 0
    assert helper.epub_resource_digest(repacked) == digest
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize('change', ['body', 'language', 'font', 'rename', 'extra', 'missing', 'rights'])
def test_edition_resource_changes_are_not_equivalent(helper, tmp_path, change):
    original = package(tmp_path/'original.epub')
    changed = dict(RESOURCES)
    if change == 'body': changed['OPS/chapter.xhtml'] += b'changed'
    elif change == 'language': changed['OPS/book.opf'] = changed['OPS/book.opf'].replace(b'en', b'fr')
    elif change == 'font': changed['OPS/font.bin'] = b'new font'
    elif change == 'rename': changed['OPS/renamed.xhtml'] = changed.pop('OPS/chapter.xhtml')
    elif change == 'extra': changed['OPS/extra.xhtml'] = b'extra'
    elif change == 'missing': del changed['OPS/font.bin']
    else: changed['META-INF/rights.xml'] = b'<rights>different licensing resource</rights>'
    candidate = package(tmp_path/'changed.epub', changed)
    original_digest = helper.epub_resource_digest(original)
    changed_digest = helper.epub_resource_digest(candidate)
    assert original_digest and changed_digest and changed_digest != original_digest


@pytest.mark.parametrize('fault', ['duplicate', 'traversal', 'absolute', 'backslash', 'alias',
                                  'colon', 'control', 'symlink', 'nonempty-directory',
                                  'missing-container', 'missing-rootfile', 'doctype', 'utf16-doctype',
                                  'wrong-mimetype', 'mimetype-not-first', 'unsupported-codec'])
def test_ambiguous_or_unsafe_packages_have_no_identity(helper, tmp_path, fault):
    resources = dict(RESOURCES); extras = []; compression = None
    if fault == 'duplicate': extras = [('OPS/chapter.xhtml', b'other')]
    elif fault in {'traversal', 'absolute', 'backslash', 'alias', 'colon', 'control'}:
        name = {'traversal': '../outside', 'absolute': '/outside', 'backslash': 'OPS\\outside',
                'alias': 'OPS//outside', 'colon': 'OPS/a:b', 'control': 'OPS/a\x01b'}[fault]
        extras = [(name, b'unsafe')]
    elif fault == 'symlink':
        info = zipfile.ZipInfo('OPS/link'); info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16; extras = [(info, b'chapter.xhtml')]
    elif fault == 'nonempty-directory': extras = [('OPS/dir/', b'not empty')]
    elif fault == 'missing-container': del resources['META-INF/container.xml']
    elif fault == 'missing-rootfile': del resources['OPS/book.opf']
    elif fault in {'doctype', 'utf16-doctype'}:
        xml = '<!DOCTYPE container [<!ENTITY path "OPS/book.opf">]><container><rootfiles><rootfile full-path="&path;"/></rootfiles></container>'
        resources['META-INF/container.xml'] = xml.encode('utf-16' if fault == 'utf16-doctype' else 'utf-8')
    elif fault == 'wrong-mimetype': resources['mimetype'] = b'application/epub+zip\n'
    elif fault == 'mimetype-not-first': resources = {**{'extra': b'extra'}, **resources}
    else: compression = zipfile.ZIP_BZIP2
    archive = package(tmp_path/'bad.epub', resources, extra=extras, compression=compression)
    assert helper.epub_resource_digest(archive) is None


def test_shared_budget_is_consumed_by_failed_scans_and_prevents_unbounded_candidate_work(helper, tmp_path):
    first = package(tmp_path/'first.epub'); second = package(tmp_path/'second.epub', repack=True)
    budget = helper.EpubScanBudget(archive_bytes=first.stat().st_size + second.stat().st_size - 1)
    assert helper.epub_resource_digest(first, budget=budget)
    assert budget.remaining_archive_bytes == second.stat().st_size - 1
    assert helper.epub_resource_digest(second, budget=budget) is None
    assert budget.remaining_archive_bytes >= 0
    failed = helper.EpubScanBudget(expanded_bytes=40)
    assert helper.epub_resource_digest(first, budget=failed) is None
    assert failed.remaining_archive_bytes == 200*1024*1024 - first.stat().st_size
    assert 0 <= failed.remaining_expanded_bytes < 40
    assert helper.epub_resource_digest(second, budget=failed) is None
    for invalid in [-1, True, 1.5, '100']:
        with pytest.raises(ValueError): helper.EpubScanBudget(archive_bytes=invalid)
        with pytest.raises(ValueError): helper.EpubScanBudget(expanded_bytes=invalid)


def test_input_symlink_and_zip_bomb_do_not_gain_resource_identity(helper, tmp_path):
    original = package(tmp_path/'original.epub')
    link = tmp_path/'link.epub'; link.symlink_to(original)
    assert helper.epub_resource_digest(link) is None
    assert helper.epub_resource_digest(tmp_path) is None
    resources = dict(RESOURCES); resources['OPS/bomb'] = b'0' * 2_000_000
    bomb = package(tmp_path/'bomb.epub', resources, repack=True)
    assert helper.epub_resource_digest(bomb) is None


def test_oversized_container_is_refused_before_expansion(helper, tmp_path):
    resources = dict(RESOURCES)
    resources['META-INF/container.xml'] = CONTAINER.replace(b'<rootfiles>', b'<!--'+b'x'*3_000_000+b'--><rootfiles>')
    path = package(tmp_path/'large-container.epub', resources)
    budget = helper.EpubScanBudget()
    assert helper.epub_resource_digest(path, budget=budget) is None
    assert budget.remaining_expanded_bytes == 400*1024*1024


def test_excess_directory_entries_are_refused_before_zipfile_member_allocation(helper, monkeypatch, tmp_path):
    path = package(tmp_path/'many.epub', extra=[('dir'+str(n)+'/', b'') for n in range(10001)])
    monkeypatch.setattr(helper.zipfile, 'ZipFile', lambda *args, **kwargs: pytest.fail('excess ZIP records allocated before count refusal'))
    assert helper.epub_resource_digest(path) is None


@pytest.mark.parametrize('field_offset', [14, 18, 22])
def test_local_header_crc_and_sizes_must_agree_with_central_directory(helper, tmp_path, field_offset):
    path = package(tmp_path/'mismatch.epub')
    with zipfile.ZipFile(path) as archive:
        offset = archive.getinfo('OPS/chapter.xhtml').header_offset
    raw = bytearray(path.read_bytes()); raw[offset+field_offset] ^= 1; path.write_bytes(raw)
    assert helper.epub_resource_digest(path) is None


def test_utf16_without_bom_cannot_hide_container_entities(helper, tmp_path):
    resources = dict(RESOURCES)
    xml = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE container [<!ENTITY path "OPS/book.opf">]>'+CONTAINER.decode().replace('OPS/book.opf','&path;')
    resources['META-INF/container.xml'] = xml.encode('utf-16-le')
    assert helper.epub_resource_digest(package(tmp_path/'utf16.epub', resources)) is None


@pytest.mark.parametrize('descriptor_offset', [None, 4, 8, 12])
def test_streamed_zip_descriptors_must_agree_with_resource_metadata(helper, tmp_path, descriptor_offset):
    import io
    import struct
    class StreamOutput(io.BytesIO):
        def seekable(self): return False
        def seek(self, *args): raise OSError('non-seekable writer')
    sink = StreamOutput()
    with zipfile.ZipFile(sink, 'w') as archive:
        for name, payload in RESOURCES.items(): archive.writestr(name, payload)
    path = tmp_path/'streamed.epub'; path.write_bytes(sink.getvalue())
    if descriptor_offset is None:
        assert helper.epub_resource_digest(path) == helper.epub_resource_digest(package(tmp_path/'ordinary.epub'))
        return
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo('OPS/chapter.xhtml')
        assert info.flag_bits & 8
    raw = bytearray(path.read_bytes())
    nlen, elen = struct.unpack_from('<HH', raw, info.header_offset+26)
    descriptor = info.header_offset+30+nlen+elen+info.compress_size
    assert raw[descriptor:descriptor+4] == b'PK\x07\x08'
    raw[descriptor+descriptor_offset] ^= 1; path.write_bytes(raw)
    assert helper.epub_resource_digest(path) is None


@pytest.mark.parametrize('fault', ['hidden-decoded-tail', 'invalid-deflate'])
def test_complete_deflate_stream_is_checked_beyond_central_size_hint(helper, tmp_path, fault):
    import struct
    import zlib
    resources = dict(RESOURCES)
    if fault == 'hidden-decoded-tail': resources['OPS/chapter.xhtml'] += b'<p>A changed edition beyond the claimed length</p>'
    path = package(tmp_path/'bad-deflate.epub', resources, repack=True)
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo('OPS/chapter.xhtml'); directory = archive.start_dir
    raw = bytearray(path.read_bytes())
    nlen, elen = struct.unpack_from('<HH', raw, info.header_offset+26)
    payload_start = info.header_offset+30+nlen+elen
    if fault == 'invalid-deflate': raw[payload_start] = 7  # reserved DEFLATE block type
    else:
        crc = zlib.crc32(RESOURCES['OPS/chapter.xhtml']); length = len(RESOURCES['OPS/chapter.xhtml'])
        struct.pack_into('<I', raw, info.header_offset+14, crc)
        struct.pack_into('<I', raw, info.header_offset+22, length)
        position = directory
        while raw[position:position+4] == b'PK\x01\x02':
            namesize, extrasize, commentsize = struct.unpack_from('<3H', raw, position+28)
            if raw[position+46:position+46+namesize] == b'OPS/chapter.xhtml':
                struct.pack_into('<I', raw, position+16, crc); struct.pack_into('<I', raw, position+24, length); break
            position += 46+namesize+extrasize+commentsize
        actual = zlib.decompress(raw[payload_start:payload_start+info.compress_size], -15)
        assert actual == resources['OPS/chapter.xhtml'] and actual != RESOURCES['OPS/chapter.xhtml']
    path.write_bytes(raw)
    if fault == 'hidden-decoded-tail':
        with zipfile.ZipFile(path) as archive: assert archive.read('OPS/chapter.xhtml') == RESOURCES['OPS/chapter.xhtml']
    assert helper.epub_resource_digest(path) is None
