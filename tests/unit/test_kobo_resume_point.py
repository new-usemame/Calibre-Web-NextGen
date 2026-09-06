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


@pytest.mark.parametrize('encoded_length', [50000, 500])
def test_repeated_spine_references_bound_path_decoding(epub, monkeypatch, encoded_length):
    """A tiny EPUB must not multiply href decoding work by its 40,000 itemrefs."""
    from urllib import parse
    href = '%41' * encoded_length
    source = 'OEBPS/chapter.xhtml'
    with zipfile.ZipFile(epub, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('META-INF/container.xml', CONTAINER_XML)
        archive.writestr('OEBPS/content.opf', OPF_TEMPLATE.format(
            book_uuid='fixture-book',
            manifest_items=f'<item id="chapter" href="{href}" media-type="application/xhtml+xml"/>',
            spine_items='<itemref idref="chapter"/>' * 40000))
    assert epub.stat().st_size < 4000
    decoded_bytes = 0
    budget = len(source) + len(href)
    unquote = parse.unquote

    def measured_unquote(value, *args, **kwargs):
        nonlocal decoded_bytes
        decoded_bytes += len(value.encode('utf-8'))
        # Stop a broken implementation promptly; assert outside its fallback
        # exception handler so swallowing this exception cannot pass the test.
        if decoded_bytes > budget:
            raise ValueError('path decode work exceeded one source plus one href')
        return unquote(value, *args, **kwargs)

    monkeypatch.setattr(parse, 'unquote', measured_unquote)
    point = kobo_position.compute_cfi_point(epub, source, 'KoboSpan', 'kobo.1.2')
    print(f'href bytes={len(href)}, decoded bytes={decoded_bytes}, budget={budget}, point={point}')
    assert decoded_bytes <= budget
    assert point is None


@pytest.mark.parametrize('alias', [
    'OEBPS/./chapter.xhtml',
    'OEBPS/extra/../chapter.xhtml',
    'OEBPS//chapter.xhtml',
    'OEBPS/chapter.xhtml',
])
def test_ambiguous_zip_paths_never_supply_exact_resume(epub, alias):
    """JSZip may replace the chapter with a later normalized alias despite a matching hash."""
    chapter = _kobo_chapter_html([('kobo.1.2', 'WRONG CHAPTER TEXT.')])
    chapter = chapter.replace('<body>', '<body><p>Leading paragraph.</p>')
    with zipfile.ZipFile(epub, 'a', zipfile.ZIP_DEFLATED) as archive:
        if alias == 'OEBPS/chapter.xhtml':
            with pytest.warns(UserWarning, match='Duplicate name'):
                archive.writestr(alias, chapter)
        else:
            archive.writestr(alias, chapter)
    snapshot = kobo_position._resume_snapshot(
        epub, 'OEBPS/chapter.xhtml', 'KoboSpan', 'kobo.1.2')
    print(f'alias={alias}, snapshot={snapshot}')
    assert snapshot is None


def _add_selection_difference(epub, difference, chapter=None):
    """An unused member must not change the browser's interpretation of the ZIP."""
    import struct
    if difference == 'selected-directory':
        with zipfile.ZipFile(epub) as archive:
            contents = {name: archive.read(name) for name in archive.namelist()}
        contents['META-INF/container.xml'] = contents['META-INF/container.xml'].replace(b'content.opf', b'content.opf/')
        contents['OEBPS/content.opf/'] = contents.pop('OEBPS/content.opf').replace(b'chapter.xhtml', b'../chapter.xhtml')
        with zipfile.ZipFile(epub, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, raw in contents.items():
                archive.writestr(name, raw)
        return
    name = 'OEBPS/another.xhtml'
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    if difference == 'zip64-extra':
        info.extra = struct.pack('<HH', 1, 0)
    elif difference == 'legacy-name':
        info.filename = 'OEBPS/café.xhtml'
    elif difference == 'dos-directory':
        info.external_attr = 0x10
    elif difference == 'unix-directory':
        info.create_system = 3
        info.external_attr = 0o40755 << 16
    with zipfile.ZipFile(epub, 'a') as archive:
        archive.writestr(info, chapter or _kobo_chapter_html([
            ('kobo.1.2', 'WRONG CHAPTER TEXT.')]).replace('<body>', '<body><p>Leading paragraph.</p>'))
    raw = bytearray(epub.read_bytes())
    with zipfile.ZipFile(epub) as archive:
        member = archive.infolist()[-1]
        local = member.header_offset
        central = archive.start_dir
        for entry in archive.infolist()[:-1]:
            n, e, c = struct.unpack_from('<3H', raw, central + 28)
            central += 46 + n + e + c
    if difference == 'local-name':
        raw[local + 30:local + 30 + len(name)] = b'OEBPS/chapter.xhtml'
    elif difference == 'legacy-name':
        for offset in (local + 6, central + 8):
            flags = struct.unpack_from('<H', raw, offset)[0]
            struct.pack_into('<H', raw, offset, flags & ~0x800)
    elif difference == 'local-flags':
        struct.pack_into('<H', raw, local + 6, 0x800)
    elif difference == 'local-method':
        struct.pack_into('<H', raw, local + 8, zipfile.ZIP_STORED)
    elif difference == 'local-bounds':
        struct.pack_into('<H', raw, local + 28, 65535)
    epub.write_bytes(raw)


@pytest.mark.parametrize('difference', [
    'local-name', 'legacy-name', 'dos-directory',
    'unix-directory', 'local-flags', 'local-method', 'local-bounds',
    'zip64-extra', 'selected-directory',
])
def test_all_members_have_one_browser_selection(epub, difference):
    """Issue #324: validating only selected XML cannot establish archive identity."""
    _add_selection_difference(epub, difference)
    snapshot = kobo_position._resume_snapshot(epub, 'OEBPS/chapter.xhtml', 'KoboSpan', 'kobo.1.2')
    print(f'difference={difference}, snapshot={snapshot}')
    assert snapshot is None, f'{difference} must retain percentage resume'


def test_overlong_manifest_href_is_rejected_before_decoding(epub, monkeypatch):
    """Pin the href guard independently of repeated-spine ambiguity or XML errors."""
    from urllib import parse
    with zipfile.ZipFile(epub) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    href = '%41' * 683  # 2049 encoded characters; an unused manifest item.
    contents['OEBPS/content.opf'] = contents['OEBPS/content.opf'].replace(
        b'</manifest>', f'<item id="unused" href="{href}"/></manifest>'.encode())
    with zipfile.ZipFile(epub, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in contents.items():
            archive.writestr(name, raw)
    decoded = []
    unquote = parse.unquote
    def measured(value, *args, **kwargs):
        decoded.append(len(value))
        return unquote(value, *args, **kwargs)
    monkeypatch.setattr(parse, 'unquote', measured)
    point = kobo_position.compute_cfi_point(epub, 'OEBPS/chapter.xhtml', 'KoboSpan', 'kobo.1.2')
    print(f'href={len(href)}, decoded lengths={decoded}, point={point}')
    assert max(decoded) <= 2048, 'oversized href reached path decoding'
    assert point is None


@pytest.mark.parametrize('field', ['size', 'crc'])
def test_member_integrity_is_checked_after_bounded_inflation(epub, field):
    """Valid XML with false metadata must fail the final check, not the XML parser."""
    import struct
    raw = bytearray(epub.read_bytes())
    with zipfile.ZipFile(epub) as archive:
        info = archive.getinfo('META-INF/container.xml')
        central = archive.start_dir
    if field == 'size':
        struct.pack_into('<I', raw, info.header_offset + 22, info.file_size + 1)
        struct.pack_into('<I', raw, central + 24, info.file_size + 1)
    else:
        struct.pack_into('<I', raw, info.header_offset + 14, info.CRC ^ 1)
        struct.pack_into('<I', raw, central + 16, info.CRC ^ 1)
    epub.write_bytes(raw)
    snapshot = kobo_position._resume_snapshot(epub, 'OEBPS/chapter.xhtml', 'KoboSpan', 'kobo.1.2')
    print(f'false {field}, snapshot={snapshot}')
    assert snapshot is None, f'valid XML must not bypass the final {field} check'
