"""Source-record metadata changes only the OPF, preserving graded reading bytes."""
import uuid
import zipfile
from xml.etree import ElementTree as ET

from cps.services.reflow import vision_pipeline as vp
from tests.unit.round7_recovery_test_support import fixture


def test_source_record_metadata_reaches_opf_without_changing_reading_members(tmp_path, monkeypatch):
    monkeypatch.setattr(uuid, 'uuid4', lambda: uuid.UUID(int=1))
    doc, reader, _ = fixture(tmp_path, monkeypatch)
    original, labelled = tmp_path / 'original.epub', tmp_path / 'labelled.epub'
    metadata = {'title': 'A title & its <source>',
                'authors': ['First Author', 'Second Author'], 'language': 'eng'}
    try:
        vp.convert(doc, str(original), reader)
        vp.convert(doc, str(labelled), reader, metadata=metadata)
    finally:
        doc.close()
    with zipfile.ZipFile(original) as before, zipfile.ZipFile(labelled) as after:
        assert before.namelist() == after.namelist()
        container = ET.fromstring(after.read('META-INF/container.xml'))
        opf = container.find('.//{*}rootfile').get('full-path')
        assert all(before.read(n) == after.read(n) for n in before.namelist() if n != opf)
        root = ET.fromstring(after.read(opf))
        dc = '{http://purl.org/dc/elements/1.1/}'
        assert root.find('.//' + dc + 'title').text == metadata['title']
        assert [e.text for e in root.findall('.//' + dc + 'creator')] == metadata['authors']
        assert root.find('.//' + dc + 'language').text == metadata['language']
