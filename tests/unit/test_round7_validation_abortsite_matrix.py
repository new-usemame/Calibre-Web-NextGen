"""Reach each original strict page-validation abortsite through public convert.
Each case triggers its real primitive rejection; healthy before/after source
pages and the rejected source image must survive, rather than mocking success.
"""
import copy
import hashlib
import importlib.util
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
import pymupdf
import pytest
from PIL import Image
from cps.services.reflow import vision_pipeline as vp,vision_reading as vr,vision_pixels,build_epub
from cps.services.reflow.source_display import SourceDisplay

_spec=importlib.util.spec_from_file_location('r7_abortsite_helpers',Path(vp.__file__).parents[3]/'tests/unit/test_round4_defects.py')
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
BEFORE='The scarlet notebook remains complete before this source.'
FAILED='This isolated source preserves the carved amber emblem.'
AFTER='The turquoise notebook remains complete after this source.'
CASES=[('EXPLICIT-041','page','invalid page reading'),('EXPLICIT-042','fields','invalid reading block fields'),('EXPLICIT-043','identity','invalid or duplicate block identity'),('EXPLICIT-044','role','invalid block text or role'),('EXPLICIT-045','rectangle','invalid reading rectangle'),('EXPLICIT-046','confidence','invalid confidence token'),('EXPLICIT-047','continuation','invalid page continuation'),('EXPLICIT-048','note','unbound printed note marker')]


@pytest.mark.parametrize('site_id,defect,message',CASES)
def test_each_strict_validation_site_is_contained_and_source_visible(tmp_path,monkeypatch,site_id,defect,message):
    prepared=[]
    for text in (BEFORE,FAILED,AFTER):
        page=h.InkPage();page.text(text,'body',y=60);prepared.append(page.finish())
    doc=pymupdf.open()
    for image,_,_ in prepared:
        page=doc.new_page(width=h.W,height=h.H);page.insert_image(page.rect,stream=image)
    mapping={hashlib.sha256(SourceDisplay(doc,n).pixmap(scale=2,max_pixels=2_500_000).tobytes('jpg',jpg_quality=90)).hexdigest():n for n in range(3)}
    answer=copy.deepcopy(prepared[1][2]);b=answer['blocks'][0]
    if defect=='page':answer={'blocks':'invalid'}
    elif defect=='fields':b.pop('marker')
    elif defect=='identity':answer['blocks'].append(copy.deepcopy(b))
    elif defect=='role':b['role']='not_a_role'
    elif defect=='rectangle':b['bbox'][2]=b['bbox'][0]
    elif defect=='confidence':b['low_confidence']=[len(b['text'].split())]
    elif defect=='continuation':b['continues_next']=1
    elif defect=='note':b['text']+=' <n:unbound>'
    reached=[]
    class Reader:
        cache=tmp_path/'cache'/'readings'
        calls=[]
        def read(self,image):
            n=mapping[hashlib.sha256(image).hexdigest()];self.calls.append(n)
            if n==1:
                try:vr.validate(copy.deepcopy(answer))
                except ValueError as error:
                    assert str(error)==message
                    frames=[];tb=error.__traceback__
                    while tb:
                        frames.append((tb.tb_frame.f_code.co_name,tb.tb_lineno));tb=tb.tb_next
                    assert any(name=='validate' for name,line in frames)
                    reached.append({'site_id':site_id,'primitive':'vision_reading.validate','message':str(error),'exception_frames':frames})
                    raise
            return {'answer':copy.deepcopy(prepared[n][2]),'cached':True,'new_cost_usd':0}
    reader=Reader();path=tmp_path/'source-site.epub'
    monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(prepared[display.pno][1]))
    try:report=vp.convert(doc,str(path),reader)
    finally:doc.close()
    assert len(reached)==1, 'the exact original abortsite trigger must be observed once'
    assert reader.calls==[0,1,2], 'reassembly must not redispatch healthy primary reads'
    assert build_epub.validate(path)==[]
    units=report['degraded_units'];assert {(u['page'],u.get('block')) for u in units}=={(1,None)}
    assert message in units[0]['reason'] and units[0]['stage']
    assert report['reread_pages']==[] and set(map(int,report['page_homes']))=={0,1,2}
    with zipfile.ZipFile(path) as archive:
        docs={name:ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith('.xhtml') and not name.endswith(('nav.xhtml','vision-about.xhtml'))}
        body=h.body_texts(docs);assert body.count(BEFORE)==body.count(AFTER)==1
        source=units[0]['source_image'];assert 'OEBPS/'+source in archive.namelist()
        pixels=Image.open(io.BytesIO(archive.read('OEBPS/'+source))).convert('L');assert pixels.getextrema()[0]<100
        assert any(image.get('src')==source for root in docs.values() for image in root.findall('.//x:img',h.NS)), 'original source must be visibly published'
    (tmp_path/'reached-site.json').write_text(__import__('json').dumps(reached,indent=2))
