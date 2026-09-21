"""Ordinary figures and their pixel probes share the chosen reading frame."""
import copy
from dataclasses import asdict
import pymupdf
import pytest
from cps.services.reflow import extract,source,assemble,build_epub
from cps.services.reflow.source_display import SourceDisplay

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('rotation,orientation',[(0,0),(90,0),(0,90),(90,270)])
def test_source_bound_ordinary_crop_uses_reading_frame(rotation,orientation):
    doc=pymupdf.open();page=doc.new_page(width=400,height=600)
    page.draw_rect((40,60,170,220),color=(1,0,0),fill=(1,0,0))
    page.draw_rect((220,340,360,560),color=(0,0,1),fill=(0,0,1))
    page.set_rotation(rotation)
    prov={'layer':'ocr','orientation':orientation,'source_rotation':rotation,'page_rect':list(page.rect)}
    display=SourceDisplay(doc,0,prov);box=list(display.reading_rect((40,60,170,220)))
    e=assemble.Element('fig',pno=0,bbox=box)
    geometry={'space':'reading','orientation':orientation,'source_rotation':rotation,'page_rect':list(page.rect)}
    book=assemble.Book(elements=[e],pages={0:[e]},figures=[{'pno':0,'bbox':box,'source_geometry':geometry}])
    chapters=[build_epub.Chapter(index=1,title='test',blocks=['<figure><img src="images/fig_p0000_0.jpg"/></figure>'])]
    # The legacy caller is still present for notes/old books. A new figure's
    # explicit reading-space provenance must not be transformed twice.
    old=source.Recovery(provenance={0:source.PageRecovery(pno=0,layer='ocr',orientation=orientation,
        source_rotation=rotation,page_rect=tuple(page.rect),derotation=tuple(page.derotation_matrix))})
    images,missing,blanks=build_epub._figure_images(chapters,doc,book,old.figure_rect)
    assert not missing and not blanks
    assert images['images/fig_p0000_0.jpg']==display.jpeg(box,scale=2,quality=85)
    doc.close()


@pytest.mark.parametrize('rotation,orientation',[(0,0),(90,0),(0,90),(90,270)])
def test_pixel_probe_mask_and_bounds_use_the_same_reading_frame(rotation,orientation):
    doc=pymupdf.open();page=doc.new_page(width=400,height=600)
    box=(80,100,200,250);page.draw_rect(box,color=(.5,0,.8),fill=(.5,0,.8))
    page.set_rotation(rotation)
    display=SourceDisplay(doc,0,{'layer':'ocr','orientation':orientation,'source_rotation':rotation})
    reading=display.query_document();expected=tuple(display.reading_rect(box))
    probe=extract.ScanPixelProbe(reading,0)
    assert probe.has_ink(expected)
    measured=probe.ink_bounds(tuple(display.rect))
    assert all(abs(a-b)<3 for a,b in zip(measured,expected))
    masked=extract.ScanPixelProbe(reading,0,mask=[expected])
    assert not masked.has_ink(tuple(display.rect)) and masked.ink_bounds(tuple(display.rect)) is None
    doc.close()


def test_reading_query_keeps_preallocation_limit_and_invalid_geometry_rejection():
    import math
    class Page:
        rect=pymupdf.Rect(0,0,1e8,2e8)
        rotation=0
        def get_pixmap(self,**kw):
            matrix,clip=kw['matrix'],kw['clip']
            assert (math.ceil(clip.width*matrix.a)+2)*(math.ceil(clip.height*matrix.d)+2)<=extract.MAX_RASTER_PIXELS
            return 'bounded-before-allocation'
    class Doc:
        def __getitem__(self,pno):return Page()
    reading=SourceDisplay(Doc(),0,{'layer':'ocr','orientation':0}).query_document()
    assert reading[0].get_pixmap(matrix=pymupdf.Matrix(3,3),clip=Page.rect)=='bounded-before-allocation'
    probe=extract.ScanPixelProbe(reading,0)
    for box in ((0,0,0,5),(0,0,float('nan'),5),(0,0,float('inf'),5)):
        with pytest.raises(ValueError):probe.ink_bounds(box)


def test_inset_page_background_and_artwork_are_normalized_once_without_changing_words():
    doc=pymupdf.open();page=doc.new_page(width=400,height=600);page.set_rotation(90)
    provenance={'layer':'ocr','orientation':0,'source_rotation':90,'page_rect':list(page.rect)}
    display=SourceDisplay(doc,0,provenance)
    image=(40,50,360,550);expected=tuple(display.reading_rect(image))
    line=extract.Line(spans=[extract.Span('This is the ordinary prose on the scanned page. '*4,12,'ocr',0,
        (100,100,400,115),115)],bbox=(100,100,400,115))
    raw=extract.RawPage(0,600,400,blocks=[extract.Block(0,line.bbox,[line])],
        images=[extract.Image(image,.66)],drawings=1,drawing_rects=[(50,70,80,110)])
    before=copy.deepcopy([b.to_dict() for b in raw.blocks])
    normalized=source.normalize_recovery_geometry(raw,doc,provenance)
    assert normalized.images[0].bbox==expected
    assert normalized.images[0].page_background and normalized.is_page_scan
    assert normalized.images[0].area_ratio==.66, 'do not fake the 80% threshold'
    assert normalized.drawing_rects==[tuple(display.reading_rect((50,70,80,110)))]
    assert [b.to_dict() for b in normalized.blocks]==before
    assert raw.images[0].bbox==image
    assert asdict(source.normalize_recovery_geometry(normalized,doc,provenance))==asdict(normalized)
    # A small image that does not contain the source prose is still a separate
    # illustration, even on an OCR page; no pixel-hash or page-number shortcut.
    raw.images=[extract.Image((50,70,80,110),.01)]
    assert not source.normalize_recovery_geometry(raw,doc,provenance).images[0].page_background
    doc.close()


def test_prose_footnote_is_not_figure_ink_but_its_printed_body_is_retained():
    doc=pymupdf.open();page=doc.new_page(width=400,height=600)
    page.insert_text((60,510),'7 A real footnote is already retained in the reading flow.',fontsize=10)
    note=assemble.Note(num=7,text='A real footnote is already retained in the reading flow.',pno=0,bbox=(55,495,340,520))
    figure={'pno':0,'bbox':(40,450,360,550),'needs_ink':True}
    book=assemble.Book(notes=[note],figures=[figure])
    chapter=build_epub.Chapter(index=1,title='test',blocks=['<figure><img src="images/fig_p0000_0.jpg"/></figure>'])
    images,missing,blanks=build_epub._figure_images([chapter],doc,book)
    assert images=={} and missing==[] and blanks==['images/fig_p0000_0.jpg']
    assert book.notes==[note] and note.text.startswith('A real footnote')
    doc.close()


def test_successful_chosen_recovery_geometry_is_used_after_empty_native_assessment(monkeypatch):
    from dataclasses import replace
    from cps.services.reflow import pipeline
    from tests.fixtures import reflow_pdfs as F
    master=F.new_doc();F.scan_chart_band_page(master,F.art_png(F.CHART_BAND_ART))
    pixels=master[0].get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False)
    doc=pymupdf.open();page=doc.new_page(width=master[0].rect.width,height=master[0].rect.height)
    page.insert_image(page.rect,stream=pixels.tobytes('png'))
    chosen=replace(extract.read_page(master,0),images=extract.read_page(doc,0).images)
    provenance=source.PageRecovery(pno=0,layer='ocr',words=100,source_rotation=0,
        page_rect=tuple(page.rect),derotation=tuple(page.derotation_matrix))
    chosen=source.normalize_recovery_geometry(chosen,doc,asdict(provenance))
    monkeypatch.setattr(source,'recover',lambda *a,**kw:source.Recovery(pages=[chosen],provenance={0:provenance}))
    result=pipeline.run(doc)
    assert not result.assessment.layer_is_trusted, 'original native page has no text'
    assert result.book.figures and all(f['bbox'][3]<page.rect.height*.8 for f in result.book.figures)
    assert 'Figure 7.4' in result.page_html[0]
    doc.close();master.close()


def test_source_geometry_loop_stops_before_another_page_after_cancellation(monkeypatch):
    from cps.services.reflow import pipeline,skeleton
    from tests.fixtures import reflow_pdfs as F
    doc=F.new_doc()
    for _ in range(3):F.prose_page(doc)
    measured=[];original=skeleton.page_skeleton
    def measure(raw,*a,**kw):
        measured.append(raw.pno)
        return original(raw,*a,**kw)
    monkeypatch.setattr(skeleton,'page_skeleton',measure)
    with pytest.raises(build_epub.BuildCancelled):
        pipeline.run(doc,recovery_opts={'mode':'off'},should_stop=lambda:bool(measured))
    assert measured==[0]
    doc.close()


def test_artwork_record_cannot_consume_equal_token_in_surviving_body_or_caption():
    from cps.services.reflow.enriched_source import prepare_source_page
    figure={'pno':0,'bbox':(0,0,100,100),'source_geometry':{'space':'reading',
        'orientation':0,'source_rotation':0,'page_rect':[0,0,200,300]}}
    elements=[assemble.Element('fig',pno=0,bbox=figure['bbox']),
        assemble.Element('caption',pno=0,bbox=(10,110,100,130),runs=[['t','6 caption']],caption_uncertain=True),
        assemble.Element('p',pno=0,bbox=(10,200,150,220),runs=[['t','6 units remain in body']])]
    book=assemble.Book(elements=elements,pages={0:elements},figures=[figure],
        artwork=[{'pno':0,'bbox':(10,10,50,50),'text':'6'}])
    records=[{'token':'6','score':3,'source_bbox':[20,20,30,30]},
             {'token':'6','score':70,'source_bbox':[20,200,30,210]},
             {'token':'6','score':9,'source_bbox':[20,110,30,120]}]
    page=prepare_source_page(book,0,{'layer':'ocr','orientation':0,'uncertain_words':3},records)
    report=page.report()
    assert report['placed_record_indices']==[1], 'artwork confidence was moved to body'
    assert report['artwork_record_indices']==[0]
    assert report['qualified_caption_record_indices']==[2]
    assert report['raw_records']==3 and report['unplaced_record_indices']==[0,2]
    assert 'Caption transcription uncertain' in page.html
    assert '6</span> units remain in body' in page.html


@pytest.mark.parametrize('rotation,orientation',[(0,0),(90,0),(0,90),(90,270)])
def test_isolated_query_retains_pixels_and_closes_only_its_owned_page(rotation,orientation):
    doc=pymupdf.open();doc.new_page(width=400,height=600)
    page=doc.new_page(width=400,height=600)
    page.draw_rect((40,60,170,220),color=(1,0,0),fill=(1,0,0));page.set_rotation(rotation)
    display=SourceDisplay(doc,1,{'layer':'ocr','orientation':orientation,'source_rotation':rotation})
    baseline=display.pixmap(scale=1)
    with display.query_document(isolate=True) as query:
        actual=query[1].get_pixmap(matrix=pymupdf.Matrix(1,1))
        assert (actual.width,actual.height,actual.samples)==(baseline.width,baseline.height,baseline.samples)
    with pytest.raises((ValueError,RuntimeError,AssertionError)):
        query[1].get_pixmap(matrix=pymupdf.Matrix(1,1))
    assert not doc.is_closed and display.pixmap(scale=1).samples==baseline.samples
    query.close();doc.close()


@pytest.mark.parametrize('failure',[build_epub.BuildCancelled,ValueError])
def test_pipeline_closes_owned_probe_on_cancel_or_geometry_error(monkeypatch,failure):
    from cps.services.reflow import pipeline,skeleton
    from tests.fixtures import reflow_pdfs as F
    doc=F.new_doc();F.prose_page(doc)
    prov=source.PageRecovery(pno=0,layer='ocr',words=100,page_rect=tuple(doc[0].rect))
    chosen=source.normalize_recovery_geometry(extract.read_page(doc,0),doc,asdict(prov))
    monkeypatch.setattr(source,'recover',lambda *a,**kw:source.Recovery(pages=[chosen],provenance={0:prov}))
    closed=[];original=SourceDisplay.query_document
    def query(self,*a,**kw):
        value=original(self,*a,**kw);close=value.close
        def record_close():closed.append(True);close()
        value.close=record_close
        return value
    monkeypatch.setattr(SourceDisplay,'query_document',query)
    def fail(*a,**kw):raise failure('stop during source geometry')
    monkeypatch.setattr(skeleton,'page_skeleton',fail)
    with pytest.raises(failure):pipeline.run(doc)
    assert closed==[True] and not doc.is_closed
    doc.close()


def test_failed_query_copy_closes_new_document(monkeypatch):
    doc=pymupdf.open();doc.new_page(width=100,height=200)
    display=SourceDisplay(doc,0);doc.close()
    opened=[];original=pymupdf.open
    def capture(*a,**kw):
        value=original(*a,**kw);opened.append(value);return value
    monkeypatch.setattr(pymupdf,'open',capture)
    with pytest.raises(ValueError):display.query_document(isolate=True)
    assert len(opened)==1 and opened[0].is_closed
