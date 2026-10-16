"""Operation audit binds the final body while retaining deferred uncertainty."""
import zipfile
from xml.etree import ElementTree as ET

import pytest
from cps.services.reflow import operation_audit as audit
from tests.unit.test_reflow_operation_audit import image_quote, source

pytestmark = pytest.mark.unit

X = audit.N


def _chapter(path):
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist()
                    if n.startswith('OEBPS/ch') and n.endswith('.xhtml'))
        return name, ET.fromstring(archive.read(name))


def _chapters(path):
    with zipfile.ZipFile(path) as archive:
        return [(name, ET.fromstring(archive.read(name)))
                for name in archive.namelist()
                if name.startswith('OEBPS/ch') and name.endswith('.xhtml')]


def test_audit_projects_final_body_and_keeps_deferred_uncertainty(image_quote):
    result, doc, path = image_quote
    rows, matching = audit.capture(result, 567, document=doc)
    material = matching[rows[0]['candidate_id']]
    assert 'source-reading-annotation' not in repr(material['projection'])
    assert 'Uncertain source mark' not in repr(material['projection'])
    assert audit.observe(path, rows, matching)[0]['status'] == 'verified'

    _, root = _chapter(path)
    quote = next(root.iter(X + 'blockquote'))
    quote_text = ''.join(quote.itertext())
    marker = next(quote.iter(X + 'sup'))
    uncertain = next(s for s in marker.iter(X + 'span')
                     if s.get('class') == 'reflow-uncertain')
    assert ''.join(marker.itertext()) == '7'
    assert 'damaged text layer' in uncertain.get('title', '')
    assert '(?)' not in quote_text
    assert 'Uncertain source mark' not in quote_text

    notices = [s for _, chapter in _chapters(path) for s in chapter.iter(X + 'section')
               if 'source-notes' in s.get('class', '').split()]
    assert len(notices) == 1
    annotations = [s for s in notices[0].iter(X + 'span')
                   if 'source-reading-annotation' in s.get('class', '').split()
                   and 'Uncertain source mark 7 (?)' in ''.join(s.itertext())]
    assert len(annotations) == 1
    source_marker = next(run for run in result.book.pages[0][1].runs
                         if run[0] == 'sup')
    assert source_marker == ['sup', '7', 0, 'uncertain']
    assert result.book.notes[0].uncertain is True


@pytest.mark.parametrize('mutation', ['annotation', 'source_word', 'role', 'glyph_pixels'])
def test_final_body_audit_rejects_annotation_or_content_tampering(image_quote, mutation):
    result, doc, path = image_quote
    rows, matching = audit.capture(result, 567, document=doc)
    assert audit.observe(path, rows, matching)[0]['status'] == 'verified'
    name, root = _chapter(path)
    quote = next(root.iter(X + 'blockquote'))
    if mutation == 'annotation':
        fake = ET.Element(X + 'span', {'class': 'source-reading-annotation'})
        fake.text = 'Invented notice (?)'
        quote.insert(0, fake)
    elif mutation == 'source_word':
        quote.text = 'Invented source words '
    elif mutation == 'role':
        quote.tag = X + 'p'
    else:
        images = [node for node in quote.iter(X + 'img')]
        assert images
        resource = 'OEBPS/' + images[0].get('src')
        with zipfile.ZipFile(path) as archive:
            files = {entry: archive.read(entry) for entry in archive.namelist()}
        files[resource] += b'forged glyph pixels'
    if mutation != 'glyph_pixels':
        with zipfile.ZipFile(path) as archive:
            files = {entry: archive.read(entry) for entry in archive.namelist()}
    files[name] = ET.tostring(root)
    with zipfile.ZipFile(path, 'w') as archive:
        for entry, data in files.items():
            archive.writestr(entry, data)
    assert audit.observe(path, rows, matching)[0]['status'] == 'not_found'
