"""Actual native source detail helpers are scoped separately from final package repair."""
import zipfile,pymupdf,pytest
from types import SimpleNamespace
from xml.etree import ElementTree as ET
from cps.services.reflow import assemble,build_epub,vision_pipeline as vp,source_display,native_text
from tests.unit.round7_recovery_test_support import TEXTS,h
from tests.unit.round7_public_note_contract_support import source_visible

@pytest.mark.parametrize('method',['source_image','source_rect','complete_detail_rect','_lossless_candidate','glyph_outside_source'])
def test_native_detail_function_failure_keeps_other_source_pages(tmp_path,monkeypatch,method):
    doc=pymupdf.open();pages={};reached=[]
    for n,text in enumerate(TEXTS):
        page=doc.new_page(width=440,height=600);page.insert_text((40,90),text,fontsize=8)
        pages[n]=[assemble.Element('p',runs=[['t',text]],pno=n,bbox=(40,80,340,94),punctuation_uncertain=n==1)]
    if method=='glyph_outside_source':
        record=native_text.descriptor(1,[500,700,550,710],8,'synthetic')
        pages[1][0].runs=[['glyph',TEXTS[1],record]]
        actual=native_text.package_glyphs
        def observe(*args,**kwargs):
            try:return actual(*args,**kwargs)
            except Exception as error:
                if 'outside page' in str(error):reached.append('actual glyph original outside-page predicate')
                raise
        monkeypatch.setattr(native_text,'package_glyphs',observe)
    elif method=='_lossless_candidate':
        actual=source_display.SourceDisplay._lossless_candidate
        def broken(pix,jpeg,max_bytes):
            # The method has no page field; only the one uncertain native page
            # owns a SOURCE-evidence render in this fixture.
            if not reached:
                reached.append('same original lossless function fault');raise ValueError('synthetic failed native lossless candidate integrity')
            return actual(pix,jpeg,max_bytes)
        monkeypatch.setattr(source_display.SourceDisplay,method,staticmethod(broken))
    else:
        actual=getattr(source_display.SourceDisplay,method)
        def broken(display,*args,**kwargs):
            if display.pno==1 and not reached:
                reached.append(method)
                if method=='source_image':return actual(display,*args,**dict(kwargs,max_bytes=1))
                return actual(display,[500,700,550,710])
            return actual(display,*args,**kwargs)
        monkeypatch.setattr(source_display.SourceDisplay,method,broken)
    book=assemble.Book(elements=[e for group in pages.values() for e in group],pages=pages)
    class Reader:
        cache=tmp_path/'cache'
        def read(self,*args):raise AssertionError('native source detail fallback cannot dispatch a model')
    Reader.cache.mkdir();out=tmp_path/'detail.epub'
    try:report=vp.convert(doc,str(out),Reader(),native_prepared=SimpleNamespace(book=book))
    finally:doc.close()
    assert reached and build_epub.validate(out)==[]
    with zipfile.ZipFile(out) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    bodies=h.body_texts(docs);assert bodies.count(TEXTS[0])==bodies.count(TEXTS[2])==1
    assert report['degraded_units'] and all(u['page']==1 for u in report['degraded_units'])
    source_visible(out,report,{0,1,2})
