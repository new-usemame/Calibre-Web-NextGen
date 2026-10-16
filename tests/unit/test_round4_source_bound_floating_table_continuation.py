"""Source-ink-bound tables may float after exact unindented sentence openings."""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline
_helper=Path(__file__).parents[1]/'test_round4_defects.py'
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_spec=importlib.util.spec_from_file_location('source_table_helpers',_helper)
h=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(h)

@pytest.mark.parametrize('scope',['same_page','pdf_turn'])
@pytest.mark.parametrize('barrier',['none','terminal','indent'])
def test_source_bound_table_and_caption_preserve_sentence_continuity(tmp_path,monkeypatch,scope,barrier):
    left=['The quiet travellers crossed the fields','before returning beneath the']
    right=['patient branches of a nearby tree','where the resting birds sang.']
    if barrier=='terminal':left[-1]='before returning beneath the tree.'
    page=h.InkPage();pages=[page]
    page.text(left,'left',y=50 if scope=='same_page' else 315)
    if scope=='pdf_turn':page=h.InkPage();pages.append(page)
    page.figure()
    table=next(b for b in page.blocks if b['id']=='art');table['role']='table';table['text']='A connected synthetic table with ruled rows.'
    # All cells/borders and the caption are source ink, not a blank box whose
    # schema role alone would be allowed to override a structural barrier.
    page.page.draw_line((35,120),(260,120),color=(0,0,0),width=1)
    page.page.draw_line((140,90),(140,175),color=(0,0,0),width=1)
    for point,label in [((55,112),'A'),((165,112),'B'),((55,145),'10'),((165,145),'20')]:
        page.page.insert_text(point,label,fontsize=8)
    cap=next(b for b in page.blocks if b['id']=='cap');cap['text']='A connected synthetic diagram.'
    page.text(right,'right',x=42 if barrier=='indent' else 24,y=218)
    if barrier=='indent':
        page.page.insert_text((24,246),'Another source line returns to the normal margin.',fontsize=9)
        page.blocks[-1]['text']+=' Another source line returns to the normal margin.'
        page.blocks[-1]['bbox'][3]=250/h.H*1000
    epub,result,docs=h.convert_ink(tmp_path,monkeypatch,pages)
    bodies=h.body_texts(docs);a=' '.join(left);b=' '.join(right)
    tables=[v for v in result['images'].values() if v['kind']=='table']
    assert len(tables)==1,'one whole source table remains outside BODY'
    assert tables[0]['bbox'][0]<=35 and tables[0]['bbox'][1]<=90 and tables[0]['bbox'][2]>=260 and tables[0]['bbox'][3]>=175,'all table corners survive'
    captions=[h.plain(n) for n in h.body_nodes(docs) if n.tag.endswith('}p') and n.attrib.get('class')=='caption']
    assert captions==['A connected synthetic diagram.'],'source caption survives once as caption'
    if barrier=='none':
        assert a+' '+b in bodies,'exact source-bound unfinished sentence must not be split by leading TABLE and caption'
        assert a not in bodies and b not in bodies,'the approved sentence must have one live paragraph'
    else:
        assert a in bodies and any(text.startswith(b) for text in bodies),'terminal punctuation and real source indentation preserve paragraph boundary'
