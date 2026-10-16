"""ZIP wire-format edge cases for book rewrites (APPNOTE 6.3.10)."""
import struct
import warnings
import zipfile
import zlib

import pytest

from cps.epub_helper import updateEpub

pytestmark = pytest.mark.unit


def _extra(tag, data):
    return struct.pack('<HH', tag, len(data)) + data


def _fixture(path, *, descriptor=False, signature=True, zip64=False,
             method=8, flags=0, name=b'chapter.xhtml', central_zip64=False):
    """Independent ZIP encoder: local extras deliberately differ from central."""
    payload = b'<html>alpha bravo charlie delta</html>' * 1000
    compressor = zlib.compressobj(1, zlib.DEFLATED, -15)
    compressed = compressor.compress(payload) + compressor.flush() if method == 8 else payload
    crc = zlib.crc32(payload)
    flags |= 8 if descriptor else 0
    local_extra = _extra(0xCAFE, b'local-only')
    central_extra = _extra(0xCAFE, b'central-only')
    if zip64:
        local_extra += _extra(1, struct.pack('<QQ', 0 if descriptor else len(payload),
                                            0 if descriptor else len(compressed)))
    local = struct.pack('<4s5H3L2H', b'PK\x03\x04', 45 if zip64 else 20,
                        flags, method, 0x1883, 0x5022, 0 if descriptor else crc,
                        0xFFFFFFFF if zip64 else (0 if descriptor else len(compressed)),
                        0xFFFFFFFF if zip64 else (0 if descriptor else len(payload)),
                        len(name), len(local_extra)) + name + local_extra + compressed
    if descriptor:
        local += (b'PK\x07\x08' if signature else b'') + struct.pack(
            '<LQQ' if zip64 else '<LLL', crc, len(compressed), len(payload))
    if central_zip64:
        central_extra += _extra(1, struct.pack('<QQQ', len(payload), len(compressed), 0))
    central = struct.pack('<4s6H3L5H2L', b'PK\x01\x02', (3 << 8) | 45, 45,
                          flags, method, 0x1883, 0x5022, crc,
                          0xFFFFFFFF if central_zip64 else len(compressed),
                          0xFFFFFFFF if central_zip64 else len(payload),
                          len(name), len(central_extra), 7, 0, 1, 0o100640 << 16,
                          0xFFFFFFFF if central_zip64 else 0) + name + central_extra + b'comment'
    path.write_bytes(local + central + struct.pack('<4s4H2LH', b'PK\x05\x06', 0, 0, 1, 1,
                                                 len(central), len(local), 7) + b'archive')
    return payload, local


def _local_record(path, info):
    with open(path, 'rb') as f:
        f.seek(info.header_offset)
        header = f.read(30)
        fields = struct.unpack('<4s5H3L2H', header)
        return header + f.read(fields[-2] + fields[-1] + info.compress_size)


@pytest.mark.parametrize('descriptor,signature,zip64', [
    (False, False, False), (False, False, True),
    (True, False, False), (True, True, False),
    (True, False, True), (True, True, True),
])
def test_unchanged_local_record_and_descriptor_survive(tmp_path, descriptor, signature, zip64):
    source, target = tmp_path / 'src.epub', tmp_path / 'out.epub'
    payload, local = _fixture(source, descriptor=descriptor, signature=signature, zip64=zip64,
                              central_zip64=True)
    updateEpub(source, target, 'new.opf', b'new metadata')
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(target) as out:
        assert out.testzip() is None
        assert out.namelist() == ['chapter.xhtml', 'new.opf']
        assert out.read('chapter.xhtml') == payload
        assert out.read('new.opf') == b'new metadata'
        assert out.comment == b'archive'
        old, new = src.getinfo('chapter.xhtml'), out.getinfo('chapter.xhtml')
        for attr in ('date_time', 'CRC', 'compress_type', 'internal_attr', 'external_attr', 'comment', 'flag_bits'):
            assert getattr(new, attr) == getattr(old, attr), attr
        # Includes the descriptor, independent of how the implementation parses it.
        assert target.read_bytes()[:len(local)] == local
        assert new.extra == _extra(0xCAFE, b'central-only')  # no stale ZIP64 size/offset


def test_cp437_local_and_central_names_remain_consistent(tmp_path):
    source, target = tmp_path / 'src', tmp_path / 'out'
    payload, local = _fixture(source, name=b'caf\x82.xhtml')
    updateEpub(source, target, 'new.opf', b'new')
    with zipfile.ZipFile(target) as out:
        assert out.read('café.xhtml') == payload
        assert out.getinfo('café.xhtml').flag_bits & 0x800 == 0
        assert target.read_bytes()[:len(local)] == local


@pytest.mark.parametrize('method,flags,error', [(99, 0, NotImplementedError), (0, 1, RuntimeError)])
def test_unsupported_members_use_legacy_reader_instead_of_raw_copy(tmp_path, monkeypatch, method, flags, error):
    source, target = tmp_path / 'src', tmp_path / 'out'
    _fixture(source, method=method, flags=flags)
    reads = []
    original = zipfile.ZipFile.read

    def read(self, name, *args, **kwargs):
        reads.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'read', read)
    with pytest.raises(error):
        updateEpub(source, target, 'new.opf', b'new')
    assert reads == ['chapter.xhtml']


def test_replacements_keep_order_metadata_and_collapse_duplicate_names(tmp_path):
    source, target = tmp_path / 'src', tmp_path / 'out'
    with zipfile.ZipFile(source, 'w') as src, warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        src.comment = b'archive'
        src.writestr('mimetype', b'application/epub+zip')
        info = zipfile.ZipInfo('content.opf', (2002, 3, 4, 5, 6, 8))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100600 << 16
        info.internal_attr = 1
        info.extra = _extra(1, struct.pack('<QQ', 3, 3)) + _extra(0xCAFE, b'tag')
        info.comment = b'metadata'
        src.writestr(info, b'old')
        src.writestr('chapter.xhtml', b'earlier')
        src.writestr('chapter.xhtml', b'latest')
    updateEpub(source, target, 'content.opf', b'new metadata with a different size')
    with zipfile.ZipFile(target) as out:
        assert out.namelist() == ['mimetype', 'content.opf', 'chapter.xhtml']
        assert out.read('content.opf') == b'new metadata with a different size'
        assert out.read('chapter.xhtml') == b'latest'
        assert out.getinfo('mimetype').compress_type == zipfile.ZIP_STORED
        new = out.getinfo('content.opf')
        assert new.date_time == info.date_time
        assert new.external_attr == info.external_attr
        assert new.internal_attr == 1
        assert new.comment == b'metadata'
        assert new.extra == _extra(0xCAFE, b'tag')
        assert out.testzip() is None


def test_writer_internals_support_raw_write_raw_and_zip64_offsets(tmp_path):
    """Fails if ZipFile's raw registration/start_dir/ZIP64 writer contract changes."""
    from cps.epub_helper import rewrite_zip_members

    source, target = tmp_path / 'src', tmp_path / 'out'
    with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as src:
        src.comment = b'archive'
        for name in ('first', 'replace', 'last'):
            src.writestr(name, (name.encode() + b' ') * 9000)
    # A sparse prefix exercises real >4 GiB central offsets without allocating
    # or copying gigabytes. The output ZipFile starts at the current position.
    offset = 0x100000010
    with open(target, 'w+b') as handle:
        handle.seek(offset)
        rewrite_zip_members(source, handle, {'replace': b'changed', 'appended': 'text'})
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(target) as out:
        assert out.namelist() == ['first', 'replace', 'last', 'appended']
        assert out.comment == b'archive'
        assert out.getinfo('first').header_offset == offset
        assert out.read('replace') == b'changed'
        assert out.read('appended') == b'text'
        for name in ('first', 'last'):
            assert out.read(name) == src.read(name)
            assert _local_record(target, out.getinfo(name)) == _local_record(source, src.getinfo(name))
        assert out.testzip() is None
    # Read this as source as well: central ZIP64 offsets must be stripped or
    # rebuilt when its sparse prefix disappears and the metadata size changes.
    final = tmp_path / 'final'
    rewrite_zip_members(target, final, {'replace': b'a longer replacement'})
    with zipfile.ZipFile(final) as out:
        assert out.getinfo('first').header_offset == 0
        assert out.testzip() is None
        assert out.read('replace') == b'a longer replacement'


@pytest.mark.parametrize('method', [zipfile.ZIP_STORED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_other_supported_methods_remain_verbatim(tmp_path, monkeypatch, method):
    source, target = tmp_path / 'src', tmp_path / 'out'
    with zipfile.ZipFile(source, 'w', compression=method) as src:
        src.writestr('member', b'some text ' * 2000)
    original = zipfile.ZipFile.read

    def read(self, name, *args, **kwargs):
        assert self.filename != str(source), 'unchanged member entered decompression path'
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'read', read)
    updateEpub(source, target, 'new.opf', 'new')
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(target) as out:
        assert out.read('member') == b'some text ' * 2000
        assert _local_record(source, src.getinfo('member')) == _local_record(target, out.getinfo('member'))


def test_unrecognized_descriptor_uses_readable_legacy_path(tmp_path):
    source, target = tmp_path / 'src', tmp_path / 'out'
    payload, local = _fixture(source, descriptor=True)
    data = bytearray(source.read_bytes())
    data[len(local) - 12] ^= 1  # damage only the descriptor CRC; central is valid
    source.write_bytes(data)
    updateEpub(source, target, 'new.opf', b'new')
    with zipfile.ZipFile(target) as out:
        assert out.read('chapter.xhtml') == payload
        assert out.getinfo('chapter.xhtml').flag_bits & 8 == 0
        assert out.testzip() is None


def test_container_repair_copies_compressed_members_and_archive_comment(tmp_path, monkeypatch):
    from cps import web

    source, target = tmp_path / 'src.epub', tmp_path / 'repaired.epub'
    container = (b'<?xml version="1.0"?><?xml version="1.0"?><container '
                 b'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 b'<rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
    with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as src:
        src.comment = b'repair comment'
        src.writestr('mimetype', b'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        src.writestr('META-INF/container.xml', container)
        src.writestr('chapter.xhtml', b'some chapter text ' * 20000)
    monkeypatch.setattr(web, '_get_fixed_epub_path', lambda *_: str(target))
    assert web._repair_epub_container_if_needed(1, str(source)) == str(target)
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(target) as out:
        assert out.namelist() == src.namelist()
        assert out.comment == b'repair comment'
        assert web._is_valid_container_xml(out.read('META-INF/container.xml'))
        assert out.testzip() is None
        for name in ('mimetype', 'chapter.xhtml'):
            assert _local_record(source, src.getinfo(name)) == _local_record(target, out.getinfo(name))


def test_mimetype_is_first_and_stored_even_when_source_needs_normalizing(tmp_path):
    from cps.epub_helper import rewrite_zip_members

    source, target = tmp_path / 'src', tmp_path / 'out'
    with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED) as src:
        src.writestr('chapter', b'chapter')
        src.writestr('mimetype', b'application/epub+zip')
        src.writestr('content.opf', b'old')
    rewrite_zip_members(source, target, {'content.opf': b'new', 'added': b'added'})
    with zipfile.ZipFile(target) as out:
        assert out.namelist() == ['mimetype', 'chapter', 'content.opf', 'added']
        assert out.getinfo('mimetype').compress_type == zipfile.ZIP_STORED
        assert out.read('mimetype') == b'application/epub+zip'
        assert out.testzip() is None


@pytest.mark.parametrize('method,flags', [(99, 0), (0, 1)])
def test_replacing_unsupported_member_still_works_without_decoding_it(tmp_path, method, flags):
    source, target = tmp_path / 'src', tmp_path / 'out'
    _fixture(source, method=method, flags=flags)
    updateEpub(source, target, 'chapter.xhtml', b'new chapter')
    with zipfile.ZipFile(target) as out:
        assert out.namelist() == ['chapter.xhtml']
        assert out.read('chapter.xhtml') == b'new chapter'
        assert out.getinfo('chapter.xhtml').flag_bits & 1 == 0
        assert out.testzip() is None


def test_cover_delivery_does_not_inflate_unchanged_chapters_for_validation(tmp_path, monkeypatch):
    """A full-archive testzip after raw copying would restore size-dependent work."""
    from cps import helper
    from cps.services import user_cover

    source = tmp_path / 'source.epub'
    with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('cover.jpg', b'old cover')
        archive.writestr('chapter.xhtml', b'chapter text ' * 1000)
    monkeypatch.setattr(user_cover, 'override_for_user', lambda *_, **__: object())
    monkeypatch.setattr(user_cover, '_cover_member', lambda _: ('cover.jpg', '.jpg'))
    monkeypatch.setattr(user_cover, 'path_for_row', lambda _: 'personal.jpg')
    monkeypatch.setattr(user_cover, '_encode_for_member', lambda *_: b'new cover')
    monkeypatch.setattr(helper, 'get_temp_dir', lambda: str(tmp_path / 'deliveries'))
    original = zipfile.ZipFile.open

    def open_member(self, name, mode='r', *args, **kwargs):
        member = name.filename if isinstance(name, zipfile.ZipInfo) else name
        assert mode != 'r' or member != 'chapter.xhtml', 'delivery inflated an unchanged chapter'
        return original(self, name, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'open', open_member)
    delivered = user_cover.materialize_delivery_copy(1, 2, str(source), 'epub')
    assert delivered is not None
    directory, stem = delivered
    with zipfile.ZipFile(str(directory) + '/' + stem + '.epub') as archive:
        assert archive.read('cover.jpg') == b'new cover'


def test_zip64_central_sizes_are_rebuilt_for_raw_and_replacement(tmp_path, monkeypatch):
    """Exercise ZIP64 size regeneration cheaply, in addition to real 4 GiB offsets."""
    from cps.epub_helper import rewrite_zip_members

    source, target = tmp_path / 'src', tmp_path / 'out'
    payload, local = _fixture(source, zip64=True, descriptor=True, central_zip64=True)
    monkeypatch.setattr(zipfile, 'ZIP64_LIMIT', 200)
    replacement = b'new metadata ' * 1000
    rewrite_zip_members(source, target, {'new.opf': replacement})
    with zipfile.ZipFile(target) as out:
        assert out.testzip() is None
        assert out.read('chapter.xhtml') == payload
        assert out.read('new.opf') == replacement
        assert out.getinfo('chapter.xhtml').file_size == len(payload)
        assert out.getinfo('new.opf').file_size == len(replacement)
        assert out.getinfo('chapter.xhtml').extra.startswith(_extra(1, struct.pack(
            '<QQ', len(payload), out.getinfo('chapter.xhtml').compress_size)))
        new = out.getinfo('new.opf')
        assert struct.unpack_from('<HHQQQ', new.extra) == (
            1, 24, len(replacement), new.compress_size, new.header_offset)
        assert target.read_bytes()[:len(local)] == local


@pytest.mark.parametrize('flags', [
    0, 1 << 1, 1 << 2, 1 << 4, 1 << 7, 1 << 8, 1 << 9,
    1 << 10, 1 << 11, 1 << 12, 1 << 14, 1 << 15, 0x816, 0xDF96,
])
def test_compression_encoding_and_opaque_flags_do_not_trigger_recompression(tmp_path, monkeypatch, flags):
    """APPNOTE 4.4.4: only flags altering safe interpretation block raw copy."""
    source, target = tmp_path / 'src', tmp_path / 'out'
    payload, local = _fixture(source, flags=flags)
    original = zipfile.ZipFile.read

    def read(self, name, *args, **kwargs):
        assert self.filename != str(source), 'flag alone sent unchanged data to the decoder'
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'read', read)
    updateEpub(source, target, 'new.opf', b'new')
    with zipfile.ZipFile(target) as out:
        assert out.read('chapter.xhtml') == payload
        assert out.getinfo('chapter.xhtml').flag_bits == flags
        assert target.read_bytes()[:len(local)] == local


@pytest.mark.parametrize('bit', [0, 5, 6, 13])
@pytest.mark.parametrize('other_flags', [0, 0x816])
def test_encryption_patch_and_masking_flags_still_use_legacy_path(tmp_path, monkeypatch, bit, other_flags):
    """Never certify ciphertext, patched data or masked headers as ordinary records."""
    source, target = tmp_path / 'src', tmp_path / 'out'
    _fixture(source, flags=other_flags | (1 << bit))
    reads = []

    class LegacyRead(Exception):
        pass

    def read(self, name, *args, **kwargs):
        assert self.filename == str(source)
        reads.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
        raise LegacyRead()

    monkeypatch.setattr(zipfile.ZipFile, 'read', read)
    with pytest.raises(LegacyRead):
        updateEpub(source, target, 'new.opf', b'new')
    assert reads == ['chapter.xhtml']


@pytest.mark.parametrize('signature,zip64', [(False, False), (True, False), (False, True), (True, True)])
def test_flagged_members_keep_their_validated_descriptors(tmp_path, monkeypatch, signature, zip64):
    source, target = tmp_path / 'src', tmp_path / 'out'
    payload, local = _fixture(source, flags=0x816, descriptor=True, signature=signature, zip64=zip64)
    original = zipfile.ZipFile.read

    def read(self, name, *args, **kwargs):
        assert self.filename != str(source), 'flagged descriptor member was decompressed'
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'read', read)
    updateEpub(source, target, 'new.opf', b'new')
    with zipfile.ZipFile(target) as out:
        assert out.getinfo('chapter.xhtml').flag_bits == 0x81E
        assert out.read('chapter.xhtml') == payload
        assert target.read_bytes()[:len(local)] == local


def test_lzma_eos_flag_does_not_require_decoding(tmp_path, monkeypatch):
    source, target = tmp_path / 'src', tmp_path / 'out'
    payload = b'chapter words ' * 3000
    with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_LZMA) as archive:
        archive.writestr('chapter', payload)
    with zipfile.ZipFile(source) as archive:
        info = archive.getinfo('chapter')
        assert info.flag_bits & 2  # LZMA's EOS marker, not a deflate option
        local = _local_record(source, info)
    original = zipfile.ZipFile.read

    def read(self, name, *args, **kwargs):
        assert self.filename != str(source), 'LZMA EOS member was decompressed'
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'read', read)
    updateEpub(source, target, 'new.opf', b'new')
    with zipfile.ZipFile(target) as out:
        assert out.read('chapter') == payload
        assert _local_record(target, out.getinfo('chapter')) == local
