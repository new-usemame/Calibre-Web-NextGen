"""Scoped native markup and extraction failures preserve unrelated live source units."""
from types import SimpleNamespace
import zipfile
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import vision_pipeline as vp,assemble,build_epub,extract
from tests.unit.round7_recovery_test_support import h
from tests.unit.round7_public_note_contract_support import source_visible
FIRST='The amber native notebook preserves its exact healthy first paragraph.'
BAD='The cobalt native specimen retains all of its unresolved original ink.'
LAST='The emerald native notebook preserves its exact healthy last paragraph.'
LATER='The violet later native page remains complete and live without model reading.'

@pytest.mark.parametrize('kind',['p','h','caption'])
def test_owned_native_markup_failure_does_not_degrade_sibling_elements(tmp_path,monkeypatch,kind):
    doc=pymupdf.open();pages={};runs=[]
    for n,items in enumerate([[(FIRST,'p',65),(BAD,kind,160),(LAST,'p',260)],[(LATER,'p',90)]]):
        page=doc.new_page(width=440,height=600);els=[]
        for text,role,y in items:
            page.insert_text((40,y),text,fontsize=8);box=[40,y-10,410,y+3]
            els.append(assemble.Element(role,runs=[['t',text]],pno=n,bbox=box,level=2 if role=='h' else 0))
        pages[n]=els;runs.extend(els)
    book=assemble.Book(elements=runs,pages=pages);reached=[];original=build_epub._nav_runs_html
    def broken(items,*args,**kwargs):
        if any(len(r)>1 and r[1]==BAD for r in items):
            reached.append('actual native '+kind+' source runs');raise ValueError('synthetic owned native markup failure')
        return original(items,*args,**kwargs)
    monkeypatch.setattr(build_epub,'_nav_runs_html',broken)
    class Reader:
        cache=tmp_path/'readings'
        def read(self,*args):raise AssertionError('native recovery cannot invent a paid reading')
        def read_marker(self,*args):raise AssertionError('native recovery cannot invent glyph reading')
    Reader.cache.mkdir();path=tmp_path/'native.epub'
    try:report=vp.convert(doc,str(path),Reader(),native_prepared=SimpleNamespace(book=book))
    finally:doc.close()
    assert reached and build_epub.validate(path)==[]
    with zipfile.ZipFile(path) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    assert h.body_texts(docs)==[FIRST,LAST,LATER]
    assert {(u['page'],u.get('block')) for u in report['degraded_units']}=={(0,'native-element-1')}
    source_visible(path,report,{0,1})


def test_native_extraction_failure_is_owned_by_actual_page_before_assembly(tmp_path,monkeypatch):
    doc=pymupdf.open()
    for text in (FIRST,BAD,LATER):
        page=doc.new_page(width=440,height=600);page.insert_text((40,90),text,fontsize=8)
    original=extract._read_page;reached=[]
    def broken(doc,pno,**kwargs):
        if pno==1:
            reached.append(('actual extract._read_page',pno));raise ValueError('synthetic malformed native source-page structure')
        return original(doc,pno,**kwargs)
    monkeypatch.setattr(extract,'_read_page',broken)
    class Reader:
        cache=tmp_path/'readings'
        def read(self,*args):raise AssertionError('native extraction SOURCE fallback cannot dispatch reading')
    Reader.cache.mkdir();path=tmp_path/'native-page.epub'
    try:report=vp.convert(doc,str(path),Reader())
    finally:doc.close()
    assert reached and build_epub.validate(path)==[]
    with zipfile.ZipFile(path) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    assert h.body_texts(docs)==[FIRST,LATER]
    assert {(u['page'],u.get('block')) for u in report['degraded_units']}=={(1,None)}
    source_visible(path,report,{0,1,2})
