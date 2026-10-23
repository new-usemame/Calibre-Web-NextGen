"""Synthetic mixed-font print with two opaque codes and independently mapped prose."""
import re

import pymupdf
import pytest

from cps.services.reflow import native_text, pixel_adjudication, vision_pipeline
from cps.services.reflow.source_display import SourceDisplay
from tests.unit.test_reflow_vision_pilot import answer, block

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('borrowed_tail', [False, True])
def test_placed_opaque_script_ink_cannot_discard_proved_latin_neighbors(tmp_path, monkeypatch, borrowed_tail):
    class Package:
        def disclosure_image(self, *args):
            pass

    monkeypatch.setattr(pixel_adjudication, 'read_crop', lambda *args: [])
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=250)
        page.insert_text((30, 70), 'The quiet', fontsize=12)
        page.insert_text((90, 70), 'x z', fontname='cour', fontsize=12)
        page.insert_text((130, 70), 'remains readable.', fontsize=12)
        xref = next(font[0] for font in page.get_fonts() if font[3] == 'Courier')
        doc.xref_set_key(xref, 'BaseFont', '/CustomMarks')
        page = doc.reload_page(page)
        source = [dict(text=w[4], bbox=list(w[:4]), confidence=99) for w in page.get_text('words')]
        assert native_text.drawn_word_authorities(page, source) == [True, True, False, False, True, True]
        reading = 'The quiet αλφαβήτα γαμμαδέλτα remains readable.'
        if borrowed_tail:
            reading += ' An extra unowned sentence follows beyond this printed region.'
        body, record = vision_pipeline.compile_page(
            answer(block(reading, bbox=(0, 0, 1000, 1000))), source,
            SourceDisplay(doc, 0), 0, Package(), {}, pixel_words=[],
            note_context={'cache': tmp_path/'cache', 'labels': {}, 'catalogue': {}, 'refs': {}})
    own = record['blocks'][0]
    assert own['published_tokens'] == 'The quiet x z remains readable.'.split()
    assert own['source_image_words'] == [2, 3]
    assert own['marked'] == [2, 3]
    html = ''.join(body['body'])
    assert html.count('class="source-word-image"') == 2
    assert vision_pipeline._seam_text(re.sub(r'<a[^>]*class="source-disclosure-ref"[^>]*>.*?</a>', '', html)) == 'The quiet remains readable.'
    assert 'αλφαβήτα' not in html and 'unowned' not in html
