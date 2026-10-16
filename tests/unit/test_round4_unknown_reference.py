"""A confident unknown marker cannot erase a source-proven selected reference."""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline
_spec = importlib.util.spec_from_file_location('round4_unknown_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


@pytest.mark.parametrize('selected_marker', ['7', '9'])
def test_confident_unknown_fallback_rejects_known_in_scope_but_allows_out_of_scope(tmp_path, monkeypatch, selected_marker):
    origin, notes = h.InkPage(), h.InkPage()
    origin.reference(raised=True, corrupted=True)
    notes.text(selected_marker + ' A synthetic selected note preserves its own source meaning.', 'note', 'footnote', y=320, size=7, marker=selected_marker, model='A synthetic selected note preserves its own source meaning.')
    actual = vision_pipeline._literal_note_refs
    calls = []
    rejected = []
    def unknown(image):
        assert image[:2] == b'\xff\xd8'
        calls.append(image)
        return {'label': '99', 'uncertain': False, 'cached': True, 'new_cost_usd': 0}
    def refs(tokens, optical, display, box, cache, labels, pno, context):
        context['marker_reader'] = unknown
        try:
            return actual(tokens, optical, display, box, cache, labels, pno, context)
        except ValueError as error:
            rejected.append(str(error))
            raise
    monkeypatch.setattr(vision_pipeline, '_literal_note_refs', refs)
    if selected_marker == '7':
        path, report, docs = h.convert_ink(tmp_path, monkeypatch, [origin, notes])
        assert rejected, 'strict source-reference helper must still reject unknown selected label'
        _assert_disclosed_source_unit(path, report, 0)
        emitted_refs, emitted_notes = h.refs_and_notes(docs)
        assert emitted_refs == [], 'unknown printed label cannot invent a normal note owner link'
        assert len(emitted_notes) == 1, 'healthy selected note must remain live exactly once'
        assert 'synthetic selected note preserves' in ''.join(emitted_notes[0][1].itertext())
    else:
        _, _, docs = h.convert_ink(tmp_path, monkeypatch, [origin, notes])
        emitted_refs, emitted_notes = h.refs_and_notes(docs)
        assert emitted_refs == [], 'source reference7 has no owner in this selected scope; do not invent note9 link'
        assert len(emitted_notes) == 1
        assert h.body_texts(docs), 'out-of-scope reference cannot drop ordinary live body'
    assert len(calls) == 1, 'actual source-proven raised marker reached compact fallback'


def _assert_disclosed_source_unit(path, report, page):
    import io
    import zipfile
    from PIL import Image
    units = [u for u in report.get('degraded_units', []) if u['page'] == page]
    assert units, 'public conversion must report the affected SOURCE unit'
    assert all(u.get('stage') and u.get('reason') for u in units)
    with zipfile.ZipFile(path) as archive:
        for unit in units:
            member = unit['source_image']
            member = member if member.startswith('OEBPS/') else 'OEBPS/' + member
            assert member in archive.namelist(), 'failed source reading must remain in actual ZIP'
            pixels = Image.open(io.BytesIO(archive.read(member))).convert('L')
            assert pixels.width > 5 and pixels.height > 5
            assert pixels.getextrema()[0] < 100, 'SOURCE replacement cannot silently discard original ink'
    from xml.etree import ElementTree as ET
    import posixpath
    with zipfile.ZipFile(path) as archive:
        roots = {name: ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith('.xhtml')}
        shown = {posixpath.normpath(posixpath.join(posixpath.dirname(name), image.get('src'))) for name, root in roots.items() for image in root.findall('.//x:img', h.NS)}
        for unit in units:
            member = unit['source_image']
            member = member if member.startswith('OEBPS/') else 'OEBPS/' + member
            assert member in shown, 'retained SOURCE image must be visible in actual publication, not an unused ZIP asset'
        assert any('source-disclosure-ref' in (a.get('class') or '').split() for root in roots.values() for a in root.findall('.//x:a', h.NS)), 'affected source unit must have a visible disclosed source control'
    assert set(map(int, report['page_homes'])) == {0, 1}
