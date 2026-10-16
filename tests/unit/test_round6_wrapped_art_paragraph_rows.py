"""SOURCE row ownership permits a float beside an earlier paragraph row.

This exercises final EPUB publication, including an original-context figure,
without inventing caption words or letting unrelated columns authorize a seam.
"""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline
_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_s=importlib.util.spec_from_file_location('round6_wrapped_rows_ink',_helper)
h=importlib.util.module_from_spec(_s);_s.loader.exec_module(h)
pytestmark=pytest.mark.unit

@pytest.mark.parametrize('context',[False,True])
@pytest.mark.parametrize('barrier',['none','terminal','indent','heading'])
def test_caption_beside_an_earlier_source_row_does_not_interrupt_sentence(tmp_path,monkeypatch,context,barrier):
    p=h.InkPage()
    lines=['The quiet observers kept careful records',
           'while comparing the figures and following',
           'several ordinary paths through the valley',
           'before they returned to the northern road',
           'and continued their account beneath',
           'the']
    if barrier=='terminal':lines[-1]='the completed account.'
    left=' '.join(lines)
    p.text(lines,'left',x=22,y=55,size=6)
    # The caption is beside an EARLIER prose row, with an actual blank lateral
    # gap; it does not lie beside the paragraph's final printed tail.
    rect=[172,76,271,112]
    p.page.draw_rect(rect,color=(0,0,0),fill=(.8,.8,.8),width=1)
    p.page.draw_line((172,76),(271,112),color=(0,0,0),width=1)
    p.blocks.append(h.model_block('A connected original sketch','art','figure',rect))
    p.text('A separate labelled sketch.','cap','caption',x=168,y=91,size=6)
    if context:p.page.draw_line((220,76),(220,48),color=(0,0,0),width=2)
    if barrier=='heading':p.text('A Separate Section','heading','heading',x=22,y=205,size=8)
    right='quiet canopy while preserving every source word in order.'
    p.text(right,'right',x=37 if barrier=='indent' else 22,y=235,size=6)
    later='A separate final paragraph ends the ordinary account.'
    p.text(later,'later',x=22,y=300,size=6)
    _,report,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    records={b['id']:b for b in report['pages'][0]['blocks']}
    assert records['left']['printed_tail']==lines[-1].split()[-1] and records['right']['printed_head']=='quiet'
    assert records['cap']['printed_head']=='A' and records['cap']['printed_tail']=='sketch.'
    texts=h.body_texts(docs);expected=left+' '+right
    if barrier=='none':
        assert texts==[expected,later], 'a lateral caption beside an earlier SOURCE row cannot split the continuous paragraph'
        nodes=h.body_nodes(docs)
        assert next(i for i,n in enumerate(nodes) if h.plain(n)==expected)<next(i for i,n in enumerate(nodes) if n.tag.endswith('}figure'))
    else:assert expected not in texts and left in texts and right in texts
    assert [h.plain(n) for n in h.body_nodes(docs) if n.get('class')=='caption']==['A separate labelled sketch.']
