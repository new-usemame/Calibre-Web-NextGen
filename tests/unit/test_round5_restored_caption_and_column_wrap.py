"""SOURCE-free publication: restored body rows, caption instances and note columns."""
import importlib.util
import json
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline

_helper=Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():_helper=Path(vision_pipeline.__file__).parents[3]/'tests/unit/test_round4_defects.py'
_s=importlib.util.spec_from_file_location('round5_restored_ink',_helper)
h=importlib.util.module_from_spec(_s);_s.loader.exec_module(h)
pytestmark=pytest.mark.unit


@pytest.mark.parametrize('stale',[True,False])
def test_caption_cannot_borrow_a_restored_body_reference_instance(tmp_path,monkeypatch,stale):
    p=h.InkPage()
    lines=['The careful observers gathered near the coastal station',
           'and compared several independently recorded measurements',
           'before returning with the specimens described in Plate B.']
    for line,y in zip(lines,[45,58,71]):p.page.insert_text((22,y),line,fontsize=5)
    body=' '.join(lines)
    p.blocks.append(h.model_block(body,'body','body',[20,37,270,60]))
    p.page.insert_text((125,91),'Plate B.',fontsize=5)
    p.blocks.append(h.model_block('Plate B.','cap','caption',[20,64,270,74] if stale else [123,84,148,94]))
    p.page.draw_rect([35,108,260,175],color=(0,0,0),width=1)
    p.blocks.append(h.model_block('A complete outlined source diagram','art','figure',[35,108,260,175]))
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p])
    assert h.body_texts(docs)==[body],'restored reference words stay in the body exactly once'
    caps=[n for n in h.body_nodes(docs) if n.get('class')=='caption']
    assert len(caps)==1 and h.plain(caps[0])=='Plate B.'
    spans=caps[0].findall('.//x:span',h.NS)
    regions=[r for span in spans for r in json.loads(span.get('data-source-regions','[]'))]
    assert regions and all(r[1]>80 for r in regions),'the caption owns the separately printed caption row, never the restored inline body reference'


def test_complete_note_runs_left_column_then_right_column_above_opening(tmp_path,monkeypatch):
    p=h.InkPage();body='A complete ordinary body paragraph remains outside the two notes.'
    p.text(body,'body',size=5)
    first='The first separate source note finishes with an independent explanation.'
    p.text('* '+first,'one','footnote',x=22,y=280,size=3,marker='*',model=first)
    left=['The second source note opens with measured observations',
          'then records several independent descriptions of the specimens',
          'before its printed continuation moves into the next column']
    right=['where careful workers preserve every original source detail',
           'and conclude the second distinct explanation without a new note.']
    for line,y in zip(left,[315,328,341]):p.page.insert_text((22,y),line,fontsize=3)
    for line,y in zip(right,[300,313]):p.page.insert_text((164,y),line,fontsize=3)
    note=' '.join(left+right)
    p.blocks.append(h.model_block(note,'two','footnote',[20,308,133,345],marker='†'))
    p.page.draw_line((22,270),(130,270),color=(0,0,0))
    p.page.draw_line((164,287),(280,287),color=(0,0,0))
    _,_,docs=h.convert_ink(tmp_path,monkeypatch,[p]);_,notes=h.refs_and_notes(docs)
    texts=[h.plain(n)for _,n in notes]  # Exclude generated controls, preserving every authored word and tail.
    assert h.body_texts(docs)==[body],'column continuation words never become recovered body prose'
    assert sum(note in text for text in texts)==1,'complete left-to-right note order survives a right-column continuation above the opening'
    assert sum(first in text for text in texts)==1 and len(notes)==2


@pytest.mark.parametrize('proved',[True,False])
def test_unmatched_caption_attaches_only_to_a_proved_source_context_object(tmp_path,monkeypatch,proved):
    class GarbledCaption(h.InkPage):
        def finish(self):
            image,words,answer=super().finish()
            for word in words:
                if 178<word['bbox'][1]<200:word['text']='unreadable'
            return image,words,answer
    p=GarbledCaption();a='The careful observers compare the';b='original instruments before dawn.'
    p.text(a,'a',x=22,y=45,size=7)
    # A physically complete object crosses the source prose territory. It must
    # remain accessible as a context object, without inventing a caption owner.
    p.page.draw_rect([35,75,260,175],color=(0,0,0),width=1)
    p.page.draw_line((35,75),(35,41),color=(0,0,0),width=1)
    p.blocks.append(h.model_block('A connected original apparatus','art','figure' if proved else 'other',[35,75,260,175]))
    p.page.insert_text((100,190),'A complete source apparatus.',fontsize=5)
    p.blocks.append(h.model_block('A complete source apparatus.','cap','caption',[98,184,220,246]))
    p.text(b,'b',x=22,y=235,size=7)
    p.text('Another ordinary authored paragraph begins here.','later',x=22,y=270,size=5)
    _,result,docs=h.convert_ink(tmp_path,monkeypatch,[p]);texts=h.body_texts(docs)
    if proved:
        assert result['degraded_figure_count']==1,'the control exercises an actual original-page context carrier'
        assert texts==[a+' '+b,'Another ordinary authored paragraph begins here.'],'unmatched stale caption remains with source-proved context art outside the continuing sentence'
    else:
        assert a in texts and b in texts and a+' '+b not in texts,'an unproved OTHER object cannot earn sentence continuation'
