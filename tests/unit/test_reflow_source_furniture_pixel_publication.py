"""The ordinary figure publishing path applies source furniture exclusions."""
import copy
import io
from PIL import Image
import pymupdf
import pytest
from cps.services.reflow import build_epub, crop_edges
from tests.unit.test_reflow_synthetic_furniture_ownership import inventory_book

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('found,expect_exclusion',[('scan_figure_band',True),('scan_figure_side',True),('source_visual_table',False),('ocr_uncertain_region',False),('unrecovered_scan_layer',False),('native_outline_conflict',False)])
def test_figure_publisher_binds_source_role_and_preserves_canonical_figures(monkeypatch,found,expect_exclusion):
    book,figure=inventory_book();figure['found']=found;figure['bbox']=[0,0,200,200]
    book.figures=[figure];before=copy.deepcopy(book.figures);inventory=copy.deepcopy(book.source_inventory)
    image=Image.new('RGB',(400,400),'white')
    from PIL import ImageDraw
    draw=ImageDraw.Draw(image);draw.rectangle((42,22,60,34),fill='black');draw.line((360,0,360,399),fill='black')
    stream=io.BytesIO();image.save(stream,format='PNG');original=stream.getvalue()
    monkeypatch.setattr(build_epub.extract,'crop_jpeg',lambda *a,**k:original)
    monkeypatch.setattr(build_epub,'_source_scan_edges',lambda *a:[])
    monkeypatch.setattr(crop_edges,'trim_scan_borders',lambda data:(data,[]))
    monkeypatch.setattr(build_epub,'_figure_crop_margin',lambda data,*a:data)
    class Sink(dict):
        def image(self,href,data):self[href]=data
    sink=Sink();presentation={};doc=pymupdf.open();doc.new_page(width=400,height=600)
    chapter=build_epub.Chapter(index=1,title='Diagram',blocks=['<figure><img src="images/fig_p0000_0.jpg"/></figure>'])
    assert build_epub._figure_images([chapter],doc,book,sink,figure_presentation=presentation)==([],[])
    result=Image.open(io.BytesIO(sink['images/fig_p0000_0.jpg'])).convert('RGB')
    assert result.getpixel((48,26))==((255,255,255) if expect_exclusion else (0,0,0))
    assert result.crop((310,0,400,400)).tobytes()==image.crop((310,0,400,400)).tobytes()
    assert book.figures==before and book.source_inventory==inventory
    assert presentation and original==stream.getvalue()
    doc.close()
