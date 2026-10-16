"""One source quotation character can contain two disconnected ink strokes.

A terminal note symbol before closing punctuation owns its original glyph,
not the left half of a quote. No quote-only verification request is admitted.
"""
import importlib.util
from pathlib import Path
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('round5_closing_quote_ink',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit


def fixture(suffix,source_symbol=True):
    p=h.InkPage();font=pymupdf.Font('cjk')
    p.page.insert_font(fontname='unicode',fontbuffer=font.buffer)
    body='Careful observers compare several independently measured specimens.'
    p.text(body,'body',y=45,size=6);p.figure('diagram',caption=False)
    caption='The sample workbench (W. Plan.'+('†' if source_symbol else '')+suffix+')'
    assert font.text_length(caption,fontsize=6)<258
    p.page.insert_text((22,194),caption,fontsize=6,fontname='unicode')
    p.blocks.append(h.model_block(caption,'cap','caption',[20,184,280,198]))
    note='This printed note retains the original citation.'
    p.page.insert_text((22,330),'† '+note,fontsize=6,fontname='unicode')
    p.blocks.append(h.model_block(note,'note','footnote',[20,320,280,335],marker='†'))
    return p,body,caption,note


def refuse_quote_crop(monkeypatch):
    original=vision_pipeline.convert;calls=[]
    def convert(doc,path,reader,*args,**kwargs):
        def read_marker(image):
            calls.append(image)
            raise AssertionError('The full-height original dagger needs no quote-only marker callback')
        reader.read_marker=read_marker
        return original(doc,path,reader,*args,**kwargs)
    monkeypatch.setattr(vision_pipeline,'convert',convert)
    return calls


@pytest.mark.parametrize('suffix',['"','”'])
def test_source_dagger_before_two_stroke_closing_quote_owns_dagger_not_quote(tmp_path,monkeypatch,suffix):
    p,body,caption,note=fixture(suffix);calls=refuse_quote_crop(monkeypatch)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);refs,notes=h.assert_exact_returns(docs,1)
    name,ref=refs[0];assert ''.join(ref.itertext())=='†'
    target=h.destination(name,ref.get('href'));selected=next(n for nn,n in notes if (nn,n.get('id'))==target)
    assert note in ''.join(selected.itertext())
    caps=[n for root in docs.values() for n in root.findall('.//x:p[@class="caption"]',h.NS)]
    assert [h.plain(n) for n in caps]==[caption.replace('†','')],'authored closing quote and parenthesis remain once'
    assert h.body_texts(docs)==[body] and calls==[]


def test_single_component_closing_parenthesis_retains_existing_dagger_navigation(tmp_path,monkeypatch):
    p,body,caption,_=fixture('');calls=refuse_quote_crop(monkeypatch)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);h.assert_exact_returns(docs,1)
    assert h.body_texts(docs)==[body] and calls==[]


def test_authored_closing_quote_without_source_symbol_cannot_borrow_note_owner(tmp_path,monkeypatch):
    p,body,caption,_=fixture('"',source_symbol=False);calls=refuse_quote_crop(monkeypatch)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    assert h.refs_and_notes(docs)[0]==[] and calls==[]
    assert h.body_texts(docs)==[body]
    assert any(h.plain(n)==caption for root in docs.values() for n in root.findall('.//x:p[@class="caption"]',h.NS))
