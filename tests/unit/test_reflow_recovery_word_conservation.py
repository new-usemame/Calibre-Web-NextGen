"""Synthetic source words and geometry for omitted citation-strip ownership."""
from types import SimpleNamespace
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline as vp

pytestmark=pytest.mark.unit


def test_short_printed_run_between_note_units_is_not_silently_lost():
    text='Collected examples volume three;'
    words=[dict(text=t,bbox=[30+i*40,100,65+i*40,110])for i,t in enumerate(text.split())]
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,300,400))
    def block(ident,text,top,bottom):
        return dict(id=ident,text=text,role='footnote',marker='7' if ident=='left' else '8',
            bbox=[80,top/400*1000,900,bottom/400*1000],low_confidence=[],
            continues_previous=False,continues_next=False)
    answer=dict(blocks=[block('left','A printed note ends here.',70,85),
                        block('right','Another printed note begins here.',130,145)])
    regions={'left':[[24,70,270,85]],'right':[[24,130,270,145]]}
    recovered,ids=vp.cover_missing_lines(answer,words,words,display,regions,{})
    assert len(ids)==1
    inserted=next(b for b in recovered['blocks']if b['id'] in ids)
    assert inserted['text']==text
    assert regions[inserted['id']]==[[30,100,185,110]]
    assert {_ for _ in regions['_instances'].values()}=={inserted['id']}


def test_rejected_note_keeps_its_own_opening_row_out_of_body_recovery(tmp_path,monkeypatch):
    from tests.unit.test_round4_defects import InkPage,model_block,convert_ink
    page=InkPage()
    lines=['Careful observers describe an ordinary sample beside the window.',
           'An omitted printed sentence still belongs to this continuous paragraph.',
           'The closing sentence records the final independent observation.']
    for i,text in enumerate(lines):page.page.insert_text((22,60+i*13),text,fontsize=5)
    page.blocks.append(model_block(lines[0]+' '+lines[2],'prose','body',[20,50,282,91]))
    page.page.insert_text((22,350),'* A distinct printed note explains a separate measurement.',fontsize=4)
    page.blocks.append(model_block(lines[1],'note','footnote',[20,343,200,354],marker='*'))
    _,result,_=convert_ink(tmp_path,monkeypatch,[page])
    records=result['pages'][0]['blocks']
    live=[' '.join(b['published_tokens'])for b in records
          if b['role']not in ('footnote','running_head','folio') and not b.get('degraded_source_context')]
    assert live==[' '.join(lines)]
    assert any(u['block']=='note' and u['unit_kind']=='footnote'for u in result['degraded_units'])
