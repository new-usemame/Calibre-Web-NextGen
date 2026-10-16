"""SOURCE-free public unit recovery controls, frozen after9005 source seal.
Strict primitives remain strict; public convert retains complete original units.
"""
import copy
import hashlib
import importlib.util
import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf
import pytest
from PIL import Image
from cps.services.reflow import build_epub, vision_pipeline as vp, vision_pixels, vision_reading as vr
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.source_display import SourceDisplay

_spec = importlib.util.spec_from_file_location('r7_ink_helpers', Path(vp.__file__).parents[3] / 'tests/unit/test_round4_defects.py')
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)

BEFORE = 'Before replacement keeps the copper lantern visible.'
FAILED = 'Middle disputed source retains the silver compass.'
AFTER = 'After replacement keeps the violet orchard visible.'


@pytest.mark.parametrize('failure', ['schema', 'cap', 'unknown_bill'])
def test_one_bad_read_keeps_source_and_later_live_page(tmp_path, monkeypatch, failure):
    pages = []
    for index, text in enumerate((BEFORE, FAILED, AFTER)):
        page = h.InkPage()
        page.text(text, 'body', y=55)
        pages.append(page.finish())
    doc = pymupdf.open()
    for image, _, _ in pages:
        page = doc.new_page(width=h.W, height=h.H)
        page.insert_image(page.rect, stream=image)
    monkeypatch.setattr(vision_pixels, 'words', lambda display, cache: copy.deepcopy(pages[display.pno][1]))
    images = {hashlib.sha256(SourceDisplay(doc, index).pixmap(scale=2, max_pixels=2_500_000).tobytes('jpg', jpg_quality=90)).hexdigest(): index for index in range(3)}
    ledger = Ledger(tmp_path / 'ledger.jsonl', cap_usd=0 if failure == 'cap' else .02)
    reached = []

    class Reader:
        cache = tmp_path / 'readings'
        reread_calls = 0
        calls = []

        def read(self, image):
            page = images[hashlib.sha256(image).hexdigest()]
            self.calls.append(page)
            if page == 1:
                if failure == 'schema':
                    reached.append(('vision_reading.validate', 66, page))
                    vr.validate({'blocks': 'malformed isolated page'})
                if failure == 'cap':
                    reached.append(('ledger.Ledger.reserve_attempt', 126, page))
                    ledger.reserve_attempt('synthetic-cap-page', .001)
                reached.append(('synthetic_postdispatch_unknown_billing', page))
                if not ledger.pending_usd():
                    ledger.reserve_attempt('synthetic-uncertain-page', .001)
                raise vr.model.UncertainBilling('synthetic dispatched response has unresolved bill')
            return {'answer': copy.deepcopy(pages[page][2]), 'cached': True, 'new_cost_usd': 0}

        def reread(self, image):
            self.reread_calls += 1
            assert failure == 'schema', 'financial gate must prevent targeted reread dispatch'
            assert images[hashlib.sha256(image).hexdigest()] == 1
            return self.read(image)

    reader = Reader()
    reader.ledger = ledger
    out = tmp_path / 'isolated-source-recovery.epub'
    try:
        report = vp.convert(doc, str(out), reader)
    finally:
        doc.close()
    assert reached, 'the actual selected strict abortsite must be reached'
    assert build_epub.validate(out) == []
    assert set(map(int, report['page_homes'])) == {0, 1, 2}
    units = [unit for unit in report.get('degraded_units', []) if unit['page'] == 1]
    assert units and all(unit.get('stage') and unit.get('reason') for unit in units)
    assert not any(unit['page'] != 1 for unit in report['degraded_units']), 'healthy pages must stay live'
    with zipfile.ZipFile(out) as archive:
        docs = {name: ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith('.xhtml') and not name.endswith(('nav.xhtml', 'vision-about.xhtml'))}
        body = h.body_texts(docs)
        assert body.count(BEFORE) == 1 and body.count(AFTER) == 1, 'healthy source text survives exactly once'
        for unit in units:
            member = unit['source_image']
            member = member if member.startswith('OEBPS/') else 'OEBPS/' + member
            assert member in archive.namelist()
            pixels = Image.open(io.BytesIO(archive.read(member))).convert('L')
            assert pixels.getextrema()[0] < 100, 'SOURCE unit must contain original printed ink'
        source_links = [anchor for root in docs.values() for anchor in root.findall('.//x:a', h.NS) if 'source-disclosure-ref' in (anchor.get('class') or '').split()]
        assert source_links, 'degraded source reading must be visibly disclosed with a source control'
    assert reader.reread_calls <= 1, 'one shared physical-page targeted reread allowance'
    if failure in ('cap', 'unknown_bill'):
        assert reader.reread_calls == 0, 'blocked paid dispatch cannot be retried'
    assert ledger.spent() == 0
    assert ledger.pending_usd() == (.001 if failure == 'unknown_bill' else 0), 'unresolved liability cannot be erased'
