"""Synthetic neighboring digit crops must not repeat their shared strokes."""
import copy
from xml.etree import ElementTree as ET
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline as vp
from cps.services.reflow.source_display import SourceDisplay
from tests.unit.test_reflow_vision_pilot import answer,block

pytestmark=pytest.mark.unit


def test_adjacent_uncertain_digit_crops_publish_one_complete_original_span(monkeypatch):
    with pymupdf.open()as ink,pymupdf.open()as doc:
        page=ink.new_page(width=240,height=120);page.insert_text((40,55),'123',fontsize=16)
        chars=[c for b in page.get_text('rawdict')['blocks']for line in b.get('lines',[])for span in line['spans']for c in span['chars']]
        boxes=[list(chars[0]['bbox']),list(pymupdf.Rect(chars[1]['bbox'])|pymupdf.Rect(chars[2]['bbox']))]
        target=doc.new_page(width=240,height=120);target.insert_image(target.rect,stream=page.get_pixmap(matrix=pymupdf.Matrix(3,3)).tobytes('png'))
        words=[dict(text=t,bbox=b,confidence=99)for t,b in zip(('1','23'),boxes)]
        real_align=vp.vision_reading.align
        def uncertain(*args,**kwargs):
            result=real_align(*args,**kwargs)
            result['word_notes']={i:dict(source=[w['text']],model=[w['text']],chosen=[w['text']],
                source_image_only=True,source_bbox=w['bbox'],status='UNRESOLVED',method='incomplete_numeric_word_ink')for i,w in enumerate(words)}
            result['marked']={0,1};return result
        monkeypatch.setattr(vp.vision_reading,'align',uncertain)
        class Package:
            def __init__(self):self.images={}
            def image(self,name,data):self.images[name]=data
        package=Package();images={}
        published,record=vp.compile_page(answer(block('1 23',bbox=(0,0,1000,1000))),words,
            SourceDisplay(doc,0),0,package,images)
        root=ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(published['body'])+'</root>')
        inline=list(root.iter('img'))
        assert len(inline)==1,'padded neighboring crops must not print the middle digit twice'
        asset=images[inline[0].get('src')]
        if asset.get('kind')=='inline_print_preview':
            assert asset['coverage_eligible']is False
            asset=images[asset['inspection_image']]
        assert asset['method']=='overlapping_inline_ink_once'
        assert pymupdf.Rect(asset['bbox']).contains(pymupdf.Rect(boxes[0])|pymupdf.Rect(boxes[1]))
        assert len([a for a in root.iter('a')if a.get('class')=='source-disclosure-ref'])==2
        assert record['blocks'][0]['source_image_words']==[0,1]
