"""Kobo progress identifies a span start, not a highlight or an arbitrary book CFI."""
import zipfile

import pytest

from cps.services import kobo_position
from tests.fixtures.kepub_fixture import CONTAINER_XML, OPF_TEMPLATE, _kobo_chapter_html


@pytest.fixture
def epub(tmp_path):
    path = tmp_path / 'reader.epub'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('META-INF/container.xml', CONTAINER_XML)
        archive.writestr('OEBPS/content.opf', OPF_TEMPLATE.format(
            book_uuid='fixture-book',
            manifest_items='<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>',
            spine_items='<itemref idref="chapter"/>'))
        archive.writestr('OEBPS/chapter.xhtml', _kobo_chapter_html([
            ('kobo.1.1', 'First sentence.'), ('kobo.1.2', 'Exact reading position.')]))
    return path


def test_progress_resolves_to_span_start_in_the_requested_epub(epub):
    """A real archive and location produce a point with a text node at offset zero."""
    assert kobo_position.compute_cfi_point(epub, 'OEBPS/chapter.xhtml', 'KoboSpan', 'kobo.1.2') == (
        'epubcfi(/6/2!/4/2/4[kobo.1.2]/1:0)')


def test_unresolvable_progress_never_fabricates_a_point(epub):
    for source, kind, value in [
        ('OEBPS/missing.xhtml', 'KoboSpan', 'kobo.1.2'),
        ('elsewhere/chapter.xhtml', 'KoboSpan', 'kobo.1.2'),
        ('OEBPS/chapter.xhtml', 'KoboSpan', 'kobo.9.9'),
        ('OEBPS/chapter.xhtml', 'Other', 'kobo.1.2'),
        ('OEBPS/chapter.xhtml', 'KoboSpan', 'epubcfi(/6/2)'),
        ('', 'KoboSpan', 'kobo.1.2'),
    ]:
        assert kobo_position.compute_cfi_point(epub, source, kind, value) is None
    assert kobo_position.compute_cfi_point(epub.with_name('missing.epub'),
        'OEBPS/chapter.xhtml', 'KoboSpan', 'kobo.1.2') is None
    epub.write_bytes(b'being rewritten')
    assert kobo_position.compute_cfi_point(epub, 'OEBPS/chapter.xhtml', 'KoboSpan', 'kobo.1.2') is None


def test_oversized_compressed_chapter_falls_back_without_parsing(epub):
    """Small compressed archives can contain oversized documents; bound inflated work too."""
    with zipfile.ZipFile(epub) as original:
        contents = {name: original.read(name) for name in original.namelist()}
    contents['OEBPS/chapter.xhtml'] = _kobo_chapter_html([
        ('kobo.1.2', 'x' * (2 * 1024 * 1024))]).encode()
    with zipfile.ZipFile(epub, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in contents.items():
            archive.writestr(name, raw)
    assert epub.stat().st_size < 10000
    assert kobo_position.compute_cfi_point(epub, 'OEBPS/chapter.xhtml', 'KoboSpan', 'kobo.1.2') is None


@pytest.mark.parametrize('hidden_bytes', [32 * 1024 * 1024, 16])
def test_forged_member_size_bounds_actual_inflation(epub, monkeypatch, hidden_bytes):
    """A valid-CRC prefix must neither hide oversized inflation nor legitimize a false size."""
    import struct
    import zlib
    with zipfile.ZipFile(epub) as original:
        contents = {name: original.read(name) for name in original.namelist()}
    name = 'META-INF/container.xml'
    prefix = contents.pop(name)
    with zipfile.ZipFile(epub, 'w', zipfile.ZIP_DEFLATED) as archive:
        with archive.open(name, 'w') as member:
            member.write(prefix)
            for offset in range(0, hidden_bytes, 65536):
                member.write(b'x' * min(65536, hidden_bytes - offset))
        for member_name, raw in contents.items():
            archive.writestr(member_name, raw)
    raw = bytearray(epub.read_bytes())
    central = struct.unpack_from('<I', raw, len(raw) - 6)[0]
    # The first member is the forged one. Both headers claim only the XML
    # prefix, including its correct CRC; the compressed stream contains more.
    struct.pack_into('<I', raw, 14, zlib.crc32(prefix))
    struct.pack_into('<I', raw, 22, len(prefix))
    struct.pack_into('<I', raw, central + 16, zlib.crc32(prefix))
    struct.pack_into('<I', raw, central + 24, len(prefix))
    epub.write_bytes(raw)
    factory = zlib.decompressobj
    emitted = []
    class MeasuredInflater:
        def __init__(self, *args, **kwargs):
            self.inner = factory(*args, **kwargs)
        def decompress(self, *args, **kwargs):
            output = self.inner.decompress(*args, **kwargs)
            emitted.append(len(output))
            return output
        def flush(self, *args, **kwargs):
            output = self.inner.flush(*args, **kwargs)
            emitted.append(len(output))
            return output
        def __getattr__(self, name):
            return getattr(self.inner, name)
    monkeypatch.setattr(zlib, 'decompressobj', MeasuredInflater)
    point = kobo_position.compute_cfi_point(epub, 'OEBPS/chapter.xhtml', 'KoboSpan', 'kobo.1.2')
    print(f'hidden={hidden_bytes}, largest inflation={max(emitted, default=0)}, point={point}')
    assert max(emitted, default=0) <= 2 * 1024 * 1024 + 1, emitted
    assert point is None, 'a false size and prefix CRC must not be accepted'


@pytest.mark.parametrize('records', [2049, 200000])
def test_directory_limits_precede_zipinfo_allocation(epub, monkeypatch, records):
    """Count actual directory records, not the forged EOCD count, before allocating metadata."""
    import io
    import struct
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('x', b'')
    raw = buffer.getvalue()
    central = struct.unpack_from('<I', raw, len(raw) - 6)[0]
    record = raw[central:-22]
    directory = record * records
    # EOCD lies about the record count. ZipFile itself reads to the byte size.
    end = struct.pack('<4s4H2IH', b'PK\x05\x06', 0, 0, 1, 1, len(directory), 0, 0)
    epub.write_bytes(directory + end)
    allocated = 0
    original_init = zipfile.ZipInfo.__init__
    def measured_init(self, *args, **kwargs):
        nonlocal allocated
        allocated += 1
        original_init(self, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipInfo, '__init__', measured_init)
    assert kobo_position.compute_cfi_point(epub, 'OEBPS/chapter.xhtml', 'KoboSpan', 'kobo.1.2') is None
    print(f'directory records={records}, ZipInfo allocations={allocated}')
    assert allocated == 0, f'allocated {allocated} directory records before rejecting the archive'


@pytest.mark.parametrize('ancestor_id', ['a/b', 'a:9', 'café'])
def test_unsafe_ancestor_assertion_falls_back(epub, ancestor_id):
    """Only proven assertion characters may reach epub.js; valid XHTML ids can be unsafe CFIs."""
    with zipfile.ZipFile(epub) as original:
        contents = {name: original.read(name) for name in original.namelist()}
    contents['OEBPS/chapter.xhtml'] = contents['OEBPS/chapter.xhtml'].replace(
        b'<p>', f'<p id="{ancestor_id}">'.encode())
    with zipfile.ZipFile(epub, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in contents.items():
            archive.writestr(name, raw)
    assert kobo_position.compute_cfi_point(epub, 'OEBPS/chapter.xhtml', 'KoboSpan', 'kobo.1.2') is None
