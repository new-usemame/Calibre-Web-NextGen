"""Late native original-source support failures retain healthy native reading and SOURCE routes."""
import pymupdf,pytest,zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from cps.services.reflow import assemble,build_epub,vision_pipeline as vp
from tests.unit.round7_recovery_test_support import TEXTS,h

@pytest.mark.parametrize('target',['_source_checks','_source_index','_original_document','_inspection_controls'])
def test_native_source_support_failure_does_not_abort_selected_book(tmp_path,monkeypatch,target):
    doc=pymupdf.open();pages={}
    for n,text in enumerate(TEXTS):
        page=doc.new_page(width=440,height=600);page.insert_text((40,90),text,fontsize=8)
        pages[n]=[assemble.Element('p',runs=[['t',text]],pno=n,bbox=(40,80,340,94),punctuation_uncertain=n==1)]
    book=assemble.Book(elements=[e for group in pages.values() for e in group],pages=pages)
    actual=getattr(build_epub,target);reached=[]
    def broken(*args,**kwargs):
        reached.append(target);raise ValueError('synthetic native original-source support failure '+target)
    monkeypatch.setattr(build_epub,target,broken)
    class Reader:
        cache=tmp_path/'readings'
        def read(self,image):raise AssertionError('native support failure cannot dispatch a model')
    Reader.cache.mkdir();path=tmp_path/'native-support.epub'
    try:report=vp.convert(doc,str(path),Reader(),native_prepared=SimpleNamespace(book=book))
    finally:doc.close()
    assert reached,'actual original-source support helper must be reached'
    assert build_epub.validate(path)==[] and set(map(int,report['page_homes']))=={0,1,2}
    with zipfile.ZipFile(path) as archive:
        docs={n:ET.fromstring(archive.read(n)) for n in archive.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
        bodies=h.body_texts(docs);assert bodies.count(TEXTS[0])==bodies.count(TEXTS[2])==1
        assert bodies.count(TEXTS[1])==1 or any(u['page']==1 and u.get('source_image') and u.get('source_reference') for u in report.get('degraded_units',[]))
        for unit in report.get('degraded_units',[]):
            assert unit['page']==1 and 'OEBPS/'+unit['source_image'] in archive.namelist()
            assert any(img.get('src')==unit['source_image'] for root in docs.values() for img in root.findall('.//x:img',h.NS))
        image_names=[n for n in archive.namelist() if n.startswith('OEBPS/images/')]
        assert image_names,'uncertain native source retains visible original pixels even if support rendering fails'
