"""SOURCE-free exact sentence endpoints around a sequence of captioned art.

An unfinished body clause can end in a parenthetical figure reference; the closing parenthesis alone is not terminal punctuation.
Whole original context links and lower captions are outside the joined sentence.
Terminal punctuation, indentation and unsupported art remain barriers.
"""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_s=importlib.util.spec_from_file_location('round5_multiple_float_ink',_helper)
h=importlib.util.module_from_spec(_s);_s.loader.exec_module(h)
pytestmark=pytest.mark.unit


@pytest.mark.parametrize('barrier',['none','terminal','indented','uppercase'])
def test_parenthetical_reference_does_not_finish_an_unfinished_source_sentence(tmp_path,monkeypatch,barrier):
    p=h.InkPage()
    a='Quiet observers compare the'
    b='patient workers near the shore (see Plate B)'
    if barrier=='terminal':b='patient workers near the shore (see Plate B.)'
    c='careful collectors before dawn.'
    if barrier=='uppercase':c='Careful collectors start a separate account.'
    positions=[(22,45),(22,155),(35 if barrier=='indented' else 22,280)]
    for i,(text,(x,y)) in enumerate(zip([a,b,c],positions)):
        p.page.insert_text((x,y),text,fontsize=7)
        p.blocks.append(h.model_block(text,f'b{i}','body',[x-1,y-8,230,y+3]))
        if i==2:break
        # Upper caption belongs to art on the same physical row as body tail,
        # but is a distinct source instance far beyond its actual final word.
        top='Plate A.' if i==0 else 'Plate B.'
        lower='A complete outlined compass.' if i==0 else 'Another complete outlined compass.'
        p.page.insert_text((247,y),top,fontsize=5)
        p.blocks.append(h.model_block(top,f'upper{i}','caption',[245,y-6,281,y+3]))
        rect=[32,y+20,266,y+82]
        p.page.draw_rect(rect,color=(0,0,0),width=1)
        p.page.draw_line((35,y+23),(261,y+78),color=(0,0,0),width=1)
        p.blocks.append(h.model_block('A connected original source compass',f'art{i}','figure',rect))
        p.page.insert_text((22,y+96),lower,fontsize=5)
        p.blocks.append(h.model_block(lower,f'lower{i}','caption',[20,y+90,225,y+99]))
    # A later aligned source row proves column origin for indentation guard.
    later='Another independently authored paragraph begins here.'
    p.text(later,'later',x=22,y=315,size=5)
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);texts=h.body_texts(docs)
    if barrier=='none':
        assert texts==[a+' '+b+' '+c,later],'both captioned source objects remain after the complete sentence, including original-context carriers'
    elif barrier=='terminal':
        assert c in texts and all(b+' '+c not in t for t in texts),'actual terminal punctuation inside parenthesis is a barrier'
    elif barrier=='indented':
        assert c in texts and all(b+' '+c not in t for t in texts),'a source-indent opens a new paragraph'
    else:
        assert c in texts and all(b+' '+c not in t for t in texts),'a new uppercase paragraph is not an earned source continuation'
    caps=[h.plain(n) for n in h.body_nodes(docs) if n.get('class')=='caption']
    assert caps==['Plate A.','A complete outlined compass.','Plate B.','Another complete outlined compass.']
