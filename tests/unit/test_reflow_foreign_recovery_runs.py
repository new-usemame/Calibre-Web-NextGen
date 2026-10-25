"""Synthetic omitted-row recovery cannot duplicate another unit's occurrence."""
import pytest
from cps.services.reflow.vision_pipeline import _remove_foreign_word_runs

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('proof',['owned','elsewhere','ambiguous','unowned'])
def test_unbound_tail_is_removed_only_at_a_unique_own_to_foreign_boundary(proof):
    optical=[dict(text=t,bbox=[10+i*25,20,30+i*25,32])for i,t in enumerate('The garden stays green today.'.split())]
    boxes={0:optical[0]['bbox'],1:optical[1]['bbox']}
    if proof=='elsewhere':boxes[1]=[35,80,55,92]
    if proof=='ambiguous':optical=optical+optical[1:]
    regions={'other':[[60,18,160,34]]}if proof!='unowned'else{}
    result=dict(tokens='The garden stays green today.'.split(),boxes=boxes,marked={2,3,4},
        word_notes={i:dict(status='UNRESOLVED')for i in [2,3,4]},guard=[])
    _remove_foreign_word_runs(result,'first',{'blocks':[{'id':'first'},{'id':'other'}]},optical,regions)
    assert result['tokens']==(['The','garden']if proof=='owned'else'The garden stays green today.'.split())
    if proof=='owned':
        assert not result['marked']
        assert result['guard'][0]['runs'][0]['owner']=='other'
    else:assert not result['guard']


@pytest.mark.parametrize('competing_owner', [False, True])
def test_complete_body_tail_keeps_its_printed_paragraph_owner(competing_owner):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    text='The small tractor needs a new wheel beside the quiet garden gate.'
    with pymupdf.open() as doc:
        page=doc.new_page(width=500,height=180)
        page.insert_text((30,60),text,fontsize=12)
        words=[dict(text=w[4],bbox=list(w[:4]))for w in page.get_text('words')]
        display=SourceDisplay(doc,0)
        owned=words[:7];tail=words[7:]
        hint=vp.vision_reading.union([w['bbox']for w in owned])
        rect=display.rect
        body=dict(id='body',role='body',text=text,marker='',
            bbox=[hint[0]/rect.width*1000,hint[1]/rect.height*1000,
                  hint[2]/rect.width*1000,hint[3]/rect.height*1000],
            low_confidence=[],continues_previous=False,continues_next=False)
        regions={'body':[hint],'_instances':{vp._source_instance(w):'body'for w in owned}}
        if competing_owner:
            regions['_instances'][vp._source_instance(tail[0])]='foreign'
        answer,ids=vp.cover_missing_lines({'blocks':[body]},words,words,display,regions,{})
        if competing_owner:
            assert regions['_instances'][vp._source_instance(tail[0])]=='foreign'
            assert any(b['id']in ids for b in answer['blocks'])
        else:
            assert not ids,'A physically consecutive tail already read in this paragraph is not another unit'
            assert answer['blocks']==[body]
            assert all(regions['_instances'][vp._source_instance(w)]=='body'for w in words)
