"""Synthetic scanned rows exercise leader classification at publication time."""
import pytest
import pymupdf

from cps.services.reflow import pixel_adjudication as pa, vision_pipeline as vp
from cps.services.reflow.source_display import SourceDisplay
from tests.unit.test_reflow_vision_pilot import answer, block

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('shape,width,height,removed', [
    ('leaders', 200.22, 1.67, True),
    ('leaders', 28.65, 11.33, True),
    ('leaders', 20.99, 11.33, True),
    ('leaders', 12.33, 11.33, True),
    ('leaders', 14.66, 11.33, True),
    ('leaders', 21.99, 11.33, True),
    ('letters', 28.65, 11.33, False),
    ('ellipsis', 28.65, 11.33, False),
    ('uneven', 28.65, 11.33, False),
    ('hollow', 28.65, 11.33, False),
    ('no_folio', 28.65, 11.33, False),
])
def test_compiler_measures_wide_and_padded_leader_ink(
        tmp_path, monkeypatch, shape, width, height, removed):
    """OCR box dimensions cannot substitute for connected source ink.

    The anchors are about 6.66pt tall. The narrow OCR fragments have 11.33pt
    rectangles, while the whole run is 200.22pt wide. An alphanumeric OCR
    guess over actual letters, ellipsis, or non-leader marks must survive.
    """
    folio = 'now' if shape == 'no_folio' else '12'
    x0 = 90.1 if width > 180 else 189.3
    box = [x0, 59 - height / 2, x0 + width, 59 + height / 2]
    with pymupdf.open() as drawn, pymupdf.open() as scan:
        pg = drawn.new_page(width=350, height=130)
        pg.insert_text((20 if width > 180 else 120, 60), 'Meadow', fontsize=4.85)
        pg.insert_text((310 if width > 180 else 280, 60), folio, fontsize=4.85)
        if shape == 'letters':
            pg.insert_text((190.3, 60), 'mew', fontsize=6)
        else:
            centers = ([91.3 + i * 4 for i in range(50)] if width > 180
                       else [151.3 + i * 4 for i in range(31)])
            if shape == 'ellipsis':
                centers = [191.3, 195.3, 199.3]
            elif shape == 'uneven':
                centers = [191.3, 194.3, 201.3, 207.3, 215.3]
            elif shape == 'hollow':
                centers = [191.3 + i * 4 for i in range(6)]
            for x in centers:
                pg.draw_circle((x, 59), .6, color=(0, 0, 0) if shape == 'hollow' else None,
                               fill=None if shape == 'hollow' else (0, 0, 0), width=.12)
        anchors = [dict(text=w[4], bbox=list(w[:4]), confidence=99)
                   for w in pg.get_text('words') if w[4] != 'mew']
        words = [anchors[0], dict(text='rnx', bbox=box, confidence=99), anchors[1]]
        assert 6.6 < anchors[0]['bbox'][3] - anchors[0]['bbox'][1] < 6.7
        copied = scan.new_page(width=350, height=130)
        copied.insert_image(copied.rect, pixmap=pg.get_pixmap(matrix=pymupdf.Matrix(4, 4)))
        monkeypatch.setattr(pa, 'read_crop', lambda *a, **k: pa.CropWitness(words))

        class Package:
            def image(self, *a):
                pass

        published, receipt = vp.compile_page(
            answer(block('Meadow rnx ' + folio, bbox=(0, 0, 1000, 1000))), words,
            SourceDisplay(scan, 0), 0, Package(), {}, pixel_words=words,
            regions={'b1': [[15, 45, 330, 75]]}, body_owners={},
            note_context={'cache': tmp_path / 'cache', 'labels': {}, 'catalogue': {}, 'refs': {}})
    record = receipt['blocks'][0]
    assert record['published_tokens'] == (['Meadow', folio] if removed else ['Meadow', 'rnx', folio])
    assert ('rnx' in ''.join(published['body'])) is not removed
    row = next(row for row in record['occurrence_coverage'] if row['source_text'] == 'rnx')
    if removed:
        assert record['decorative_source_regions'] == [box]
        assert row['disposition'] == 'excluded'
        assert row['rule'] == 'measured_contents_dot_leader'
    else:
        assert row['disposition'] != 'excluded'
        assert not record.get('decorative_source_regions')
