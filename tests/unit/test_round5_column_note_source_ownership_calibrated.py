"""SOURCE-free notes: complete model text owns its actual column instances.

Physical star/dagger glyphs are portable bundled CJK font outlines. Narrow
source hints exclude a short left terminal word and the right note opening;
recovery must not concatenate either note into ordinary BODY.
"""
import importlib.util
from pathlib import Path
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_s=importlib.util.spec_from_file_location('round5_column_note_ink',_helper)
h=importlib.util.module_from_spec(_s);_s.loader.exec_module(h)
pytestmark=pytest.mark.unit


def fixture(standalone=False,coarse=True,raised=False):
    p=h.InkPage();font=pymupdf.Font('cjk');p.page.insert_font(fontname='unicode',fontbuffer=font.buffer)
    def ink(text,x,y,size=3.2):
        assert x+font.text_length(text,fontsize=size)<h.W,'all synthetic source glyphs must fit'
        p.page.insert_text((x,y),text,fontsize=size,fontname='unicode')
    prefixes=['The first complete prose sentence cites careful observations.',
              "A second ordinary sentence cites another independent explanation,'"]
    bodies=[text+(' ' if standalone else '')+mark for text,mark in zip(prefixes,['*','†'])]
    for text,mark,y in zip(prefixes,['*','†'],[45,60]):
        ink(text,22,y)
        x=22+font.text_length(text,fontsize=3.2)+(1.5 if standalone else 0)
        ink(mark,x,y-2.5 if raised else y,size=2.2 if raised else 3.2)
    for i,text in enumerate(bodies):p.blocks.append(h.model_block(text,f'b{i}','body',[20,35+i*15,282,49+i*15]))
    a='A source note records careful coastal observations in one'
    lefttail='Later details close this original left note.'
    b='Another printed source note begins with careful observations.'
    tails=['Its continuation preserves several useful observations within this distinct right column',
           'and additional measurements conclude the independently authored explanation for these specimens.']
    ink('* '+a,22,315);ink('vol.',134,315);ink(lefttail,22,338)
    ink('† '+b,164,300)
    for text,y in zip(tails,[326,338]):ink(text,164,y,size=2.9)
    p.page.draw_line((22,287),(128,287),color=(0,0,0));p.page.draw_line((164,287),(278,287),color=(0,0,0))
    left=a+' vol. '+lefttail;right=b+' '+' '.join(tails)
    p.blocks.append(h.model_block(left,'leftnote','footnote',[20,308,127,343] if coarse else [20,308,148,343],marker='*'))
    p.blocks.append(h.model_block(right,'rightnote','footnote',[160,321,280,343] if coarse else [160,292,296,343],marker='†'))
    return p,bodies,left,right


def test_complete_two_column_notes_keep_orphan_tail_instances_outside_body(tmp_path,monkeypatch):
    p,bodies,a,b=fixture()
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    body=' '.join(h.body_texts(docs))
    assert 'vol.' not in body and 'Another printed source note' not in body and 'Its continuation preserves' not in body, 'orphan note instances stay with their source note, never a recovered OTHER paragraph'
    _,notes=h.refs_and_notes(docs)
    text=[' '.join(''.join(n.itertext()).split()) for _,n in notes]
    assert sum(a in t for t in text)==1 and sum(b in t for t in text)==1
    assert all(not(a in t and b in t) for t in text),'left/right note owners remain distinct'


@pytest.mark.parametrize('standalone',[False,True])
@pytest.mark.parametrize('raised',[False,True])
def test_source_star_and_dagger_origins_link_exact_note_and_return(tmp_path,monkeypatch,standalone,raised):
    p,_,a,b=fixture(standalone=standalone,coarse=False,raised=raised)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);refs,notes=h.refs_and_notes(docs)
    assert len(refs)==2,'both actual printed symbol origins have navigation actions'
    targets={n.get('id'):n for _,n in notes}
    for _,ref in refs:
        note=targets.get(ref.get('href','').split('#')[-1]);assert note is not None
        content=''.join(note.itertext());marker=''.join(ref.itertext())
        assert (marker=='*' and a in content) or (marker=='†' and b in content),'each source glyph targets its own printed note'
        assert any(x.get('href')=='#'+ref.get('id','') for x in note.findall('.//x:a',h.NS)),'Return comes from this exact note'


def test_ordinary_arithmetic_star_is_not_a_printed_note_origin(tmp_path,monkeypatch):
    p=h.InkPage();text='An ordinary calculation states that 3 * 4 equals twelve.'
    p.text(text,'body',size=6)
    p.text('* A separate authored note has no matching origin in the calculation.','note','footnote',y=320,size=5,marker='*',model='A separate authored note has no matching origin in the calculation.')
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    assert h.body_texts(docs)==[text] and h.refs_and_notes(docs)[0]==[],'arithmetic punctuation is not promoted by lexical equality alone'


@pytest.mark.parametrize('glyph',['*','†'])
def test_symbol_without_selected_note_owner_stays_authored_punctuation(tmp_path,monkeypatch,glyph):
    p=h.InkPage();font=pymupdf.Font('cjk');p.page.insert_font(fontname='unicode',fontbuffer=font.buffer)
    text='An authored editorial symbol.'+glyph+' remains literal ordinary prose here.'
    p.page.insert_text((22,45),text,fontsize=4.3,fontname='unicode')
    p.blocks.append(h.model_block(text,'body','body',[20,35,282,50]))
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    assert h.body_texts(docs)==[text] and h.refs_and_notes(docs)[0]==[],'a source symbol does not invent a note owner'



def test_new_explicit_note_and_terminal_punctuation_preserve_distinct_owners(tmp_path,monkeypatch):
    p=h.InkPage();body='An ordinary prose paragraph remains outside all three separate notes.'
    p.text(body,'body',size=5)
    for ident,mark,text,y in [('one','*','The first authored note ends here.',290),
                            ('two','†','Another independently authored note begins afresh.',320),
                            ('three','‡','A final newly marked note owns its own wording.',350)]:
        f=pymupdf.Font('cjk');p.page.insert_font(fontname='unicode',fontbuffer=f.buffer)
        p.page.insert_text((22,y),mark+' '+text,fontsize=4,fontname='unicode')
        p.blocks.append(h.model_block(text,ident,'footnote',[20,y-7,282,y+3],marker=mark))
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);_,notes=h.refs_and_notes(docs)
    texts=[h.plain(n) for _,n in notes]
    assert h.body_texts(docs)==[body] and len(notes)==3
    expected=['The first authored note ends here.','Another independently authored note begins afresh.','A final newly marked note owns its own wording.']
    assert all(sum(s in t for t in texts)==1 for s in expected)
    assert all(sum(s in t for s in expected)==1 for t in texts),'new explicit markers and terminal note paragraphs prevent merging different notes'
