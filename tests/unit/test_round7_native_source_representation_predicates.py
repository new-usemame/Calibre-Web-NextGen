"""Actual native list/title correspondence rejection is scoped to its element."""
from types import SimpleNamespace
import zipfile
from xml.etree import ElementTree as ET
import pymupdf,pytest
from cps.services.reflow import vision_pipeline as vp,assemble,build_epub
from tests.unit.round7_recovery_test_support import h
from tests.unit.round7_public_note_contract_support import source_visible
FIRST='The amber native notebook preserves its exact healthy first paragraph.'
BAD='The cobalt native specimen retains all of its unresolved original ink.'
LAST='The emerald native notebook preserves its exact healthy last paragraph.'
LATER='The violet later native page remains complete and live without model reading.'


@pytest.mark.parametrize('kind',['list','h'])
def test_original_native_representation_inventory_predicate_is_contained(tmp_path,monkeypatch,kind):
    doc=pymupdf.open();pages={};elements=[]
    for n,items in enumerate([[(FIRST,'p',65),(BAD,kind,160),(LAST,'p',260)],[(LATER,'p',90)]]):
        page=doc.new_page(width=440,height=600);group=[]
        for text,role,y in items:
            page.insert_text((40,y),text,fontsize=8)
            element=assemble.Element(role,runs=[['t',text]],pno=n,bbox=[40,y-10,410,y+3],level=2 if role=='h' else 0)
            if text==BAD:
                if kind=='list':element.list_items=[[['t','An unrelated list rendering.']]]
                else:element.display_lines=({'runs':[['t','An unrelated heading rendering.']],'scale':1,'bold':False},)
            group.append(element);elements.append(element)
        pages[n]=group
    book=assemble.Book(elements=elements,pages=pages)
    # Public recovery invokes the same renderer kernel directly; strict older
    # versions expose that kernel only as page_fragment.
    method='_page_fragment' if hasattr(build_epub,'_page_fragment') else 'page_fragment'
    original=getattr(build_epub,method);reached=[]
    def observe(*args,**kwargs):
        try:return original(*args,**kwargs)
        except Exception as error:
            cause=getattr(error,'cause',error)
            if str(cause) in ('Source list items differ from source inventory','Source display-line text differs from heading inventory'):reached.append(str(cause))
            raise
    monkeypatch.setattr(build_epub,method,observe)
    class Reader:
        cache=tmp_path/'readings'
        def read(self,*args):raise AssertionError('Native representation recovery cannot dispatch a model')
    Reader.cache.mkdir();path=tmp_path/'native-representation.epub'
    try:report=vp.convert(doc,str(path),Reader(),native_prepared=SimpleNamespace(book=book))
    finally:doc.close()
    expected='Source list items differ from source inventory' if kind=='list' else 'Source display-line text differs from heading inventory'
    assert reached and all(value==expected for value in reached),'The original actual representation predicate must reject'
    assert build_epub.validate(path)==[]
    with zipfile.ZipFile(path) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    assert h.body_texts(docs)==[FIRST,LAST,LATER]
    assert {(u['page'],u.get('block')) for u in report['degraded_units']}=={(0,'native-element-1')}
    source_visible(path,report,{0,1})
