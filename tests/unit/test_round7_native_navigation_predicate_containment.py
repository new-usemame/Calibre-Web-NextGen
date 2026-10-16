"""Original native navigation predicates must fail inside the owning element guard."""
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


@pytest.mark.parametrize('predicate',['offset','unbalanced','overlap','unclosed'])
def test_original_native_navigation_rejection_preserves_sibling_source_elements(tmp_path,monkeypatch,predicate):
    doc=pymupdf.open();pages={};all_elements=[]
    for n,items in enumerate([[(FIRST,65),(BAD,160),(LAST,260)],[(LATER,90)]]):
        page=doc.new_page(width=440,height=600);elements=[]
        for text,y in items:
            page.insert_text((40,y),text,fontsize=8)
            elements.append(assemble.Element('p',runs=[['t',text]],pno=n,bbox=[40,y-10,410,y+3]))
        pages[n]=elements;all_elements.extend(elements)
    book=assemble.Book(elements=all_elements,pages=pages)
    original=build_epub._nav_runs_html;reached=[]
    def actual_predicate(runs,marks,available,ref_ids,ambiguous):
        if any(len(run)>1 and run[1]==BAD for run in runs):
            malformed={'offset':[(len(BAD)+1,'open','x')],'unbalanced':[(0,'close','x')],'overlap':[(0,'open','x'),(1,'open','y')],'unclosed':[(0,'open','x')]}[predicate]
            try:return original(runs,malformed,available,ref_ids,ambiguous)
            except ValueError as error:
                reached.append(str(error));raise
        return original(runs,marks,available,ref_ids,ambiguous)
    monkeypatch.setattr(build_epub,'_nav_runs_html',actual_predicate)
    class Reader:
        cache=tmp_path/'readings'
        def read(self,*args):raise AssertionError('Native predicate recovery cannot dispatch a paid reading')
        def read_marker(self,*args):raise AssertionError('Native predicate recovery cannot dispatch a marker reading')
    Reader.cache.mkdir();path=tmp_path/'native-navigation.epub'
    try:report=vp.convert(doc,str(path),Reader(),native_prepared=SimpleNamespace(book=book))
    finally:doc.close()
    expected={'offset':'PDF navigation offset beyond source text','unbalanced':'Unbalanced PDF navigation range','overlap':'Overlapping PDF navigation ranges','unclosed':'Unclosed PDF navigation range'}[predicate]
    assert reached and all(value==expected for value in reached),'The original malformed-input predicate must actually reject'
    assert build_epub.validate(path)==[]
    with zipfile.ZipFile(path) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    assert h.body_texts(docs)==[FIRST,LAST,LATER]
    assert {(u['page'],u.get('block')) for u in report['degraded_units']}=={(0,'native-element-1')}
    source_visible(path,report,{0,1})
