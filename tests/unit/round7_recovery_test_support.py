"""Independent synthetic SOURCE fixture and observable publication assertions."""
import copy,hashlib,importlib.util,io,zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
import pymupdf
from PIL import Image
from cps.services.reflow import vision_pipeline as vp,vision_pixels,build_epub
from cps.services.reflow.source_display import SourceDisplay
_spec=importlib.util.spec_from_file_location('r7_support_ink',Path(vp.__file__).parents[3]/'tests/unit/test_round4_defects.py')
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
TEXTS=('The amber notebook remains complete before replacement.','The middle source preserves a unique silver specimen.','The violet notebook remains complete after replacement.')


def fixture(tmp_path,monkeypatch):
    prepared=[];doc=pymupdf.open()
    for text in TEXTS:
        page=h.InkPage();page.text(text,'body',y=60);prepared.append(page.finish())
        source=doc.new_page(width=h.W,height=h.H);source.insert_image(source.rect,stream=prepared[-1][0])
    mapping={hashlib.sha256(SourceDisplay(doc,n).pixmap(scale=2,max_pixels=2_500_000).tobytes('jpg',jpg_quality=90)).hexdigest():n for n in range(3)}
    class Reader:
        cache=tmp_path/'cache'/'readings'
        calls=[]
        def read(self,image):
            n=mapping[hashlib.sha256(image).hexdigest()];self.calls.append(n)
            return {'answer':copy.deepcopy(prepared[n][2]),'cached':True,'new_cost_usd':0}
    reader=Reader();reader.cache.mkdir(parents=True)
    monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(prepared[display.pno][1]))
    return doc,reader,prepared


def publish(doc,reader,tmp_path):
    path=tmp_path/'test-public.epub'
    try:report=vp.convert(doc,str(path),reader)
    finally:doc.close()
    assert build_epub.validate(path)==[]
    with zipfile.ZipFile(path) as archive:
        docs={name:ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith('.xhtml') and not name.endswith(('nav.xhtml','vision-about.xhtml'))}
        body=h.body_texts(docs);assert body.count(TEXTS[0])==body.count(TEXTS[2])==1
        assert set(map(int,report['page_homes']))=={0,1,2}
        for unit in report.get('degraded_units',[]):
            assert unit['page']==1 and unit.get('stage') and unit.get('reason')
            src=unit['source_image'];assert 'OEBPS/'+src in archive.namelist()
            pixels=Image.open(io.BytesIO(archive.read('OEBPS/'+src))).convert('L');assert pixels.getextrema()[0]<100
            assert any(image.get('src')==src for root in docs.values() for image in root.findall('.//x:img',h.NS))
    return path,report,docs
