"""Original local evidence geometry/tile rejection must not abort adjacent native pages."""
import zipfile,pymupdf,pytest
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from cps.services.reflow import assemble,build_epub,vision_pipeline as vp,source_display
from tests.unit.round7_recovery_test_support import TEXTS,h
from tests.unit.round7_public_note_contract_support import source_visible

@pytest.mark.parametrize('defect',['rect_arity','rect_empty','evidence_outside','tile_configuration','tile_count','tile_aggregate_count'])
def test_actual_native_evidence_predicate_has_scoped_completed_source(tmp_path,monkeypatch,defect):
    doc=pymupdf.open();pages={};figures=[];reached=[]
    for n,text in enumerate(TEXTS):
        page=doc.new_page(width=440,height=600);page.insert_text((40,90),text,fontsize=8)
        bbox=(500,700,550,710) if n==1 and defect=='evidence_outside' else (40,80,340,94)
        pages[n]=[assemble.Element('p',runs=[['t',text]],pno=n,bbox=bbox,punctuation_uncertain=n==1)]
        if n==1 and defect.startswith('tile_'):
            page.draw_rect([40,140,120,220],fill=(0,.5,1))
            pages[n].append(assemble.Element('fig',pno=n,bbox=(40,140,120,220)))
            figures.append(dict(pno=n,bbox=(40,140,120,220),found='unrecovered_scan_layer'))
    if defect.startswith('rect_'):
        actual=source_display.SourceDisplay.source_image
        def original_bad_rect(display,*args,**kwargs):
            if display.pno==1 and not reached:
                reached.append(defect)
                return actual(display,[1,2,3] if defect=='rect_arity' else [10,10,10,20],**kwargs)
            return actual(display,*args,**kwargs)
        monkeypatch.setattr(source_display.SourceDisplay,'source_image',original_bad_rect)
    elif defect.startswith('tile_'):
        actual=source_display.inspection_tiles
        def original_bad_tiles(rect,*args,**kwargs):
            if not reached:
                reached.append(defect)
                if defect=='tile_aggregate_count':return [pymupdf.Rect(rect) for _ in range(65)]
                return actual(rect,width=24,height=24,overlap=24) if defect=='tile_configuration' else actual((0,0,10000,10000))
            return actual(rect,*args,**kwargs)
        monkeypatch.setattr(source_display,'inspection_tiles',original_bad_tiles)
    else:
        actual=build_epub._original_evidence
        def observe(*args,**kwargs):
            try:return actual(*args,**kwargs)
            except Exception as error:
                if 'Invalid original source evidence geometry' in str(error):reached.append(defect)
                raise
        monkeypatch.setattr(build_epub,'_original_evidence',observe)
    book=assemble.Book(elements=[e for group in pages.values() for e in group],pages=pages,figures=figures)
    reader=SimpleNamespace(cache=tmp_path/'cache',read=lambda *args:pytest.fail('native evidence cannot dispatch a model'))
    reader.cache.mkdir();out=tmp_path/'native-evidence.epub'
    try:report=vp.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
    finally:doc.close()
    assert reached==[defect]
    assert build_epub.validate(out)==[]
    with zipfile.ZipFile(out) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    body=h.body_texts(docs);assert body.count(TEXTS[0])==body.count(TEXTS[2])==1
    assert report['degraded_units'] and all(u['page']==1 for u in report['degraded_units'])
    source_visible(out,report,{0,1,2})
