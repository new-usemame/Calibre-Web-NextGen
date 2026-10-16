"""Synthetic original-frame provenance controls; no source excerpts or network."""
import copy
import hashlib
from pathlib import Path

import pymupdf
import pytest

from cps.services.reflow import vision_pipeline, vision_pixels
from cps.services.reflow.source_display import SourceDisplay


sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
ROI = [31, 28, 48, 43]


@pytest.fixture
def frames(tmp_path):
    original = tmp_path / 'original.pdf'
    prepared = tmp_path / 'prepared.pdf'
    authored = pymupdf.open()
    for index in range(2):
        p = authored.new_page(width=120, height=160)
        p.insert_text((15, 65), 'Plain synthetic source words stay live.', fontsize=7)
        # Subpixel vector detail is lost by the frozen two-times raster.
        p.draw_rect([34.2, 31.1, 35.1, 39.9], fill=(0, 0, 0), color=None)
        p.draw_line((37.13, 32.07), (41.37, 38.91), color=(0, 0, 0), width=.19)
        p.draw_circle((43.27, 35.91), 1.83 + index, color=(0, 0, 0), width=.18)
    authored.save(original)
    authored.close()
    raster = pymupdf.open()
    words = []
    with pymupdf.open(original) as source:
        for p in source:
            words.append([{'text': w[4], 'bbox': list(w[:4]), 'confidence': 99}
                          for w in p.get_text('words', sort=True)])
            image = p.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes('png')
            out = raster.new_page(width=p.rect.width, height=p.rect.height)
            out.insert_image(out.rect, stream=image)
    raster.save(prepared)
    raster.close()
    config = dict(original_pdf=str(original), original_pdf_sha256=sha(original),
                  prepared_pdf_sha256=sha(prepared), prepared_page_index0=0,
                  original_page_index0=0)
    return original, prepared, config, words


def display_for(prepared, config, pno=0):
    doc = pymupdf.open(prepared)
    try:
        display = SourceDisplay(doc, pno, {'original_frame': copy.deepcopy(config)})
    except BaseException:
        doc.close()
        raise
    return doc, display


def detail(display, box=ROI, scale=12):
    # Legacy fallback establishes actual detail-loss RED, not a missing-method RED.
    return getattr(display, 'roi_jpeg', display.jpeg)(box, scale=scale, quality=95)


def test_original_detail_pixels_preserved_without_changing_canonical_page_jpeg(frames):
    original, prepared, config, _ = frames
    with pymupdf.open(prepared) as canonical, pymupdf.open(original) as source:
        expected_page = SourceDisplay(canonical, 0).pixmap(scale=2, max_pixels=2_500_000).tobytes('jpg', jpg_quality=90)
        expected_detail = SourceDisplay(source, 0).jpeg(ROI, scale=12, quality=95)
        lost_detail = SourceDisplay(canonical, 0).jpeg(ROI, scale=12, quality=95)
    assert expected_detail != lost_detail, 'fixture must contain genuine preparation detail loss'
    doc, display = display_for(prepared, config)
    try:
        actual = detail(display)
        assert actual == expected_detail, 'bounded detail must contain original pixels at original resolution'
        assert display.pixmap(scale=2, max_pixels=2_500_000).tobytes('jpg', jpg_quality=90) == expected_page
        assert display.jpeg(ROI, scale=12, quality=95) == lost_detail, 'ordinary jpeg route retains frozen prepared pixels'
    finally:
        doc.close()


@pytest.mark.parametrize('mutation', ['original_hash', 'prepared_hash', 'bool_original',
                                     'fractional_original', 'wrong_prepared_page', 'out_of_range', 'wrong_original_page'])
def test_bad_frame_identity_rejected_before_detail_pixels(frames, mutation):
    original, prepared, config, _ = frames
    if mutation == 'original_hash': config['original_pdf_sha256'] = '0' * 64
    elif mutation == 'prepared_hash': config['prepared_pdf_sha256'] = '0' * 64
    elif mutation == 'bool_original': config['original_page_index0'] = True
    elif mutation == 'fractional_original': config['original_page_index0'] = .5
    elif mutation == 'wrong_prepared_page': config['prepared_page_index0'] = 1
    elif mutation == 'out_of_range': config['original_page_index0'] = 12
    elif mutation == 'wrong_original_page': config['original_page_index0'] = 1
    doc = None
    try:
        with pytest.raises(ValueError):
            doc, display = display_for(prepared, config)
            detail(display)
    finally:
        if doc is not None: doc.close()


@pytest.mark.parametrize('fault', ['geometry', 'rotation', 'orientation'])
def test_incompatible_frame_coordinates_rejected(frames, fault):
    original, prepared, config, _ = frames
    if fault != 'orientation':
        with pymupdf.open(original) as doc:
            if fault == 'geometry': doc[0].set_cropbox(pymupdf.Rect(0, 0, 110, 150))
            else: doc[0].set_rotation(180)
            altered = original.with_name('altered.pdf'); doc.save(altered)
        config['original_pdf'] = str(altered); config['original_pdf_sha256'] = sha(altered)
    doc = pymupdf.open(prepared)
    try:
        provenance = {'original_frame': config}
        if fault == 'orientation': provenance.update(layer='ocr', orientation=90)
        with pytest.raises(ValueError):
            detail(SourceDisplay(doc, 0, provenance))
    finally:
        doc.close()


def test_original_file_revalidated_after_display_creation(frames):
    original, prepared, config, _ = frames
    doc, display = display_for(prepared, config)
    try:
        original.write_bytes(original.read_bytes() + b'\n%changed-source-file\n')
        with pytest.raises(ValueError): detail(display)
    finally:
        doc.close()


def test_detail_render_never_uses_hidden_text_and_releases_original_document(frames, monkeypatch):
    original, prepared, config, _ = frames
    real_open = pymupdf.open; opened = []
    def tracked_open(*args, **kwargs):
        doc = real_open(*args, **kwargs)
        if args and str(args[0]) == str(original): opened.append(doc)
        return doc
    monkeypatch.setattr(pymupdf, 'open', tracked_open)
    monkeypatch.setattr(pymupdf.Page, 'get_text', lambda *a, **k: (_ for _ in ()).throw(AssertionError('frame rendering cannot read hidden OCR')))
    doc, display = display_for(prepared, config)
    try:
        detail(display)
        assert opened, 'detail must be rendered from bound original frame'
        assert all(d.is_closed for d in opened), 'all source documents close after the bounded detail query'
    finally:
        doc.close()


def test_default_and_native_source_display_routes_remain_unchanged(frames):
    original, prepared, _, _ = frames
    for path in [original, prepared]:
        with pymupdf.open(path) as doc:
            display = SourceDisplay(doc, 0)
            before = display.jpeg(ROI, scale=12, quality=95)
            assert detail(display) == before
            assert display.pixmap(scale=2).samples == doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).samples


def test_convert_keeps_exact_prepared_page_reader_images_with_optional_frames(frames, tmp_path, monkeypatch):
    original, prepared, config, words = frames
    with pymupdf.open(prepared) as doc:
        expected = [SourceDisplay(doc, n).pixmap(scale=2, max_pixels=2_500_000).tobytes('jpg', jpg_quality=90) for n in range(2)]
    monkeypatch.setattr(vision_pixels, 'words', lambda display, cache: copy.deepcopy(words[display.pno]))
    class Reader:
        cache = tmp_path / 'cache' / 'reading'
        images = []
        def read(self, image):
            self.images.append(image)
            return {'cached': True, 'new_cost_usd': 0, 'answer': {'blocks': [dict(id='body', role='body', text='Plain synthetic source words stay live.', bbox=[110,350,960,440], marker='', low_confidence=[], continues_previous=False, continues_next=False)]}}
    reader = Reader()
    mappings = {0: config, 1: dict(config, prepared_page_index0=1, original_page_index0=1)}
    with pymupdf.open(prepared) as doc:
        vision_pipeline.convert(doc, str(tmp_path / 'synthetic.epub'), reader, original_frames=mappings)
    assert reader.images == expected, 'optional source detail lineage cannot cause new ordinary page-reading identities'


@pytest.mark.parametrize('box', [[-4, 28, 48, 43], [31, float('nan'), 48, 43]])
def test_invalid_detail_bbox_cannot_silently_clip_original_frame(frames, box):
    _, prepared, config, _ = frames
    doc, display = display_for(prepared, config)
    try:
        with pytest.raises(ValueError): detail(display, box)
    finally:
        doc.close()
