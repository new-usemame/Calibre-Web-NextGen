"""Missing derived origin metadata cannot hide a real SOURCE-control Return obligation."""
import re,zipfile
from xml.etree import ElementTree as ET
from cps.services.reflow import vision_pipeline as vp,vision_reading as vr,unit_source,build_epub
from tests.unit.round7_recovery_test_support import fixture,h,TEXTS


def test_actual_source_origin_requires_return_even_if_report_origin_is_omitted(tmp_path,monkeypatch):
    doc,reader,_=fixture(tmp_path,monkeypatch);actual_align=vr.align;actual_source=unit_source.source_unit;reached=[]
    def broken_align(text,*args,**kwargs):
        if text==TEXTS[1]:raise ValueError('synthetic SOURCE-owned body guard failure')
        return actual_align(text,*args,**kwargs)
    monkeypatch.setattr(vr,'align',broken_align)
    def omitted(*args,**kwargs):
        fragment,asides,report=actual_source(*args,**kwargs)
        if args[3]=='body':
            reached.append(report['source_reference']);report=dict(report,source_reference_origin=None,unit_kind='footnote')
            asides=[re.sub(r'<a\b[^>]*>Return</a>','',item) for item in asides]
        return fragment,asides,report
    monkeypatch.setattr(unit_source,'source_unit',omitted)
    path=tmp_path/'source-return.epub'
    try:report=vp.convert(doc,str(path),reader)
    finally:doc.close()
    assert reached,'actual canonical SOURCE emitter integrity mutation must be reached'
    assert build_epub.validate(path)==[]
    with zipfile.ZipFile(path) as z:
        docs={n:ET.fromstring(z.read(n)) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')}
        assert h.body_texts(docs).count(TEXTS[0])==h.body_texts(docs).count(TEXTS[2])==1
        for unit in report['degraded_units']:
            target=unit['source_reference'];nodes=[(name,node) for name,root in docs.items() for node in root.iter() if node.get('id')==target]
            assert len(nodes)==1
            origins=[a for root in docs.values() for a in root.findall('.//x:a',h.NS) if a.get('href','').split('#')[-1]==target and a.get('id')]
            assert origins,'actual emitted SOURCE control survives independently of omitted metadata'
            for origin in origins:
                assert any(a.get('href','').split('#')[-1]==origin.get('id') and h.plain(a)=='Return' for a in nodes[0][1].findall('.//x:a',h.NS)),'every real SOURCE origin needs an exact working Return'
