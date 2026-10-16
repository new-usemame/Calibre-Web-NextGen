"""Independent endpoint and artwork-position barriers for floating prose."""
import pytest
from cps.services.reflow import vision_pipeline

@pytest.mark.parametrize('case',['missing_tail','mismatched_tail','mismatched_present_head','capital_opening','table','art_crosses_opening'])
def test_floating_prose_requires_source_tail_and_compatible_opening_evidence(case):
    tail='Quiet travelers continue with'
    head='patient workers before dawn.'
    if case=='capital_opening':head='Separate workers before dawn.'
    def record(ident,role,box,**kw):
        return dict(id=ident,role=role,bbox=box,source_regions=[box],continues_previous=False,continues_next=False,guard=[],**kw)
    left=record('left','body',[20,280,240,310],printed_tail='with')
    right=record('right','body',[320,190,540,225],printed_head=head.split()[0],first_line_x=320,opening_column_left=320)
    art=record('art','table' if case=='table' else 'figure',[350,35,500,200 if case=='art_crosses_opening' else 125])
    caption=record('caption','caption',[320,150,540,170])
    if case=='missing_tail':left['printed_tail']=None
    if case=='mismatched_tail':left['printed_tail']='without'
    if case=='mismatched_present_head':right['printed_head']='different'
    blocks=[left,art,caption,right]
    page={'body':[f'<p>{tail}</p>','<figure><img src="compass.png" alt="Compass"/></figure>','<p class="caption">A separate compass.</p>',f'<p>{head}</p>']}
    vision_pipeline._join_scanned_regions(page,blocks)
    assert page['body']==[f'<p>{tail}</p>','<figure><img src="compass.png" alt="Compass"/></figure>','<p class="caption">A separate compass.</p>',f'<p>{head}</p>'],f'Unsupported source endpoint/artwork was crossed: {case}'
