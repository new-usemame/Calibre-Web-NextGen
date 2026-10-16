"""A proven printed reference may not silently disappear after uncertain OCR."""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline
_spec=importlib.util.spec_from_file_location('round4_uncertain_helpers',Path(__file__).with_name('test_round4_defects.py'))
h=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


def test_proven_raised_reference_with_uncertain_fallback_fails_publication(tmp_path,monkeypatch):
    origin,notes=h.InkPage(),h.InkPage()
    origin.reference(raised=True,corrupted=True)
    notes.text('7 A synthetic source note owns the raised reference.', 'note','footnote',y=320,size=7,marker='7',model='A synthetic source note owns the raised reference.')
    actual=vision_pipeline._literal_note_refs
    calls=[]
    rejected=[]
    def uncertain(image):
        assert image[:2]==b'\xff\xd8', 'fallback must receive source crop JPEG'
        calls.append(image)
        return {'label':'','uncertain':True,'cached':True,'new_cost_usd':0}
    def refs(tokens,optical,display,box,cache,labels,pno,context):
        context['marker_reader']=uncertain
        try:
            return actual(tokens,optical,display,box,cache,labels,pno,context)
        except ValueError as error:
            rejected.append(str(error))
            raise
    monkeypatch.setattr(vision_pipeline,'_literal_note_refs',refs)
    path,report,docs=h.convert_ink(tmp_path,monkeypatch,[origin,notes])
    assert rejected, 'strict source-reference helper must still reject uncertain selected label'
    _assert_disclosed_source_unit(path,report,0)
    refs,selected_notes=h.refs_and_notes(docs)
    assert refs==[], 'uncertain physical glyph cannot invent a normal note owner'
    assert len(selected_notes)==1, 'healthy note remains live once'
    assert 'synthetic source note owns' in ''.join(selected_notes[0][1].itertext())
    assert len(calls)==1, 'geometry-proven source reference actually reached uncertain fallback'


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
