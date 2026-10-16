"""Synthetic raster controls for overlapping recognizer character boxes."""
from subprocess import CompletedProcess

import pymupdf
import pytest

from cps.services.reflow import vision_pixels
from cps.services.reflow.source_display import SourceDisplay
from tests.unit.test_round4_defects import InkPage


@pytest.mark.parametrize('adjacent_prose', [True, False])
def test_merged_digit_parenthesis_uses_original_ink_and_prose_baseline(
        tmp_path, monkeypatch, adjacent_prose):
    page = InkPage()
    page.reference(number=2048, raised=True)
    image, _, _ = page.finish()
    document = pymupdf.open()
    leaf = document.new_page(width=300, height=400)
    leaf.insert_image(leaf.rect, stream=image)
    # Coordinates describe this synthetic raster at six pixels per point.
    # The last digit and closing delimiter overlap in the recognizer's output;
    # their actual ink remains separate. Alphabetic boxes only locate prose.
    rows = ['T 6 108 39 147 0'] if adjacent_prose else []
    rows += ['( 405 96 417 147 0',
             '* 420 132 435 153 0',
             '" 438 132 468 153 0',
             '* 462 96 479 153 0',
             ') 471 96 501 153 0']
    characters = '\n'.join(rows) + '\n'
    reads = []

    def recognize(command, **kwargs):
        assert kwargs['input'].startswith(b'\x89PNG')
        reads.append(command)
        output = characters if command[-1] == 'makebox' else '2048\n'
        return CompletedProcess(command, 0, output.encode(), b'')

    monkeypatch.setattr(vision_pixels.shutil, 'which', lambda name: 'synthetic-ocr')
    monkeypatch.setattr(vision_pixels.subprocess, 'run', recognize)
    try:
        markers = vision_pixels.raised_markers(
            SourceDisplay(document, 0), [23, 41, 282, 73], tmp_path)
    finally:
        document.close()
    if adjacent_prose:
        assert len(markers) == 1
        assert markers[0]['label'] == '2048'
        assert markers[0]['bbox'] == pytest.approx([93, 47.5, 104, 51])
        assert len(reads) == 2
        assert 'tessedit_char_whitelist=0123456789' in reads[1]
    else:
        assert markers == [], 'delimiter descenders alone cannot prove raised ink'
        assert len(reads) == 1, 'do not read digits without independent role evidence'
