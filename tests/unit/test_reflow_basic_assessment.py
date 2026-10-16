"""Opening the assessment never performs conversion, OCR, or a whole-book census."""
import pytest
from tests.fixtures import reflow_pdfs as F
from cps.services.reflow import extract,pipeline,source
from tests.unit.test_reflow_api import mod
pytestmark=pytest.mark.unit


def test_actual_basic_survey_never_starts_ocr_or_conversion(mod,monkeypatch,tmp_path):
    doc=F.new_doc();page=doc.new_page();page.insert_image(page.rect,stream=F.solid_png());path=tmp_path/'scan.pdf';doc.save(path);doc.close()
    def forbidden(*args,**kwargs):raise AssertionError('basic assessment started heavy conversion/OCR/census')
    monkeypatch.setattr(source,'recover',forbidden)
    monkeypatch.setattr(pipeline,'run',forbidden)
    monkeypatch.setattr(extract,'read_pages',forbidden)
    quote=mod._survey_uncached(str(path))
    assert quote['verdict']=='NO_TEXT_LAYER'
    assert quote['ocr_image_only']==1 and quote['assessment_scope']=='complete'
    assert quote['ocr_estimated_seconds'] is None


def test_large_book_reads_at_most_the_bounded_sample_and_labels_projection(monkeypatch):
    from cps.services.reflow import source_assessment as basic
    import pymupdf
    class Page:
        rect=pymupdf.Rect(0,0,500,700)
        def get_text(self,kind,flags):
            assert kind=='text' and not flags & pymupdf.TEXT_PRESERVE_IMAGES
            return ''
        def get_image_info(self,*,hashes,xrefs):
            assert hashes is False and xrefs is False
            return [{'bbox':tuple(self.rect)}]
    class Book:
        page_count=4207
        def __init__(self):self.read=[]
        def __getitem__(self,pno):self.read.append(pno);return Page()
    book=Book();quote=basic.survey(book)
    assert len(book.read)==len(set(book.read))<=40
    assert book.read[0]==0 and book.read[-1]==4206
    assert quote['sampled']==len(book.read) and quote['assessment_scope']=='sample'
    assert quote['counts_estimated'] is True and quote['ocr_image_only']==4207
    assert quote['ocr_estimated_seconds'] is None
    assert 'eligible_pages' not in quote and 'full_bound_usd' not in quote


def test_basic_cache_does_not_reuse_old_ocr_conversion_survey(mod,monkeypatch,tmp_path):
    import hashlib,os
    from cps.services.reflow import build_epub,model
    path=tmp_path/'source.pdf';path.write_bytes(b'identity only')
    old='\0'.join((os.path.abspath(path),build_epub.CONVERTER_VERSION,model.PRICE_TABLE_MEASURED))
    assert not mod._cache_key(str(path)).startswith(hashlib.sha1(old.encode()).hexdigest()[:16]+'-')
