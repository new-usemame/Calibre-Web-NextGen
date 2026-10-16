"""A source-bound table cannot float a missing or mismatched body endpoint."""
import pytest
from cps.services.reflow import vision_pipeline

@pytest.mark.parametrize('case',['exact','missing_head','missing_tail','mismatched_head','indent'])
def test_bound_captioned_table_float_requires_exact_source_endpoints(case):
    def record(ident,role,box,**kw):
        return dict(id=ident,role=role,bbox=box,source_regions=[box],continues_previous=False,continues_next=False,guard=[],**kw)
    tail='Quiet travellers continue with'
    head='patient workers before dawn.'
    left=record('left','body',[20,280,240,310],printed_tail='with')
    right=record('right','body',[320,190,540,225],printed_head='patient',first_line_x=320,opening_column_left=320)
    art=record('art','table',[350,35,500,125],source_bound_art=True)
    caption=record('caption','caption',[320,150,540,170])
    if case=='missing_head':right['printed_head']=None
    if case=='missing_tail':left['printed_tail']=None
    if case=='mismatched_head':right['printed_head']='careful'
    if case=='indent':right['first_line_x']=335
    blocks=[left,art,caption,right]
    source=[f'<p>{tail}</p>','<figure><img src="table.png" alt="A table"/></figure>','<p class="caption">A source table.</p>',f'<p>{head}</p>']
    page={'body':list(source)}
    vision_pipeline._join_scanned_regions(page,blocks)
    if case=='exact':
        assert page['body']==[f'<p>{tail} {head}</p>',*source[1:3]],'only exact unindented SOURCE endpoints permit a floating table sentence'
    else:
        assert page['body']==source,'a table proof cannot replace missing SOURCE endpoint or indentation evidence'
