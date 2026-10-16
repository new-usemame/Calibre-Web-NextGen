"""Original native catalog and initial-source predicates enter the actual public guards."""
import copy,zipfile,pytest,pymupdf
from xml.etree import ElementTree as ET
from cps.services.reflow import vision_pipeline as vp,source_inventory,assemble,build_epub
from tests.unit.round7_recovery_test_support import h,TEXTS
from tests.unit.round7_public_note_contract_support import source_visible

@pytest.mark.parametrize('predicate',['unsupported_catalog','foreign_catalog_page','malformed_region','missing_initial'])
def test_original_native_inventory_predicate_preserves_other_physical_pages(tmp_path,monkeypatch,predicate):
    doc=pymupdf.open()
    for text in TEXTS:
        page=doc.new_page(width=440,height=600);page.insert_text((40,90),text,fontsize=8)
    reached=[]
    if predicate=='unsupported_catalog':
        original=source_inventory.catalog
        def catalog(raw):
            if raw.pno==1:
                raw=copy.deepcopy(raw);raw.source_geometry['untrusted_object']=object();reached.append(predicate)
            return original(raw)
        monkeypatch.setattr(source_inventory,'catalog',catalog)
    elif predicate in ('foreign_catalog_page','malformed_region'):
        original=source_inventory.capture
        def capture(prepared,skel,*args,**kwargs):
            if skel.pno==1:
                prepared=copy.deepcopy(prepared);skel=copy.deepcopy(skel);reached.append(predicate)
                if predicate=='foreign_catalog_page':prepared.page['page']=0
                else:skel.regions[0].bbox=(40,80,400)
            return original(prepared,skel,*args,**kwargs)
        monkeypatch.setattr(source_inventory,'capture',capture)
    else:
        original=assemble.source_word_counter
        def count(raw_pages,*args,**kwargs):
            skeletons=kwargs.get('skeletons')
            if skeletons:
                kwargs=dict(kwargs);kwargs['skeletons']=copy.deepcopy(skeletons)
                target=next((s for s in kwargs['skeletons'] if s.pno==1),None)
                if target:
                    target.regions[0].initial_join={'initial':'Q','following_word':'unprintedword'};reached.append(predicate)
            return original(raw_pages,*args,**kwargs)
        monkeypatch.setattr(assemble,'source_word_counter',count)
    class Reader:
        cache=tmp_path/'cache'
        def read(self,*args):raise AssertionError('Native source predicate recovery cannot dispatch a model')
    Reader.cache.mkdir();out=tmp_path/'inventory.epub'
    try:report=vp.convert(doc,str(out),Reader())
    finally:doc.close()
    assert reached and build_epub.validate(out)==[]
    with zipfile.ZipFile(out) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    assert h.body_texts(docs)==[TEXTS[0],TEXTS[2]]
    assert {(u['page'],u.get('block')) for u in report['degraded_units']}=={(1,None)}
    source_visible(out,report,{0,1,2})
