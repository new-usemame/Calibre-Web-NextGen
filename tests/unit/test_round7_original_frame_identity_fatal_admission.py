"""Authoritative original-frame identity is verified before any Reader dispatch."""
import hashlib,pymupdf,pytest
from cps.services.reflow import vision_pipeline as vp,source_display
from tests.unit.round7_recovery_test_support import fixture


def test_stale_original_frame_is_fatal_before_public_reading_dispatch(tmp_path,monkeypatch):
    document,reader,_=fixture(tmp_path,monkeypatch);prepared=tmp_path/'prepared.pdf';original=tmp_path/'original.pdf'
    document.save(prepared);document.close();original.write_bytes(prepared.read_bytes());doc=pymupdf.open(prepared)
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    frames={n:dict(original_pdf=str(original),original_pdf_sha256=digest(original),prepared_pdf_sha256=digest(prepared),original_page_index0=n,prepared_page_index0=n) for n in range(3)}
    frames[1]['original_pdf_sha256']='0'*64
    try:
        with pytest.raises(ValueError,match='Original source frame identity changed'):
            vp.convert(doc,str(tmp_path/'invalid-authority.epub'),reader,original_frames=frames)
    finally:doc.close()
    assert reader.calls==[],'Global authoritative source identity must reject before all model/Reader dispatch'
    assert not (tmp_path/'invalid-authority.epub').exists()


def test_original_frame_identity_error_retains_strict_valueerror_compatibility(tmp_path):
    path=tmp_path/'source.pdf';doc=pymupdf.open();page=doc.new_page(width=240,height=320);page.insert_text((20,60),'An original identity demonstration.');doc.save(path);doc.close();doc=pymupdf.open(path)
    frame=dict(original_pdf=str(path),original_pdf_sha256='0'*64,prepared_pdf_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),original_page_index0=0,prepared_page_index0=0)
    display=source_display.SourceDisplay(doc,0,{'original_frame':frame})
    try:
        with pytest.raises(ValueError,match='Original source frame identity changed'):display.roi_pixmap()
    finally:doc.close()
