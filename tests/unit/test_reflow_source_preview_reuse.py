"""Procedural gray pages require one owned complete preview and distinct controls."""
import hashlib
import io
import random
import zipfile
from xml.etree import ElementTree as ET

import pymupdf
import pytest
from PIL import Image

from cps.services.reflow import image_budget, unit_recovery, unit_source
from cps.services.reflow.source_display import SourceDisplay


def source(tmp_path):
    rng = random.Random(713)
    image = Image.frombytes('L', (240, 320),
        bytes(rng.randrange(180, 256) for _ in range(240 * 320)))
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    path = tmp_path / 'printed.pdf'
    doc = pymupdf.open()
    for label in ('First printed page.', 'Second printed page.'):
        page = doc.new_page(width=240, height=320)
        page.insert_image(page.rect, stream=stream.getvalue())
        page.insert_text((20, 50), label, fontsize=14)
    doc.save(path)
    doc.close()
    return pymupdf.open(path)


def test_uncertain_units_share_the_existing_complete_page_but_keep_own_controls(tmp_path):
    doc = source(tmp_path)
    package = unit_recovery.package(tmp_path / 'shared.epub')
    try:
        display = SourceDisplay(doc, 0)
        package.disclosure_image('images/complete.jpg', display, display.rect)
        original = package._zf.read('OEBPS/images/complete.jpg')
        records = {}
        units = [unit_source.source_unit(display, 0, name, 'body', package, records,
                    'Synthetic unit reading is unresolved.')
                 for name in ('first', 'second')]
        assert len(package.images) == 1, 'one physical page must not gain another full JPEG encoding'
        assert package._zf.read('OEBPS/images/complete.jpg') == original
        assert units[0][2]['source_reference'] != units[1][2]['source_reference']
        for fragment, asides, record in units:
            assert record['source_image'] == 'images/complete.jpg'
            assert record['source_bbox'] == list(display.rect)
            markup = ET.fromstring('<div xmlns:epub="http://www.idpf.org/2007/ops">'
                                  + fragment + ''.join(asides) + '</div>')
            control = next(node for node in markup.iter('a')
                           if node.get('id') == record['source_reference_origin'])
            aside = next(node for node in markup.iter('aside')
                         if node.get('id') == record['source_reference'])
            assert control.get('href') == '#' + aside.get('id')
            assert aside.find('img').get('src') == 'images/complete.jpg'
            assert any(node.get('href') == '#' + control.get('id') for node in aside.iter('a'))
    finally:
        package.abandon()
        doc.close()


def test_shared_complete_preview_passes_a_budget_that_repeated_encodings_exceed(tmp_path, monkeypatch):
    doc = source(tmp_path)
    path = tmp_path / 'bounded.epub'
    package = unit_recovery.package(path)
    try:
        display = SourceDisplay(doc, 0)
        package.disclosure_image('images/complete.jpg', display, display.rect)
        budget = package._zf.fp.tell() + 6000
        monkeypatch.setattr(image_budget, 'MIN_BOOK_BYTES', budget)
        fragment, asides, _ = unit_source.source_unit(display, 0, 'pending', 'body',
            package, {}, 'Synthetic unit reading is unresolved.')
        document = '<html xmlns:epub="http://www.idpf.org/2007/ops"><body>' + fragment + ''.join(asides) + '</body></html>'
        parts = dict(sidecar={}, opf='<package/>', nav='<nav/>', ncx='<ncx/>',
                     documents={'ch1.xhtml': document})
        package.finish(parts)
        assert path.stat().st_size <= budget
        assert image_budget.check(0, path.stat().st_size)['passed']
        with zipfile.ZipFile(path) as archive:
            assert [name for name in archive.namelist() if name.startswith('OEBPS/images/')] == [
                'OEBPS/images/complete.jpg']
    finally:
        package.abandon()
        doc.close()


@pytest.mark.parametrize('difference', ['document', 'page', 'rotation', 'rectangle', 'provenance'])
def test_existing_preview_cannot_be_borrowed_from_a_different_source_space(tmp_path, difference):
    doc = source(tmp_path)
    other = None
    raster = image_budget.EvidenceRaster()
    try:
        display = SourceDisplay(doc, 0)
        full = raster.jpeg(display, display.rect)
        assert raster.cached_preview(display) == full
        if difference == 'document':
            other = pymupdf.open(doc.name)
            mismatched = SourceDisplay(other, 0)
        elif difference == 'page':
            mismatched = SourceDisplay(doc, 1)
        elif difference == 'rotation':
            mismatched = SourceDisplay(doc, 0, dict(layer='ocr', orientation=90))
        elif difference == 'rectangle':
            mismatched = SourceDisplay(doc, 0)
            mismatched.rect = pymupdf.Rect(0, 0, 239, 320)
        else:
            mismatched = SourceDisplay(doc, 0, dict(local_ocr_detail_source='different proof'))
        assert raster.cached_preview(mismatched) is None
    finally:
        if other is not None:
            other.close()
        doc.close()


def test_existing_preview_rejects_an_in_memory_change_to_the_source_page(tmp_path):
    doc = source(tmp_path)
    raster = image_budget.EvidenceRaster()
    try:
        display = SourceDisplay(doc, 0)
        full = raster.jpeg(display, display.rect)
        assert raster.cached_preview(display) == full
        display.page.insert_text((20, 90), 'A new printed line changes the source.', fontsize=10)
        assert raster.cached_preview(display) is None
    finally:
        doc.close()


def test_detail_cache_is_not_a_complete_page_and_keeps_the_original_render_path(tmp_path, monkeypatch):
    doc = source(tmp_path)
    package = unit_recovery.package(tmp_path / 'detail.epub')
    try:
        display = SourceDisplay(doc, 0)
        package.disclosure_image('images/detail.jpg', display, pymupdf.Rect(10, 20, 140, 80))
        assert package._evidence_raster.cached_preview(display) is None
        calls = []
        original = display.roi_jpeg
        def render(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)
        monkeypatch.setattr(display, 'roi_jpeg', render)
        _, _, record = unit_source.source_unit(display, 0, 'pending', 'body', package,
            {}, 'Synthetic unit reading is unresolved.')
        assert len(calls) == 1
        assert calls[0][0] == (display.rect,)
        assert record['source_image'] != 'images/detail.jpg'
        assert len(package.images) == 2
    finally:
        package.abandon()
        doc.close()


def test_cached_complete_page_revalidates_its_original_frame_before_reuse(tmp_path, monkeypatch):
    doc = source(tmp_path)
    original_path = tmp_path / 'original.pdf'
    original_path.write_bytes(open(doc.name, 'rb').read())
    digest = hashlib.sha256(original_path.read_bytes()).hexdigest()
    display = SourceDisplay(doc, 0, dict(original_frame=dict(
        original_pdf=str(original_path), original_pdf_sha256=digest,
        prepared_pdf_sha256=digest, original_page_index0=0, prepared_page_index0=0)))
    raster = image_budget.EvidenceRaster()
    try:
        full = raster.jpeg(display, display.rect)
        assert raster.cached_preview(display) == full
        original_path.write_bytes(original_path.read_bytes() + b'\n% changed source\n')
        def no_render(*args, **kwargs):
            pytest.fail('a cache lookup must validate source identity before any raster allocation')
        monkeypatch.setattr(display, 'roi_pixmap', no_render)
        with pytest.raises(unit_recovery.SourceIntegrityError):
            raster.cached_preview(display)
    finally:
        doc.close()
