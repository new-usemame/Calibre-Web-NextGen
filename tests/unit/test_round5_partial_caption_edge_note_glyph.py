"""SOURCE-free complete nearby caption rows and their original note glyphs.

RAW already reads the attribution. An incomplete >=.6 local match cannot erase
its nearby final physical row, or borrow BODY or artwork words elsewhere.
"""
import importlib.util
import json
from pathlib import Path
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline, vision_pixels

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('round5_caption_edge_ink',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit


def fixture(mode='nearby',glyph=None,complete_hint=False):
    p=h.InkPage();font=pymupdf.Font('cjk')
    p.page.insert_font(fontname='unicode',fontbuffer=font.buffer)
    def ink(text,x,y,size=4):
        assert x+font.text_length(text,fontsize=size)<h.W
        p.page.insert_text((x,y),text,fontsize=size,fontname='unicode')
    body='Careful observers compare several independently measured specimens.'
    p.text(body,'body',y=45,size=6)
    p.figure('diagram',caption=False)
    opening=['A carefully connected diagram preserves several independently',
             'measured specimens alongside recorded experimental observations']
    tail='The original attribution identifies researcher R. Vale here.'
    assert len(' '.join(opening).split())==13 and len(tail.split())==8
    for line,y in zip(opening,[194,204]):ink(line,22,y)
    y=214 if mode in ('nearby','body') else 265 if mode=='remote' else 137
    ink(tail+(glyph or ''),22,y)
    if mode in ('body','remote'):p.blocks.append(h.model_block(tail,'authorbody','body',[20,y-8,280,y+4]))
    raw=' '.join(opening)+' '+tail
    p.blocks.append(h.model_block(raw,'cap','caption',[20,184,280,218 if complete_hint else 205]))
    note='This independently printed citation identifies the original archival observations.'
    if glyph:
        ink(glyph+' '+note,22,330)
        p.blocks.append(h.model_block(note,'note','footnote',[20,320,280,335],marker=glyph))
    return p,body,' '.join(opening),tail,raw,note


def caption_nodes(docs):
    return [n for root in docs.values() for n in root.findall('.//x:p[@class="caption"]',h.NS)]


def source_regions(nodes):
    return [r for n in nodes for span in n.findall('.//x:span',h.NS)
            for r in json.loads(span.get('data-source-regions','[]'))]


def test_caption_partial_local_match_retains_complete_nearby_source_row(tmp_path,monkeypatch):
    p,body,opening,tail,raw,_=fixture()
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);caps=caption_nodes(docs)
    assert [h.plain(n) for n in caps]==[raw],'the eight already-read attribution words remain in their own caption once'
    assert any(r[3]>210 for r in source_regions(caps)),'publication owns the actual nearby final SOURCE row beyond the tight hint'
    assert h.body_texts(docs)==[body]


@pytest.mark.parametrize('glyph',['*','†'])
@pytest.mark.parametrize('complete_hint',[False,True])
def test_omitted_raw_caption_glyph_restores_only_own_source_note_action(tmp_path,monkeypatch,glyph,complete_hint):
    p,body,opening,tail,raw,note=fixture(glyph=glyph,complete_hint=complete_hint)
    # Literal caption text deliberately omits the printed symbol. Source words,
    # original pixels and finite local note owner supply the terminal evidence.
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);caps=caption_nodes(docs)
    assert [h.plain(n) for n in caps]==[raw],'complete RAW caption attribution is preserved without invented lexical words'
    refs,notes=h.assert_exact_returns(docs,1);name,ref=refs[0]
    assert ''.join(ref.itertext())==glyph and any(ref in list(n.iter()) for n in caps)
    target=h.destination(name,ref.get('href'))
    assert note in ''.join(next(n for nn,n in notes if (nn,n.get('id'))==target).itertext())
    assert any(r[3]>210 for r in source_regions(caps))
    assert h.body_texts(docs)==[body]


@pytest.mark.parametrize('mode',['remote','body','art'])
def test_caption_edge_cannot_borrow_remote_body_or_artwork_instances(tmp_path,monkeypatch,mode):
    p,body,opening,tail,raw,_=fixture(mode=mode)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);caps=caption_nodes(docs)
    assert [h.plain(n) for n in caps]==[opening],'a model tail without nearby independently owned caption ink cannot relocate those words'
    assert source_regions(caps) and all(r[3]<210 for r in source_regions(caps))
    assert h.refs_and_notes(docs)[0]==[]
    assert h.body_texts(docs)==([body,tail] if mode in ('body','remote') else [body])
