"""Aggregate-only native helper failures retain admitted pages and strict inactive rejection."""
import pymupdf,pytest,zipfile
from xml.etree import ElementTree as ET
from cps.services.reflow import pipeline,vision_pipeline as vp,build_epub,assess,skeleton,assemble,route
from tests.unit.round7_recovery_test_support import h,TEXTS

@pytest.mark.parametrize('stage',['assessment','book_style','assembly','routing','route_summary','routed_pages'])
def test_aggregate_only_failure_rebuilds_exact_source_without_all_book_degradation(tmp_path,monkeypatch,stage):
    owner,name={'assessment':(assess,'assess_pages'),'book_style':(skeleton,'book_style'),'assembly':(assemble,'assemble'),'routing':(route,'route_pages'),'route_summary':(route,'summarise'),'routed_pages':(route,'routed_pages')}[stage]
    original=getattr(owner,name);reached=[]
    def broken(*args,**kwargs):
        first=args[0];size=len(args[1]) if stage=='routing' else len(first)
        if size>1:
            if stage=='assembly' and len(args)>2:args[2][0].text_blocks.clear()
            reached.append(('aggregate',size));raise ValueError('synthetic aggregate-only '+stage+' failure after source inputs admitted')
        reached.append(('fresh-singleton',size));return original(*args,**kwargs)
    monkeypatch.setattr(owner,name,broken)
    def source():
        doc=pymupdf.open()
        for text in TEXTS:
            page=doc.new_page(width=440,height=600);page.insert_text((40,90),text,fontsize=8)
        return doc
    strict=source()
    try:
        with pytest.raises(ValueError,match='synthetic aggregate-only'):pipeline.run(strict,recovery_opts={'mode':'off'})
    finally:strict.close()
    reached.clear()
    class Reader:
        cache=tmp_path/'readings'
        def read(self,*args):raise AssertionError('aggregate native recovery cannot call a Reader')
    Reader.cache.mkdir();doc=source();out=tmp_path/'aggregate.epub'
    try:report=vp.convert(doc,str(out),Reader())
    finally:doc.close()
    assert any(kind=='aggregate' for kind,_ in reached) and sum(kind=='fresh-singleton' for kind,_ in reached)>=3,'each page must be independently probed before deterministic aggregate repair'
    assert report['native_pages']==[0,1,2] and report['degraded_units']==[]
    assert build_epub.validate(out)==[]
    with zipfile.ZipFile(out) as z:docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
    assert h.body_texts(docs)==list(TEXTS),'pristine exact source input must survive failed aggregate mutation'
    assert any(e['transition']=='DETERMINISTIC_SOURCE_REPAIR' for e in report['unit_recovery_events'])
