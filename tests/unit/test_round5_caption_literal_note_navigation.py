"""Source-free caption symbols must link only their physically owned note."""
import importlib.util
from pathlib import Path
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('round5_caption_note_ink',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit


def fixture(role='caption',glyph='†',owner=True):
    p=h.InkPage();font=pymupdf.Font('cjk')
    p.page.insert_font(fontname='unicode',fontbuffer=font.buffer)
    def ink(text,x,y,size=4):
        assert x+font.text_length(text,fontsize=size)<h.W
        p.page.insert_text((x,y),text,fontsize=size,fontname='unicode')
    body='Careful observers compare several independently measured specimens.'
    p.text(body,'body',y=45,size=6)
    p.figure('diagram',caption=False)
    origin='A connected diagram records original measurements (R. Vale.'+glyph+')'
    ink(origin,22,194)
    p.blocks.append(h.model_block(origin,'origin',role,[20,185,280,198]))
    note='This independently printed citation identifies the original archival observations.'
    if owner:
        ink(glyph+' '+note,22,330)
        p.blocks.append(h.model_block(note,'note','footnote',[20,320,280,335],marker=glyph))
    return p,body,origin,note


@pytest.mark.parametrize('glyph',['*','†'])
def test_caption_source_symbol_targets_own_note_and_exact_return(tmp_path,monkeypatch,glyph):
    p,body,origin,note=fixture(glyph=glyph)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    refs,notes=h.assert_exact_returns(docs,1)
    name,ref=refs[0]
    root=docs[name]
    captions=root.findall('.//x:p[@class="caption"]',h.NS)
    assert any(ref in list(n.iter()) for n in captions),'the source caption owns this reference action'
    target=h.destination(name,ref.get('href'))
    selected=next(n for nname,n in notes if (nname,n.get('id'))==target)
    assert note in ''.join(selected.itertext())
    assert h.body_texts(docs)==[body],'caption or note words do not become BODY'
    assert sum(origin.replace(glyph,'') in h.plain(n) for n in captions)==1,'authored caption words occur once'


@pytest.mark.parametrize('role',['heading','figure'])
def test_noncaption_authored_symbol_does_not_invent_note_origin(tmp_path,monkeypatch,role):
    p,body,origin,note=fixture(role=role)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    assert h.refs_and_notes(docs)[0]==[],'title/art labels cannot acquire caption-only note ownership'
    assert h.body_texts(docs)==[body]
    assert any(note in ''.join(n.itertext()) for _,n in h.refs_and_notes(docs)[1])


def test_caption_symbol_without_finite_note_owner_stays_literal(tmp_path,monkeypatch):
    p,body,origin,_=fixture(owner=False)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    assert h.refs_and_notes(docs)[0]==[],'an authored caption glyph without an owner is not an action'
    assert h.body_texts(docs)==[body]
    assert any(origin==h.plain(n) for root in docs.values() for n in root.findall('.//x:p[@class="caption"]',h.NS))
