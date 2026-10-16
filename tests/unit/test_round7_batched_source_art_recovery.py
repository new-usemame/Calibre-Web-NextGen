"""Procedural PDF fixtures exercise recovery batching without a model or OCR engine."""
import copy,hashlib,importlib.util,io,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
import pymupdf,pytest
from PIL import Image
from cps.services.reflow import vision_pipeline as vp,vision_pixels,build_epub
from cps.services.reflow.source_display import SourceDisplay
spec=importlib.util.spec_from_file_location('batch_ink',Path(vp.__file__).parents[3]/'tests/unit/test_round4_defects.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
BODIES=('The amber specimen remains a complete healthy sentence.',
        'The silver specimen remains a complete healthy sentence.',
        'The violet specimen remains a complete healthy sentence.')
NOTES=('A separate amber note records a local observation.',
       'A separate silver note records a local observation.',
       'A separate violet note records a local observation.')

@pytest.mark.parametrize('failed_pages,role',[
    ({0,1,2},'figure'),({0,1,2},'table'),({1},'figure'),(set(),'figure')],
    ids=['three-unproved-figures','three-unproved-tables','one-unproved-figure','healthy-figures'])
def test_all_owned_art_failures_share_one_recovery_round(tmp_path,monkeypatch,failed_pages,role):
    prepared=[]
    for n in range(3):
        p=h.InkPage();p.text(BODIES[n],'body',y=48);p.figure('object',caption=False)
        if role=='table':p.blocks[-1]['role']='table'
        p.text(NOTES[n],'local-note','footnote',y=335,size=7,marker='†')
        prepared.append(p.finish())
    doc=pymupdf.open()
    for image,_,_ in prepared:
        page=doc.new_page(width=h.W,height=h.H);page.insert_image(page.rect,stream=image)
    identities={hashlib.sha256(SourceDisplay(doc,n).pixmap(scale=2,max_pixels=2_500_000).tobytes('jpg',jpg_quality=90)).hexdigest():n for n in range(3)}
    class Reader:
        cache=tmp_path/'cache'/'readings'
        primary=[];targeted=[]
        def record(self,image,kind):
            n=identities[hashlib.sha256(image).hexdigest()]
            getattr(self,kind).append(n)
            return dict(answer=copy.deepcopy(prepared[n][2]),cached=True,new_cost_usd=0.,config_sha256=kind+'-synthetic-config')
        def read(self,image):return self.record(image,'primary')
        def reread(self,image):return self.record(image,'targeted')
    reader=Reader();reader.cache.mkdir(parents=True)
    monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(prepared[display.pno][1]))
    geometry=[];figure_visits=[];original_geometry=vp.bind_geometry;original_figures=vp._bind_figures
    def counted_geometry(answer,optical,display,*args,**kwargs):
        geometry.append(display.pno)
        return original_geometry(answer,optical,display,*args,**kwargs)
    def unproved_art(answer,optical,display,regions):
        figure_visits.append(display.pno)
        if display.pno not in failed_pages:return original_figures(answer,optical,display,regions)
        return [dict(block=b['id'],degraded=True,seed_bbox=list(vp.block_rect(b,display)),reason='Synthetic source-owned artwork has no proved isolated crop.',method='synthetic-unproved-source-art')
                for b in answer['blocks'] if b['role'] in ('figure','table')]
    monkeypatch.setattr(vp,'bind_geometry',counted_geometry)
    monkeypatch.setattr(vp,'_bind_figures',unproved_art)
    path=tmp_path/'batched.epub'
    try:report=vp.convert(doc,str(path),reader)
    finally:doc.close()
    assert build_epub.validate(path)==[]
    with zipfile.ZipFile(path) as z:
        docs={name:ET.fromstring(z.read(name)) for name in z.namelist() if name.startswith('OEBPS/ch') and name.endswith('.xhtml')}
        body=h.body_texts(docs)
        assert len(body)==3 and all(body.count(text)==1 for text in BODIES),'Healthy BODY on every source page remains live exactly once.'
        notes=h.refs_and_notes(docs)[1]
        assert all(sum(text in ''.join(e.itertext()) for _,e in notes)==1 for text in NOTES),'No healthy note is degraded, duplicated, or moved to a foreign source owner.'
        units=report.get('degraded_units',[])
        assert {(u['page'],u.get('block')) for u in units}=={(n,'object') for n in failed_pages}
        assert report['reread_pages']==sorted(failed_pages)
        assert reader.primary==[0,1,2] and sorted(reader.targeted)==sorted(failed_pages),'One synthetic primary and at most one shared physical reread per failed page.'
        assert set(map(int,report['page_homes']))=={0,1,2}
        for unit in units:
            assert unit['stage']=='figure_separation' and unit.get('reason')
            source=unit['source_image'];assert 'OEBPS/'+source in z.namelist()
            pixels=Image.open(io.BytesIO(z.read('OEBPS/'+source))).convert('L')
            assert pixels.getextrema()[0]<100
            target=next(e for root in docs.values() for e in root.iter() if e.get('id')==unit['source_reference'])
            assert target.findall('.//x:img',h.NS),'Degraded artwork original pixels must remain published.'
            assert any(h.plain(a)=='Return' for a in target.findall('.//x:a',h.NS))
            assert any(unit['source_reference'] in a.get('href','') for root in docs.values() for a in root.findall('.//x:a',h.NS))
        assert all(n in figure_visits for n in failed_pages),'Every selected failure helper was actually reached.'
    assert len(geometry)<=9,('All SOURCE-bound figure/table failures must be collected before recovery; three pages permit at most initial+R2+SOURCE reassembly=9 geometry bindings.',geometry)
