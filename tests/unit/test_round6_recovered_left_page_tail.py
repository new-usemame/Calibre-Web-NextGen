"""A source-correlated recovered page tail needs its own next-page boundary proof."""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('round6_recovered_left_tail_ink',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)
pytestmark=pytest.mark.unit


@pytest.mark.parametrize('barrier',['none','terminal','indent','right_heading','note_tail','caption_tail','explicit_other','disputed_head'])
def test_recovered_unfinished_left_tail_continues_onto_its_exact_next_page_body(tmp_path,monkeypatch,barrier):
    p=h.InkPage();q=h.InkPage()
    preceding='An earlier separate observation ends with a full stop.'
    p.text(preceding,'preceding',x=22,y=55,size=6)
    p.figure()
    tail='The next investigation was carefully directed toward the valley by'
    head='patient workers following the ordinary river course.'
    if barrier=='terminal':tail+='.'
    if barrier in ('note_tail','caption_tail','explicit_other'):
        role={'note_tail':'footnote','caption_tail':'caption','explicit_other':'other'}[barrier]
        p.text(tail,'left-tail',role,x=22,y=330,size=6,marker='7' if role=='footnote' else '')
    else:
        # This independent original row is omitted from the locked model.
        # The preceding BODY sentence is complete, so backward recovery cannot
        # establish a continuation; the next physical page supplies its proof.
        p.page.insert_text((22,330),tail,fontsize=6)
    if barrier=='indent':
        q.page.insert_text((37,55),head,fontsize=6)
        second='The next source row starts at the ordinary column margin.'
        q.page.insert_text((22,66),second,fontsize=6)
        q.blocks.append(h.model_block(head+' '+second,'right','body',[21,46,282,70]))
        head+=' '+second
    else:
        source=head.replace('patient','quiet') if barrier=='disputed_head' else head
        q.text(source,'right','heading' if barrier=='right_heading' else 'body',x=22,y=55,size=6,model=head)
        if barrier=='disputed_head':q.blocks[-1]['low_confidence']=[0]
    later='A separate concluding paragraph ends the ordinary survey.'
    q.text(later,'later',x=22,y=125,size=6)
    path,report,docs=h.convert_ink(tmp_path,monkeypatch,[p,q])
    texts=h.body_texts(docs);complete=tail+' '+head
    assert preceding in texts and later in texts
    if barrier=='none':
        left=next(b for b in report['pages'][0]['blocks'] if any(g.get('kind')=='omitted_pixel_line_recovery' for g in b.get('guard',[])))
        assert left['printed_tail']=='by' and left['printed_tail_box'] is not None
        assert texts==[preceding,complete,later], 'an independently recovered unfinished source tail cannot remain OTHER at the next physical page boundary'
        nodes=h.body_nodes(docs)
        assert next(i for i,n in enumerate(nodes) if n.tag.endswith('}figure'))<next(i for i,n in enumerate(nodes) if h.plain(n)==complete), 'the earlier original figure stays before the distinct new recovered paragraph'
        spans=[n for root in docs.values() for n in root.findall('.//x:span',h.NS) if n.get('data-source-page') is not None]
        assert {n.get('data-source-page') for n in spans}>={'0','1'}
    else:
        assert complete not in texts, 'note/caption/explicit OTHER, punctuation, source disagreement and indentation remain paragraph barriers'
        if barrier=='note_tail':
            _,notes=h.refs_and_notes(docs)
            assert any(tail in ' '.join(''.join(n.itertext()).split()) for _,n in notes)
            assert all(tail not in t for t in texts)
        elif barrier=='caption_tail':assert any(h.plain(n)==tail and n.get('class')=='caption' for n in h.body_nodes(docs))
        else:assert any(tail in t for t in texts)
