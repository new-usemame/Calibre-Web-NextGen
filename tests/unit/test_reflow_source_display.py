"""Original-source orientation and inspection preserve physical relationships."""
import json
import pymupdf
import pytest
from cps.services.reflow import extract

pytestmark=pytest.mark.unit

@pytest.mark.parametrize('pdf_rotation,correction',[(0,90),(90,0),(0,0),(90,270)])
def test_reading_region_preserves_rotated_source_pixels_and_coordinates(pdf_rotation,correction):
    from cps.services.reflow.source_display import SourceDisplay
    doc=pymupdf.open();p=doc.new_page(width=120,height=180)
    p.draw_rect((0,0,60,90),fill=(1,0,0),color=None)
    p.draw_rect((60,90,120,180),fill=(0,0,1),color=None)
    p.set_rotation(pdf_rotation)
    source=SourceDisplay(doc,0,{'layer':'ocr','orientation':correction,'source_rotation':pdf_rotation,'page_rect':tuple(p.rect)})
    whole=source.pixmap(scale=1)
    reference=p.get_pixmap(matrix=pymupdf.Matrix(1,1).prerotate(correction),alpha=False)
    assert (whole.width,whole.height,whole.samples)==(reference.width,reference.height,reference.samples)
    box=pymupdf.Rect(10,10,50,70)
    crop=source.pixmap(box,scale=1)
    for y in range(crop.height):
        for x in range(crop.width):
            assert crop.pixel(x,y)==whole.pixel(x+10,y+10)
    assert source.source_rect(box).get_area()==box.get_area()
    doc.close()


def test_inspection_tiles_cover_source_with_context_overlap_and_bounded_geometry():
    from cps.services.reflow.source_display import inspection_tiles
    rect=pymupdf.Rect(0,0,842,595)
    tiles=inspection_tiles(rect)
    assert len(tiles)>2 and len(tiles)<=64
    assert all(t.width<=240 and t.height<=320 for t in tiles)
    assert min(t.x0 for t in tiles)==0 and max(t.x1 for t in tiles)==842
    assert min(t.y0 for t in tiles)==0 and max(t.y1 for t in tiles)==595
    for y in range(595):
        row=sorted((t.x0,t.x1) for t in tiles if t.y0<=y<t.y1)
        end=0
        for left,right in row:assert left<=end;end=max(end,right)
        assert end==842
    with pytest.raises(ValueError):inspection_tiles((0,0,float('inf'),1))


@pytest.mark.parametrize('kind',['grid','skew_grid','prose','single_rule','parallel_rules'])
def test_relational_grid_requires_orthogonal_repeated_rules(kind):
    from cps.services.reflow.source_display import SourceDisplay,ruled_grid
    doc=pymupdf.open();page=doc.new_page(width=240,height=240)
    if kind in ('grid','skew_grid','parallel_rules'):
        skew=.02 if kind=='skew_grid' else 0
        for y in range(30,211,30):page.draw_line((20,y),(220,y+200*skew),width=.7)
        if kind!='parallel_rules':
            for x in range(20,221,40):page.draw_line((x,30),(x,210+200*skew),width=.7)
    elif kind=='single_rule':page.draw_line((20,80),(220,80),width=1)
    else:
        for y in range(30,220,12):page.insert_text((20,y),'Ordinary paragraph continues.',fontsize=10)
    display=SourceDisplay(doc,0,{'layer':'ocr','orientation':0})
    proof=ruled_grid(display,(20,30,220,215))
    assert bool(proof)==(kind in ('grid','skew_grid'))
    if proof:assert proof['horizontal_rules']>=3 and proof['vertical_rules']>=3
    doc.close()


def test_actual_epub_shows_original_grid_before_unchanged_marked_ocr_and_excludes_wrappers(tmp_path):
    import zipfile
    from cps.services.reflow import assemble,build_epub,structural_ops as ops
    from cps.services.reflow.enriched_source import prepare_source_page
    doc=pymupdf.open();page=doc.new_page(width=300,height=400)
    for y in range(30,211,30):page.draw_line((20,y),(220,y),width=.7)
    for x in range(20,221,40):page.draw_line((x,30),(x,210),width=.7)
    doc.save(tmp_path/'source.pdf');doc.close();doc=pymupdf.open(tmp_path/'source.pdf')
    elements=[assemble.Element('p',runs=[['t','Alpha "Beta" Gamma']],pno=0,bbox=(20,30,220,215)),
              assemble.Element('p',runs=[['t','Ordinary prose continues.']],pno=0,bbox=(20,260,260,280))]
    book=assemble.Book(elements=elements,pages={0:elements})
    source=prepare_source_page(book,0,{'layer':'ocr','orientation':0},[{'token':'Beta','confidence':40}])
    prepared=ops.prepare(book,doc,0,'display-test',json.loads(source.provenance_json),source_page=source)
    assert 'e0' in json.loads(prepared.coverage_json)['source_grid_elements']
    assert all(c['element_id']!='e0' for c in prepared.candidates())
    target=tmp_path/'grid.epub'
    result=build_epub.build(book,str(target),doc=doc,source_pages={0:source})
    assert build_epub.validate(str(target))==[]
    with zipfile.ZipFile(target) as z:
        chapter=z.read('OEBPS/'+result.chapters[0]['href']).decode()
        assert 'original_p0000_layout_0.jpg' in chapter
        assert chapter.index('original_p0000_layout_0.jpg')<chapter.index('Alpha')
        assert 'OCR transcription. Read the original above for the layout and labels.' in chapter
        assert source.html.split('\n')[0] in chapter
        original=z.read('OEBPS/original-p0000.xhtml').decode()
        assert 'inspection_0' in original and 'Return to reflowed PDF page' in original
        for name in z.namelist():
            if 'inspection_' in name and name.endswith('.jpg'):
                pix=pymupdf.Pixmap(z.read(name));assert pix.width>=600
    doc.close()


@pytest.mark.parametrize('angle',[0,90,270])
def test_giant_source_and_grid_query_are_bounded_before_allocation(angle):
    from cps.services.reflow.source_display import SourceDisplay,GRID_QUERY_PIXELS
    class Page:
        rect=pymupdf.Rect(0,0,100000,90000);rotation=0
        def get_pixmap(self,**kwargs):
            region=kwargs['clip']*kwargs['matrix']
            assert region.irect.width*region.irect.height<=self.limit
            self.calls+=1
            return 'bounded'
    page=Page();page.calls=0;display=SourceDisplay([page],0,{'layer':'ocr','orientation':angle})
    for limit in (extract.MAX_RASTER_PIXELS,GRID_QUERY_PIXELS):
        page.limit=limit
        assert display.pixmap(scale=3,max_pixels=limit)=='bounded'
    assert page.calls==2
    for bad in [(0,0,0,1),(0,0,float('nan'),2),(0,0,float('inf'),2)]:
        with pytest.raises(ValueError):display.pixmap(bad)
    assert page.calls==2


def test_preexisting_grid_plan_is_rejected_at_final_builder_before_publication(tmp_path,monkeypatch):
    from cps.services.reflow import assemble,build_epub,structural_ops as ops,source_display
    from cps.services.reflow.enriched_source import prepare_source_page
    doc=pymupdf.open();page=doc.new_page(width=240,height=240)
    for y in range(30,211,30):page.draw_line((20,y),(220,y),width=.7)
    for x in range(20,221,40):page.draw_line((x,30),(x,210),width=.7)
    doc.save(tmp_path/'source.pdf');doc.close();doc=pymupdf.open(tmp_path/'source.pdf')
    e=assemble.Element('p',runs=[['t','Quoted "cell content" across source rows.']],bbox=(20,30,220,215))
    book=assemble.Book(elements=[e],pages={0:[e]})
    source=prepare_source_page(book,0,{'layer':'ocr','orientation':0})
    with monkeypatch.context() as patch:
        patch.setattr(source_display,'grid_regions',lambda *args:{})
        previous=ops.prepare(book,doc,0,'pre-display-contract',json.loads(source.provenance_json),source_page=source)
        choice=previous.candidates()[0]['candidate_id']
        plan=previous.accept(book,doc,{'protocol':ops.PROTOCOL,'snapshot_id':previous.snapshot_id,'select':[choice]},source_page=source)
    target=tmp_path/'rejected.epub'
    with pytest.raises(ops.ContractError,match='source grid'):
        build_epub.build(book,str(target),doc=doc,source_pages={0:source},operation_plans=[plan])
    assert not target.exists()
    doc.close()


def test_grid_extent_follows_connected_ruling_beyond_inward_ocr_box():
    from cps.services.reflow import assemble
    from cps.services.reflow.source_display import grid_regions
    doc=pymupdf.open();page=doc.new_page(width=300,height=300)
    for y in range(30,241,30):page.draw_line((20,y),(260,y),width=1)
    for x in range(20,261,40):page.draw_line((x,30),(x,240),width=1)
    e=assemble.Element('p',runs=[['t','Incomplete inner OCR cells']],bbox=(60,60,220,210))
    book=assemble.Book(elements=[e],pages={0:[e]})
    region=grid_regions(book,doc,0,{'layer':'ocr','orientation':0})[0]['reading_bbox']
    assert region[0]<=20 and region[1]<=30 and region[2]>=260 and region[3]>=240
    doc.close()
