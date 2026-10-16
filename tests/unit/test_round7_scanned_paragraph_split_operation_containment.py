"""New hook containment frozen before implementation; no absent-hook credit."""
import copy
import importlib.util
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pymupdf
from cps.services.reflow import vision_pipeline as vp, vision_pixels, build_epub

s = importlib.util.spec_from_file_location('scanned_paragraph_control_fixture',
    Path(__file__).with_name('test_round7_scanned_source_paragraph_ownership.py'))
c = importlib.util.module_from_spec(s)
s.loader.exec_module(c)
h = c.h


def test_new_source_paragraph_split_operation_rejection_uses_one_reread_then_visible_source(tmp_path, monkeypatch):
    before = 'A healthy emerald paragraph preserves its independent original owner.'
    after = 'A healthy sapphire paragraph retains every ordinary printed word.'
    pages = []
    for text in [before, after]:
        p = h.InkPage()
        p.text(text, 'healthy', 'body', y=55)
        pages.append(p)
    damaged, _ = c.fixture()
    pages.insert(1, damaged)
    prepared = [p.finish() for p in pages]
    doc = pymupdf.open()
    for image, _, _ in prepared:
        page = doc.new_page(width=h.W, height=h.H)
        page.insert_image(page.rect, stream=image)
    monkeypatch.setattr(vision_pixels, 'words', lambda display, cache: copy.deepcopy(prepared[display.pno][1]))
    original = getattr(vp, '_source_paragraph_boundaries', None)
    reached = []

    def split_failure(*args, **kwargs):
        for value in [*args, *kwargs.values()]:
            if isinstance(value, dict) and isinstance(value.get('blocks'), list):
                if any(isinstance(block, dict) and block.get('id') == 'body0' for block in value['blocks']):
                    reached.append(1)
                    raise ValueError('synthetic Source paragraph boundary operation unavailable')
        assert original is not None, 'The new hook must be an actual producer operation'
        return original(*args, **kwargs)

    monkeypatch.setattr(vp, '_source_paragraph_boundaries', split_failure, raising=False)

    class Reader:
        cache = tmp_path / 'synthetic-cache/readings'
        calls = 0
        retries = []
        registered_images = []

        def read(self, image):
            self.registered_images.append(image)
            value = prepared[self.calls][2]
            self.calls += 1
            return dict(answer=copy.deepcopy(value), cached=True, new_cost_usd=0)

        def reread(self, image):
            assert image == self.registered_images[1], 'A failing Source unit cannot charge another page allowance'
            self.retries.append(1)
            return dict(answer=copy.deepcopy(prepared[1][2]), cached=True, new_cost_usd=0)

    reader = Reader()
    path = tmp_path / 'contained.epub'
    try:
        report = vp.convert(doc, str(path), reader)
    finally:
        doc.close()
    assert reader.calls == 3 and reader.retries == [1]
    assert reached == [1, 1], 'One actual hook failure and one rejected targeted reassembly are required'
    assert build_epub.validate(path) == []
    assert set(map(int, report['page_homes'])) == {0, 1, 2}
    assert report['reread_pages'] == [1]
    assert any(event.get('stage') == 'source_word_coverage' for event in report['unit_recovery_events'])
    cases = report['degraded_units']
    assert cases and all(case['page'] == 1 for case in cases)
    assert any(case.get('stage') == 'source_word_coverage' and 'boundary operation unavailable' in case.get('reason', '') for case in cases)
    with zipfile.ZipFile(path) as z:
        docs = {name: ET.fromstring(z.read(name)) for name in z.namelist()
                if name.endswith('.xhtml') and not name.endswith(('nav.xhtml', 'vision-about.xhtml'))}
        live = ' '.join(h.body_texts(docs))
        assert live.count(before) == live.count(after) == 1
        for case in cases:
            assert 'OEBPS/' + case['source_image'] in z.namelist()
            assert any(img.get('src') == case['source_image'] for root in docs.values() for img in root.findall('.//x:img', h.NS))
        assert c.source_returns(docs), 'Rejected Source units must remain visibly disclosed with working original Returns'
