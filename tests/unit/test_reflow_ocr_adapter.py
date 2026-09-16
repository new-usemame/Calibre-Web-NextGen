# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral contracts for local recognition, source geometry and containment."""
import hashlib
import json
import os
import signal
import time
import shutil
import sys

import pymupdf
import pytest

from cps.services.reflow import ocr
from tests.unit.test_reflow_ocr_source_contract import _scan, LEFT, RIGHT

pytestmark = pytest.mark.unit
real_ocr = pytest.mark.skipif(not shutil.which('tesseract'), reason='needs installed OCR engine')


@real_ocr
@pytest.mark.parametrize('angle', [0, 90, 180, 270])
def test_real_scan_orientation_order_and_original_source_coordinates(angle):
    with _scan(spread=True, clockwise=angle) as document:
        digest = hashlib.sha256(document.tobytes()).hexdigest()
        result = ocr.recognize_page(document[0], source_sha256=digest)
        text = ' '.join(word.text for word in result.words)
        anchors = [LEFT[0], LEFT[-1], RIGHT[0], RIGHT[-1]]
        assert all(anchor in text for anchor in anchors), text
        assert [text.index(anchor) for anchor in anchors] == sorted(text.index(anchor) for anchor in anchors)
        assert result.orientation_clockwise == (-angle) % 360
        assert result.source_sha256 == digest
        # First recognized line was drawn at upright y=90. Its mapped original
        # pixels must return to that region, including each right-angle rotation.
        word = result.words[0]
        assert 35 < word.bbox[0] < 50 and 65 < word.bbox[1] < 95
        source = pymupdf.Rect(word.source_bbox)
        assert source.width > 0 and source.height > 0
        assert source in document[0].rect
        if angle == 0:
            assert source.x0 == pytest.approx(word.bbox[0])
        elif angle == 90:
            assert source.x0 > 600 and source.y0 < 50
        elif angle == 180:
            assert source.x0 > 900 and source.y0 > 600
        else:
            assert source.x0 < 95 and source.y0 > 900


@real_ocr
@pytest.mark.parametrize('corruption', ['confidence', 'source_identity', 'text', 'geometry', 'pdf_transform'])
def test_atomic_cache_roundtrip_and_corrupt_cache_miss(tmp_path, monkeypatch, corruption):
    with _scan() as document:
        digest = hashlib.sha256(document.tobytes()).hexdigest()
        first = ocr.recognize_page(document[0], source_sha256=digest, cache_dir=tmp_path)
        invoke = ocr._invoke
        def forbidden(*args, **kwargs):
            raise AssertionError('accepted cache should avoid recognition')
        monkeypatch.setattr(ocr, '_invoke', forbidden)
        assert ocr.recognize_page(document[0], source_sha256=digest, cache_dir=tmp_path) == first
        cache = next(tmp_path.glob('*.json'))
        payload = json.loads(cache.read_text())
        if corruption == 'confidence':
            payload['result']['words'][0]['confidence'] = 'invalid'
        elif corruption == 'source_identity':
            payload['result']['source_sha256'] = 'f' * 64
        elif corruption == 'text':
            payload['result']['words'][0]['text'] = 'CORRUPTED'
        elif corruption == 'geometry':
            payload['result']['words'][0]['source_bbox'] = [-10, -10, 900, 900]
        else:
            box = payload['result']['words'][0]['pdf_bbox']
            payload['result']['words'][0]['pdf_bbox'] = [x + 10 for x in box]
        if corruption != 'text':
            # A valid digest cannot excuse invalid geometry or provenance.
            canonical = json.dumps(payload['result'], sort_keys=True,
                                   separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
            payload['digest'] = hashlib.sha256(canonical).hexdigest()
        cache.write_text(json.dumps(payload))
        monkeypatch.setattr(ocr, '_invoke', invoke)
        assert ocr.recognize_page(document[0], source_sha256=digest, cache_dir=tmp_path) == first
        assert not list(tmp_path.glob('*.tmp'))


def test_missing_engine_and_unavailable_language_are_actionable(monkeypatch):
    monkeypatch.setattr(ocr.shutil, 'which', lambda name: None)
    with pytest.raises(ocr.OCRUnavailable, match='Tesseract'):
        ocr._engine('eng')


@real_ocr
def test_rejects_uninstalled_or_injected_language():
    for language in ['not_installed_language', 'eng;echo', '', 'osd']:
        with pytest.raises(ocr.OCRUnavailable):
            ocr._engine(language)


def test_cancel_before_render_does_not_create_cache(tmp_path):
    with _scan() as document:
        with pytest.raises(ocr.OCRCancelled):
            ocr.recognize_page(document[0], source_sha256='a' * 64,
                               cache_dir=tmp_path, should_stop=lambda: True)
    assert list(tmp_path.iterdir()) == []


def test_engine_timeout_cancellation_and_output_limits(tmp_path, monkeypatch):
    with pytest.raises(ocr.OCRFailed, match='time limit'):
        ocr._invoke([sys.executable, '-c', 'import time; time.sleep(10)'], tmp_path,
                    timeout=.15, should_stop=None)
    calls = iter([False, True])
    with pytest.raises(ocr.OCRCancelled):
        ocr._invoke([sys.executable, '-c', 'import time; time.sleep(10)'], tmp_path,
                    timeout=10, should_stop=lambda: next(calls))
    monkeypatch.setattr(ocr, 'MAX_OUTPUT_BYTES', 1024)
    with pytest.raises(ocr.OCRFailed, match='output limit'):
        ocr._invoke([sys.executable, '-c', 'print("x" * 2048)'], tmp_path,
                    timeout=10, should_stop=None)


@real_ocr
def test_blank_page_is_explicit_and_raster_is_bounded_before_allocation(monkeypatch):
    allocations = []
    original = pymupdf.Page.get_pixmap
    def tracked(page, **kwargs):
        matrix = kwargs['matrix']
        target = (page.rect * matrix).irect
        assert target.width * target.height <= 100_000
        allocations.append((target.width, target.height))
        return original(page, **kwargs)
    monkeypatch.setattr(pymupdf.Page, 'get_pixmap', tracked)
    with pymupdf.open() as document:
        page = document.new_page(width=10000, height=10000)
        result = ocr.recognize_page(page, source_sha256='b' * 64, max_pixels=100_000)
    assert allocations and not result.words
    assert 'resolution_limited' in result.flags and 'no_text_detected' in result.flags


@real_ocr
@pytest.mark.parametrize('metadata_rotation', [90, 270])
def test_rotated_cropped_pdf_maps_back_to_unrotated_page_coordinates(metadata_rotation):
    with _scan() as document:
        page = document[0]
        page.set_cropbox(pymupdf.Rect(20, 30, 480, 600))
        page.set_rotation(metadata_rotation)
        result = ocr.recognize_page(page, source_sha256=hashlib.sha256(document.tobytes()).hexdigest())
        assert ' '.join(word.text for word in result.words).startswith(LEFT[0])
        first = result.words[0]
        # The printed first glyph began at x40, baseline90 before the crop.
        # Unrotated page coordinates are relative to the new (20,30) crop.
        assert 15 < first.pdf_bbox[0] < 30
        assert 30 < first.pdf_bbox[1] < 65
        assert result.source_rotation == metadata_rotation


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process containment contract')
@pytest.mark.parametrize('finish', ['timeout', 'cancel', 'normal'])
def test_engine_descendants_stop_with_the_owned_invocation(tmp_path, finish):
    heartbeat = tmp_path / 'heartbeat'
    pidfile = tmp_path / 'child.pid'
    child_code = ("import sys,time; f=open(sys.argv[1], 'wb', buffering=0); "
                  "exec('while True:\\n f.write(b\"x\"); time.sleep(.02)')")
    parent_code = ("import subprocess,sys,time; from pathlib import Path; "
                   f"child=subprocess.Popen([sys.executable,'-c',{child_code!r},sys.argv[1]]); "
                   "Path(sys.argv[2]).write_text(str(child.pid)); "
                   "time.sleep(.3 if sys.argv[3]=='normal' else 30)")
    stop = (lambda: heartbeat.exists()) if finish == 'cancel' else None
    try:
        args = [sys.executable, '-c', parent_code, str(heartbeat), str(pidfile), finish]
        if finish == 'normal':
            status, _, _ = ocr._invoke(args, tmp_path, timeout=2, should_stop=stop)
            assert status == 0
        else:
            expected = ocr.OCRCancelled if finish == 'cancel' else ocr.OCRFailed
            with pytest.raises(expected):
                ocr._invoke(args, tmp_path, timeout=.7, should_stop=stop)
        assert heartbeat.exists(), 'the descendant must have actually started'
        size = heartbeat.stat().st_size
        time.sleep(.2)
        assert heartbeat.stat().st_size == size, 'descendant continued after invocation ended'
    finally:
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@real_ocr
def test_equal_size_crop_at_a_new_origin_cannot_reuse_old_source_words(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scratch").mkdir()
    with _scan() as document:
        source_digest = hashlib.sha256(document.tobytes()).hexdigest()
        page = document[0]
        page.set_cropbox(pymupdf.Rect(0, 0, 400, 500))
        first = ocr.recognize_page(page, source_sha256=source_digest, cache_dir=tmp_path, scratch_dir="scratch")
        assert LEFT[0] in ' '.join(word.text for word in first.words)
        page.set_cropbox(pymupdf.Rect(50, 100, 450, 600))
        second = ocr.recognize_page(page, source_sha256=source_digest, cache_dir=tmp_path, scratch_dir="scratch")
        assert (first.width, first.height) == (second.width, second.height)
        # The first printed line is outside the new crop; cached old words would
        # falsely put it back into this logically different source region.
        assert LEFT[0] not in ' '.join(word.text for word in second.words)
        assert len(list(tmp_path.glob('*.json'))) == 2
