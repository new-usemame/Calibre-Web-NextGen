"""Keep CSS declarations coherent with the existing EPUB writer's output encoding."""
import hashlib
import zipfile

import pytest

from tests.unit.test_kindle_epub_fixer_idempotency import (
    build_epub, epub_payload, fixer_module, backups_taken,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('codec,label,text', [
    ('iso-8859-1', 'iso-8859-1', 'café'),
    ('cp1252', 'windows-1252', '“café”'),
    ('ascii', 'iso-8859-1', 'plain ASCII'),
    ('utf-8-sig', 'iso-8859-1', 'café'),
])
def test_complete_writer_aligns_css_declaration_and_preserves_text(fixer_module, tmp_path, codec, label, text):
    css = f'@charset "{label}";\np.note:before {{ content: "{text}"; }}\n'
    book = build_epub(tmp_path/'book.epub', css=css.encode(codec))
    before = epub_payload(book)
    result = fixer_module.EPUBFixer().process(str(book))
    after = epub_payload(book)
    expected = f'@charset "utf-8";\np.note:before {{ content: "{text}"; }}\n'
    assert after['OEBPS/page_styles.css'] == expected.encode('utf-8')
    assert after['OEBPS/page_styles.css'].decode('utf-8') == expected
    assert {name: data for name, data in after.items() if name != 'OEBPS/page_styles.css'} == {
        name: data for name, data in before.items() if name != 'OEBPS/page_styles.css'}
    assert result and len(backups_taken(fixer_module)) == 1
    checksum = hashlib.sha256(book.read_bytes()).hexdigest()
    mtime = book.stat().st_mtime_ns
    assert fixer_module.EPUBFixer().process(str(book)) == []
    assert hashlib.sha256(book.read_bytes()).hexdigest() == checksum
    assert book.stat().st_mtime_ns == mtime
    assert len(backups_taken(fixer_module)) == 1


@pytest.mark.parametrize('prefix', [
    '@charset "utf-8";',
    '/* @charset "iso-8859-1"; */',
    ' @charset "iso-8859-1";',
    "@charset 'iso-8859-1';",
    '@CHARSET "iso-8859-1";',
])
def test_utf8_or_non_declaration_css_is_byte_identical(fixer_module, tmp_path, prefix):
    css = (prefix + '\np.note:before { content: "café"; }\n').encode('utf-8')
    book = build_epub(tmp_path/'book.epub', css=css)
    checksum = hashlib.sha256(book.read_bytes()).hexdigest()
    assert fixer_module.EPUBFixer().process(str(book)) == []
    assert epub_payload(book)['OEBPS/page_styles.css'] == css
    assert hashlib.sha256(book.read_bytes()).hexdigest() == checksum
    assert backups_taken(fixer_module) == []


@pytest.mark.parametrize('codec,bom', [('utf-16-le', b'\xff\xfe'), ('utf-16-be', b'\xfe\xff')])
def test_bom_utf16_css_keeps_encoding_and_original_bytes(fixer_module, tmp_path, codec, bom):
    css = bom + '@charset "utf-16";\np.note:before { content: "café"; }\n'.encode(codec)
    book = build_epub(tmp_path/'book.epub', css=css)
    checksum = hashlib.sha256(book.read_bytes()).hexdigest()
    assert fixer_module.EPUBFixer().process(str(book)) == []
    assert epub_payload(book)['OEBPS/page_styles.css'] == css
    assert hashlib.sha256(book.read_bytes()).hexdigest() == checksum


def test_uppercase_css_keeps_existing_binary_policy(fixer_module, tmp_path):
    book = build_epub(tmp_path/'book.epub')
    css = '@charset "iso-8859-1";\np.note:before { content: "café"; }\n'.encode('iso-8859-1')
    with zipfile.ZipFile(book, 'a') as archive:
        archive.writestr('OEBPS/UPPER.CSS', css)
    checksum = hashlib.sha256(book.read_bytes()).hexdigest()
    assert fixer_module.EPUBFixer().process(str(book)) == []
    assert epub_payload(book)['OEBPS/UPPER.CSS'] == css
    assert hashlib.sha256(book.read_bytes()).hexdigest() == checksum
