"""Synthetic paragraph ink must not leak into a neighboring long-run guard."""
import pytest
if __package__:
    from .test_round2_assembly import answer,block,optical,source
else:
    from test_round2_assembly import answer,block,optical,source
from cps.services.reflow import build_epub,source_display,vision_pipeline

UPPER='Patient travelers arrange ordinary tools beside quiet gardens while distant workers prepare safe shelters.'
LOWER='Careful readers examine useful maps before early journeys begin. Several companions carry sturdy baskets along narrow roads while others gather fresh supplies for the next village visit.'

@pytest.mark.parametrize('sloped',[False,True])
def test_tall_last_line_ink_does_not_borrow_next_paragraph_row(tmp_path,sloped):
    pixels=optical(UPPER,20,90,columns=8,step=45)
    # A tall glyph in the final upper row belongs to that row by its center,
    # but expands its rectangular union into the next printed paragraph.
    pixels[8]['bbox'][1]=102;pixels[8]['bbox'][3]=126
    lower=optical(LOWER,20,122,columns=8,step=45)
    if sloped:
        for words in (pixels,lower):
            for w in words:
                shift=(w['bbox'][0]-20)/315*3
                w['bbox'][1]-=shift;w['bbox'][3]-=shift
    pixels+=lower
    reading=answer(block(UPPER,ident='upper',bbox=(30,220,680,320)),block(LOWER,ident='lower',bbox=(30,300,680,490)))
    document=source([pixels]);package=build_epub._Package(str(tmp_path/'synthetic.epub'))
    try:
        display=source_display.SourceDisplay(document,0);regions={};owners={}
        bound,_=vision_pipeline.bind_geometry(reading,pixels,display,regions,owners)
        page,report=vision_pipeline.compile_page(bound,[],display,0,package,{},pixel_words=pixels,regions=regions,body_owners=owners)
        published={b['id']:b['published_tokens'] for b in report['blocks']}
        assert published['upper']==UPPER.split(), 'Next printed paragraph was published inside the correct terminal upper paragraph'
        assert published['lower']==LOWER.split(), 'Neighbor reservation stole actual opening words from the lower paragraph'
        assert [build_epub.block_text(p) for p in page['body']]==[UPPER,LOWER]
    finally:package.abandon();document.close()

@pytest.mark.parametrize('layout',['same_column','two_leaf'])
def test_own_dropped_row_still_falls_back_after_neighbor_allocation(tmp_path,layout):
    opening='Quiet readers examine useful maps before early journeys begin.'
    dropped='Patient workers carry sturdy baskets along narrow roads today.'
    neighbor='Several companions gather fresh supplies for the next village visit.'
    pixels=optical(opening,20,70,columns=10,step=35)+optical(dropped,20,90,columns=10,step=35)
    x,y=(20,145) if layout=='same_column' else (330,30)
    pixels+=optical(neighbor,x,y,columns=5,step=45)
    reading=answer(block(opening,ident='upper',bbox=(30,165,610,255)),block(neighbor,ident='neighbor',bbox=(int(x/600*1000),int(y/400*1000),int((x+225)/600*1000),int((y+35)/400*1000))))
    document=source([pixels]);package=build_epub._Package(str(tmp_path/'synthetic.epub'))
    try:
        display=source_display.SourceDisplay(document,0);regions={'upper':[[20,70,378,100]],'neighbor':[[x,y,x+223,y+40]]}
        page,report=vision_pipeline.compile_page(reading,[],display,0,package,{},pixel_words=pixels,regions=regions,body_owners={'neighbor':regions['neighbor'][0]})
        published={b['id']:b for b in report['blocks']}
        assert published['upper']['published_tokens']==(opening+' '+dropped).split(), 'Actual own omitted line was hidden by a neighboring owner'
        assert published['upper']['fallback'] and published['upper']['marked'], 'Recovered line lost conservative uncertainty'
        assert published['neighbor']['published_tokens']==neighbor.split()
    finally:package.abandon();document.close()

@pytest.mark.parametrize('case,expected',[
    ('only_halo_current_strict_neighbor',False),
    ('strict_current_only_halo_reserved_neighbor',True),
    ('strict_both_coherent_neighbor',False),
    ('only_halo_no_competing_owner',True),
    ('separate_columns',True),
])
def test_strict_physical_ownership_outweighs_neighbor_halo(case,expected):
    from types import SimpleNamespace
    import pymupdf
    current=[20,20,200,30];neighbor=[20,31,200,45]
    word={'text':'ordinary','bbox':[30,30,40,32]};owners={}
    if case=='strict_current_only_halo_reserved_neighbor':
        current,neighbor=neighbor,current;owners={'neighbor':neighbor}
    elif case=='strict_both_coherent_neighbor':
        neighbor=current[:];word['bbox']=[30,25,40,29];owners={'neighbor':neighbor}
    elif case=='only_halo_no_competing_owner':
        neighbor=[20,60,200,75]
    elif case=='separate_columns':
        neighbor=[300,20,480,45];word['bbox']=[30,25,40,29];owners={'neighbor':neighbor}
    reading=answer(block('ordinary',ident='current'),block('several',ident='neighbor'))
    display=SimpleNamespace(rect=pymupdf.Rect(0,0,600,400))
    found=vision_pipeline._owned_block_words(reading['blocks'][0],reading,[word],display,
        {'current':[current],'neighbor':[neighbor]},owners)
    assert bool(found)==expected, 'Physical strict ownership was overridden by an unrelated rectangle halo'
