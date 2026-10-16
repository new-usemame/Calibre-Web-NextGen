"""Exact source-row removal predicates share only their actual physical page owner."""
import zipfile,pymupdf,pytest
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from cps.services.reflow import assemble,build_epub,vision_pipeline as vp
from tests.unit.round7_recovery_test_support import TEXTS,h
from tests.unit.round7_public_note_contract_support import source_visible

@pytest.mark.parametrize('which',['old','remaining'])
def test_actual_native_margin_projection_rejection_preserves_outer_pages(tmp_path,monkeypatch,which):
    doc=pymupdf.open();pages={};reached=[]
    for n,text in enumerate(TEXTS):
        page=doc.new_page(width=440,height=600)
        page.insert_text((40,25),'Synthetic shared notebook',fontsize=7)
        page.insert_text((40,90),text,fontsize=11)
        pages[n]=[assemble.Element('p',runs=[['t','Synthetic shared notebook '+text]],pno=n,bbox=(40,15,390,94))]
    original=vp._native_removal_offsets;plain=assemble.plain_text
    def faulty_dependency(runs,start,end,remaining):
        if TEXTS[1] in plain(runs) and not reached:
            reached.append(which)
            selected=runs if which=='old' else remaining
            with monkeypatch.context() as local:
                local.setattr(assemble,'plain_text',lambda value:plain(value)+' foreign' if value is selected else plain(value))
                return original(runs,start,end,remaining)
        return original(runs,start,end,remaining)
    monkeypatch.setattr(vp,'_native_removal_offsets',faulty_dependency)
    book=assemble.Book(elements=[e for group in pages.values() for e in group],pages=pages)
    reader=SimpleNamespace(cache=tmp_path/'cache',read=lambda *args:pytest.fail('native margin source recovery cannot dispatch a model'))
    reader.cache.mkdir();out=tmp_path/'native-offset.epub'
    try:report=vp.convert(doc,str(out),reader,native_prepared=SimpleNamespace(book=book))
    finally:doc.close()
    assert reached==[which]
    assert build_epub.validate(out)==[]
    with zipfile.ZipFile(out) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    bodies=h.body_texts(docs);assert bodies.count(TEXTS[0])==bodies.count(TEXTS[2])==1
    assert report['degraded_units'] and all(u['page']==1 for u in report['degraded_units'])
    source_visible(out,report,{0,1,2})
