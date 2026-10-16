"""SOURCE-free original glyph verification in an exact adjacent note slot.

A full-height original double dagger is read as baseline 't' by the locked word
OCR and omitted by character OCR. Adjacent model representations alone do not
prove identity; bounded original verification and exact two-sided anchors do.
"""
import copy
import importlib.util
from pathlib import Path

import pymupdf
import pytest
from cps.services.reflow import vision_pipeline,vision_pixels
from cps.services.reflow.source_display import SourceDisplay

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():
    _helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('round5_provisional_note_slot_ink',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit


def fixture(original_glyph='‡',mismatch=None,two=False):
    p=h.InkPage();font=pymupdf.Font('cjk')
    p.page.insert_font(fontname='unicode',fontbuffer=font.buffer)
    prefix='Careful observations describe pale minerals,’'
    suffix='and preserve each original detail.'
    p.page.insert_text((22,55),prefix,fontsize=6,fontname='unicode')
    x=22+font.text_length(prefix,fontsize=6)+1.5
    p.page.insert_text((x,55),original_glyph,fontsize=6,fontname='unicode')
    end=x+font.text_length(original_glyph,fontsize=6)
    if two:
        p.page.insert_text((end+2,55),'‡',fontsize=6,fontname='unicode')
        right=end+2+font.text_length('‡',fontsize=6)+2
    else:right=end+2
    assert right+font.text_length(suffix,fontsize=6)<h.W
    p.page.insert_text((right,55),suffix,fontsize=6,fontname='unicode')
    text=prefix+'‡ <n:note> '+suffix
    p.blocks.append(h.model_block(text,'body','body',[20,43,282,61]))
    note='The independent source citation records the original archival observations.'
    p.page.insert_text((22,325),'‡ '+note,fontsize=5,fontname='unicode')
    p.blocks.append(h.model_block(note,'note','footnote',[20,315,282,331],marker='‡'))
    glyph_box=next(c['bbox'] for b in p.page.get_text('rawdict')['blocks']
        for row in b.get('lines',[]) for s in row['spans'] for c in s['chars']
        if c['c']==original_glyph and c['bbox'][1]<70 and c['bbox'][0]>=x-.1)
    finish_original=p.finish
    def finish():
        image,words,answer=finish_original()
        slots=[w for w in words if w['bbox'][1]<70 and w['text']==original_glyph]
        assert slots,'locked OCR mutation requires genuine separate original-source slot ink'
        slots[0]['text']='t'
        if two:
            extra=next(w for w in words if w is not slots[0] and w['bbox'][1]<70 and w['text']=='‡')
            words.remove(extra)  # the original second source glyph remains intact
        if mismatch=='before':
            before=next(w for w in words if w['bbox'][1]<70 and 'minerals' in w['text'])
            before['text']=before['text'].replace('minerals','crystals')
        if mismatch=='after':
            after=next(w for w in words if w['bbox'][1]<70 and w['text']=='and')
            after['text']='but'
        return image,words,answer
    p.finish=finish
    return p,prefix,suffix,note,list(glyph_box)


def convert(tmp_path,monkeypatch,prepared,response=None):
    p,prefix,suffix,note,glyph=prepared
    monkeypatch.setattr(vision_pixels,'raised_markers',lambda *args,**kwargs:[])
    original_convert=vision_pipeline.convert;original_roi=SourceDisplay.roi_jpeg
    crops=[];calls=[]
    def roi(display,bbox,*args,**kwargs):
        if kwargs.get('scale')==12:crops.append(list(bbox))
        return original_roi(display,bbox,*args,**kwargs)
    monkeypatch.setattr(SourceDisplay,'roi_jpeg',roi)
    def convert(doc,path,reader,*args,**kwargs):
        def read_marker(image):
            assert crops,'a provisional glyph must be verified from bounded original ink'
            box=crops[-1];cx=(glyph[0]+glyph[2])/2;cy=(glyph[1]+glyph[3])/2
            assert box[0]-.5<=cx<=box[2]+.5 and box[1]-.5<=cy<=box[3]+.5,'quotation or neighboring prose cannot borrow the glyph verification'
            assert box[2]-box[0]<=glyph[2]-glyph[0]+4,'verification stays in the original source glyph slot'
            calls.append(dict(bbox=box,image=image))
            return dict(response or {'label':'‡','uncertain':False},cached=True,new_cost_usd=0)
        reader.read_marker=read_marker
        return original_convert(doc,path,reader,*args,**kwargs)
    monkeypatch.setattr(vision_pipeline,'convert',convert)
    result=h.convert_ink(tmp_path,monkeypatch,[p])
    return result,calls


def test_original_fullheight_double_dagger_ocr_t_has_one_verified_reference(tmp_path,monkeypatch):
    prepared=fixture()
    (_,report,docs),calls=convert(tmp_path,monkeypatch,prepared)
    assert len(calls)==1,'the adjacent placeholder is not proof; verify the original glyph once'
    refs,notes=h.assert_exact_returns(docs,1)
    assert ''.join(refs[0][1].itertext())=='‡'
    assert h.body_texts(docs)==[prepared[1]+' '+prepared[2]],'one physical glyph cannot remain as an additional unlinked literal'
    assert sum(prepared[3] in ''.join(n.itertext()) for _,n in notes)==1
    body=next(b for b in report['pages'][0]['blocks'] if b['id']=='body')
    assert body['published_tokens'][4:7]==['minerals,’‡','<n:note>','and'],'verified reconciliation must preserve model tokens'


@pytest.mark.parametrize('glyph,response',[('t',{'label':'t','uncertain':False}),('‡',{'label':'','uncertain':True})])
def test_unproven_slot_keeps_literal_and_structured_reference_separate(tmp_path,monkeypatch,glyph,response):
    prepared=fixture(original_glyph=glyph)
    (_,report,docs),calls=convert(tmp_path,monkeypatch,prepared,response)
    refs,_=h.assert_exact_returns(docs,1)
    assert ''.join(refs[0][1].itertext())=='‡'
    assert h.body_texts(docs)==[prepared[1]+'‡ '+prepared[2]],'unproven identity cannot silently consume an authored model literal'
    assert len(calls)<=1


@pytest.mark.parametrize('mismatch',['before','after'])
def test_disputed_source_endpoint_does_not_dispatch_provisional_slot(tmp_path,monkeypatch,mismatch):
    prepared=fixture(mismatch=mismatch)
    (_,_,docs),calls=convert(tmp_path,monkeypatch,prepared)
    assert calls==[],'both adjacent lexical anchors require exact source correspondence'
    h.assert_exact_returns(docs,1)
    assert h.body_texts(docs)==[prepared[1]+'‡ '+prepared[2]]


def test_two_original_double_daggers_with_one_ocr_miss_keep_two_actions(tmp_path,monkeypatch):
    prepared=fixture(two=True)
    (_,_,docs),calls=convert(tmp_path,monkeypatch,prepared)
    assert len(calls)==1
    refs,_=h.assert_exact_returns(docs,2)
    assert [''.join(a.itertext()) for _,a in refs]==['‡','‡']
    assert h.body_texts(docs)==[prepared[1]+' '+prepared[2]]
