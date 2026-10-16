"""Source-identified figure failures use their actual local context/crop guards."""
import zipfile,pymupdf,pytest
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from cps.services.reflow import assemble,build_epub,vision_pipeline as vp
from tests.unit.round7_recovery_test_support import TEXTS,h
from tests.unit.round7_public_note_contract_support import source_visible

@pytest.mark.parametrize('defect',['context_nonunique','scan_key_proof'])
def test_named_native_figure_inner_failure_retains_other_source_pages(tmp_path,monkeypatch,defect):
    doc=pymupdf.open();pages={};figures=[];reached=[]
    for n,text in enumerate(TEXTS):
        page=doc.new_page(width=440,height=600);page.insert_text((40,90),text,fontsize=8)
        page.draw_rect([40,140,120,220],fill=(0,.5,1));page.draw_line((40,140),(120,220),width=2)
        pages[n]=[assemble.Element('p',runs=[['t',text]],pno=n,bbox=(40,80,340,94)),assemble.Element('fig',pno=n,bbox=(40,140,120,220))]
        figures.append(dict(pno=n,bbox=(40,140,120,220),found='uncertain_scan_key_panel' if n==1 and defect=='scan_key_proof' else 'native'))
    if defect=='context_nonunique':
        images=build_epub._figure_images
        def missing(*args,**kwargs):
            bad,blank=images(*args,**kwargs);package=args[3];src='images/fig_p0001_0.jpg'
            if not reached:
                package.images.pop(package.aliases.get(src,src),None);package.images.pop(src,None)
                return [src]+bad,blank
            return bad,blank
        monkeypatch.setattr(build_epub,'_figure_images',missing)
        recover=vp._recover_native_figures
        def original_context(unavailable,html_native,*args,**kwargs):
            if unavailable and not reached:
                # The first pass declares the known asset unavailable. The next
                # SOURCE context pass alone receives a duplicate published carrier.
                recovery=getattr(vp,'unit_recovery',None)
                if recovery is None or 'native-figure-source-0' in recovery.current().blocked(1):
                    reached.append(defect)
                    html_native[1]+='<figure><img src="images/fig_p0001_0.jpg" /></figure>'
            return recover(unavailable,html_native,*args,**kwargs)
        monkeypatch.setattr(vp,'_recover_native_figures',original_context)
    else:
        actual=build_epub._scan_key_white_margin_jpeg
        def failed_proof(doc,pno,bbox):
            if pno==1 and not reached:
                reached.append(defect);raise ValueError('synthetic source side crop changed rendered pixels')
            return actual(doc,pno,bbox)
        monkeypatch.setattr(build_epub,'_scan_key_white_margin_jpeg',failed_proof)
    book=assemble.Book(elements=[e for group in pages.values() for e in group],pages=pages,figures=figures)
    reader=SimpleNamespace(cache=tmp_path/'cache',read=lambda *args:pytest.fail('native artwork source recovery cannot dispatch a model'))
    reader.cache.mkdir();out=tmp_path/'native-figure.epub'
    try:report=vp.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
    finally:doc.close()
    assert reached==[defect]
    assert build_epub.validate(out)==[]
    with zipfile.ZipFile(out) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    bodies=h.body_texts(docs);assert bodies.count(TEXTS[0])==bodies.count(TEXTS[2])==1
    if defect=='context_nonunique':
        assert report['degraded_units'] and all(u['page']==1 for u in report['degraded_units'])
        source_visible(out,report,{0,1,2})
    else:
        assert bodies.count(TEXTS[1])==1
        assert any(record['page']==1 and record['kind']=='uncertain_scan_key_panel' for record in report['images'].values())
