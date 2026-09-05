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
