"""Source-free native prepared-cache publication controls, authored by independent measurer.

Real native PDF geometry proves repeated marginal furniture; stale prepared runs
have concatenated it into live paragraphs. No model, OCR, library input or scorer.
"""
import re
import zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import pymupdf
from cps.services.reflow import assemble, build_epub, vision_pipeline

NS = {'x': 'http://www.w3.org/1999/xhtml'}
HEADER = 'RIVER STUDIES'
AUTHORED = 'The phrase RIVER STUDIES belongs to this sentence.'
CHAPTER = 'Sources and Journeys'

class OfflineNativeReader:
    def __init__(self, cache):
        self.cache = cache
        self.calls = 0
    def read(self, image):
        self.calls += 1
        raise AssertionError('Native controls must never invoke a model reading')
    def read_marker(self, image):
        raise AssertionError('Native controls have no printed note request')

def published_body(epub):
    paragraphs = []
    headings = []
    with zipfile.ZipFile(epub) as z:
        for name in z.namelist():
            if not re.fullmatch(r'OEBPS/ch\d+\.xhtml', name):
                continue
            root = ET.fromstring(z.read(name))
            parents = {child: parent for parent in root.iter() for child in parent}
            for node in root.iter():
                local = node.tag.rsplit('}', 1)[-1]
                if local not in ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                    continue
                ancestor = parents.get(node)
                excluded = False
                while ancestor is not None:
                    if ancestor.tag.rsplit('}', 1)[-1] in ('aside', 'nav'):
                        excluded = True
                    if 'notes' in ancestor.get('class', '').split():
                        excluded = True
                    ancestor = parents.get(ancestor)
                if excluded:
                    continue
                text = ' '.join(''.join(node.itertext()).split())
                (paragraphs if local == 'p' else headings).append(text)
    return paragraphs, headings

def native_fixture(tmp_path, stale):
    doc = pymupdf.open()
    pages = {}
    for n in range(3):
        page = doc.new_page(width=431.97, height=647.97)
        if stale:
            page.insert_text((54, 48), HEADER, fontsize=10)
            page.insert_text((370, 48), str(201 + n), fontsize=10)
        elements = []
        if n == 0:
            page.insert_text((54, 76), CHAPTER, fontsize=12)
            elements.append(assemble.Element('h', runs=[['t', CHAPTER]], pno=n,
                level=1, bbox=(54, 61, 220, 80)))
            page.insert_text((54, 103), AUTHORED, fontsize=11)
            elements.append(assemble.Element('p', runs=[['t', AUTHORED]], pno=n,
                bbox=(54, 90, 365, 108)))
            text = 'A native sentence moves through the valley'
            page.insert_text((54, 133), text, fontsize=11)
            cached = text + (' ' + HEADER + ' 201' if stale else '')
            elements.append(assemble.Element('p', runs=[['t', cached]], pno=n,
                bbox=(54, 36 if stale else 120, 391, 137)))
        elif n == 1:
            text = 'before entering the sea.'
            page.insert_text((54, 83), text, fontsize=11)
            cached = (HEADER + ' 202 ' if stale else '') + text
            elements.append(assemble.Element('p', runs=[['t', cached]], pno=n,
                bbox=(54, 36 if stale else 70, 391, 87)))
        else:
            text = 'The final paragraph gives a separate complete observation.'
            page.insert_text((54, 83), text, fontsize=11)
            cached = text + (' ' + HEADER + ' 203' if stale else '')
            elements.append(assemble.Element('p', runs=[['t', cached]], pno=n,
                bbox=(54, 36 if stale else 70, 391, 87)))
        pages[n] = elements
    book = assemble.Book(elements=[e for els in pages.values() for e in els], pages=pages)
    assert all(vision_pipeline.clean_native(p) for p in doc)
    reader = OfflineNativeReader(tmp_path / 'unused-reader-cache')
    reader.cache.mkdir()
    out = tmp_path / 'native-furniture.epub'
    try:
        report = vision_pipeline.convert(doc, str(out), reader,
            native_prepared=SimpleNamespace(book=book))
        assert reader.calls == 0
        assert report['vision_pages'] == [] and report['native_pages'] == [0, 1, 2]
        assert build_epub.validate(str(out)) == []
        return published_body(out)
    finally:
        doc.close()

def test_stale_native_furniture_removed_without_erasing_authored_same_words(tmp_path):
    paragraphs, headings = native_fixture(tmp_path, stale=True)
    body = ' '.join(paragraphs)
    assert AUTHORED in paragraphs, 'identical title words authored in body remain live'
    assert CHAPTER in headings, 'real chapter heading below marginal furniture remains live'
    assert body.count(HEADER) == 1, 'only the authored body occurrence survives repeated native marginal header removal'
    assert not any(re.search(r'\b20[123]\b', p) for p in paragraphs), 'proved top-margin folios cannot remain inside body'

def test_stale_native_furniture_does_not_interrupt_page_turn_sentence(tmp_path):
    paragraphs, headings = native_fixture(tmp_path, stale=True)
    assert any('A native sentence moves through the valley before entering the sea.' in p for p in paragraphs), 'source marginal header and folio cannot split or contaminate native page-turn sentence'

def test_clean_native_body_and_chapter_heading_survive_without_margin_repetition(tmp_path):
    paragraphs, headings = native_fixture(tmp_path, stale=False)
    assert AUTHORED in paragraphs
    assert CHAPTER in headings
    assert ' '.join(paragraphs).count(HEADER) == 1
    assert any('A native sentence moves through the valley before entering the sea.' in p for p in paragraphs)
