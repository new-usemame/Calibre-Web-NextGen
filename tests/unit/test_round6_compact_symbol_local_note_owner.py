"""An unusable compact-symbol reading cannot jump to a foreign note owner.

Original ink, literal SOURCE optical evidence and note owners are separate from
an explicitly conflicting bounded verification response. All reads are mocks.
"""
import copy,importlib.util,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import vision_pipeline,vision_pixels,build_epub
_helper=Path(__file__).with_name('test_round5_terminal_symbol_component_ownership.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round5_terminal_symbol_component_ownership.py'
_s=importlib.util.spec_from_file_location('compact_symbol_original_fixture',_helper)
t=importlib.util.module_from_spec(_s);_s.loader.exec_module(t)
pytestmark=pytest.mark.unit

@pytest.mark.parametrize('remote_mark,verified',[('†','†'),('†','*'),('*','*')])
def test_compact_symbol_cannot_jump_from_corroborated_local_owner(tmp_path,monkeypatch,remote_mark,verified):
    prepared=[t.fixture(mark=remote_mark,symbol_size=6),t.fixture(mark='*',symbol_size=3)]
    assert prepared[1][4][3]-prepared[1][4][1]<=3.2
    doc=pymupdf.open()
    for item in prepared:
        page=doc.new_page(width=t.h.W,height=t.h.H);page.insert_image(page.rect,stream=item[0])
    monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(prepared[display.pno][1]))
    monkeypatch.setattr(vision_pixels,'raised_markers',lambda display,box,cache:[{'label':remote_mark if display.pno==0 else '*','bbox':prepared[display.pno][4],'kind':'bare'}])
    class Reader:
        cache=tmp_path/'readings'
        calls=0;marker_calls=0
        def read(self,image):
            answer=copy.deepcopy(prepared[self.calls][2]);self.calls+=1
            return dict(answer=answer,cached=True,new_cost_usd=0)
        def read_marker(self,image):
            self.marker_calls+=1
            return dict(label=verified,uncertain=False,cached=True,new_cost_usd=0)
    reader=Reader();reader.cache.mkdir();out=tmp_path/'symbol-owner.epub'
    try:report=vision_pipeline.convert(doc,str(out),reader)
    finally:doc.close()
    assert reader.calls==2 and reader.marker_calls>=1
    assert build_epub.validate(str(out))==[]
    with zipfile.ZipFile(out) as z:
        docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    refs,notes=t.h.assert_exact_returns(docs,2)
    bypage={int(a.get('id').split('-')[2]):a for _,a in refs}
    assert ''.join(bypage[1].itertext())=='*', 'a conflicting compact label with no local owner cannot replace a corroborated literal SOURCE star'
    assert bypage[1].get('href').endswith('#v4-note-1-note'), 'same printed symbol on another page must not steal the local source note'
    assert bypage[0].get('href').endswith('#v4-note-0-note')
    assert t.h.body_texts(docs)==[prepared[0][5].rstrip(remote_mark),prepared[1][5].rstrip('*')]
