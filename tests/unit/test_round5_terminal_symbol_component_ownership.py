"""SOURCE-free actual EPUB controls: nearby punctuation cannot own a note glyph.

Every candidate box contains its own original synthetic ink. A deterministic
character-recognition decoy arrives before the genuine terminal star/dagger;
the tiny reader must never receive the unrelated punctuation crop. No network,
external source, optical rerun, or model response is used.
"""
import copy
import importlib.util
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
import pymupdf
import pytest
from cps.services.reflow import vision_pipeline, vision_pixels, build_epub
from cps.services.reflow.source_display import SourceDisplay

_helper = Path(__file__).with_name('test_round4_defects.py')
if not _helper.exists():
    _helper = Path(vision_pipeline.__file__).parents[3] / 'tests/unit/test_round4_defects.py'
_s = importlib.util.spec_from_file_location('round5_terminal_symbol_ink', _helper)
h = importlib.util.module_from_spec(_s)
_s.loader.exec_module(h)
pytestmark = pytest.mark.unit


def source_ink(page, rect):
    """Independent tight bounds from the synthetic ORIGINAL raster, not OCR."""
    clip = pymupdf.Rect(rect)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(12, 12), clip=clip,
                          colorspace=pymupdf.csGRAY, alpha=False)
    dark = [(i % pix.width, i // pix.width) for i, value in enumerate(pix.samples)
            if value < 180]
    assert dark, 'the candidate must contain genuine printed source ink'
    return [(pix.x + min(x for x, y in dark)) / 12,
            (pix.y + min(y for x, y in dark)) / 12,
            (pix.x + max(x for x, y in dark) + 1) / 12,
            (pix.y + max(y for x, y in dark) + 1) / 12]


def fixture(mark='*', punctuation='’', symbol_size=6):
    p = h.InkPage()
    font = pymupdf.Font('cjk')
    p.page.insert_font(fontname='unicode', fontbuffer=font.buffer)
    prefix = 'Several careful observations describe clear specimens'
    p.page.insert_text((22, 55), prefix, fontsize=7)
    x = 22 + pymupdf.get_text_length(prefix, fontsize=7)
    p.page.insert_text((x, 52), punctuation, fontsize=3, fontname='unicode')
    width = font.text_length(punctuation, fontsize=3)
    decoy = source_ink(p.page, [x, 48, x + width + .3, 54])
    sx = x + width + 1.1
    p.page.insert_text((sx, 53.5), mark, fontsize=symbol_size, fontname='unicode')
    sw = font.text_length(mark, fontsize=symbol_size)
    true = source_ink(p.page, [sx, 44, sx + sw + .5, 58])
    assert decoy[2] < true[0], 'the false candidate and symbol are distinct source components'
    text = prefix + punctuation + mark
    p.blocks.append(h.model_block(text, 'body', 'body', [20, 43, 282, 59]))
    note = 'The independent source note explains the ordinary observation precisely.'
    p.page.insert_text((22, 320), mark + ' ' + note, fontsize=6, fontname='unicode')
    p.blocks.append(h.model_block(note, 'note', 'footnote', [20, 309, 282, 325], marker=mark))
    image, words, answer = p.finish()
    # A whole-word optical reading may fuse punctuation and a symbol. Its large
    # box must not make the punctuation's own distinct ink into the note glyph.
    suffix = [w for w in words if w['bbox'][1] < 60 and w['bbox'][0] >= x - pymupdf.get_text_length('specimens', fontsize=7) - .2]
    assert suffix and any('specimens' in w['text'] for w in suffix)
    words = [w for w in words if w not in suffix]
    merged = {'text': 'specimens' + punctuation + mark,
              'bbox': [min(w['bbox'][0] for w in suffix), min(w['bbox'][1] for w in suffix),
                       max(w['bbox'][2] for w in suffix), max(w['bbox'][3] for w in suffix)], 'confidence': 99}
    words.append(merged)
    words.sort(key=lambda w: (round(w['bbox'][1] / 10), w['bbox'][0]))
    return image, words, answer, decoy, true, text, note


def convert(tmp_path, monkeypatch, prepared, candidates, response=None):
    image, words, answer, decoy, true, body, note = prepared
    doc = pymupdf.open()
    page = doc.new_page(width=h.W, height=h.H)
    page.insert_image(page.rect, stream=image)
    monkeypatch.setattr(vision_pixels, 'words', lambda display, cache: copy.deepcopy(words))
    monkeypatch.setattr(vision_pixels, 'raised_markers', lambda display, box, cache: copy.deepcopy(candidates))
    crops = []
    original_roi = SourceDisplay.roi_jpeg
    def roi(display, rect=None, **kwargs):
        if kwargs.get('scale') == 12:
            crops.append(list(rect))
        return original_roi(display, rect, **kwargs)
    monkeypatch.setattr(SourceDisplay, 'roi_jpeg', roi)
    class Reader:
        cache = tmp_path / 'cache' / 'readings'
        calls = 0
        marker_calls = 0
        def read(self, image):
            self.calls += 1
            return {'answer': copy.deepcopy(answer), 'cached': True, 'new_cost_usd': 0}
        def read_marker(self, image):
            self.marker_calls += 1
            rect = crops[-1]
            cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
            assert true[0] - .2 <= cx <= true[2] + .2 and true[1] - .2 <= cy <= true[3] + .2, 'unrelated punctuation must be rejected before marker dispatch'
            return dict(response or {'label': answer['blocks'][1]['marker'], 'uncertain': False},
                        cached=True, new_cost_usd=0)
    reader = Reader()
    path = tmp_path / 'synthetic.epub'
    try:
        result = vision_pipeline.convert(doc, str(path), reader)
    finally:
        doc.close()
    assert reader.calls == 1
    assert build_epub.validate(str(path)) == []
    with zipfile.ZipFile(path) as archive:
        docs = {name: ET.fromstring(archive.read(name)) for name in archive.namelist()
                if name.endswith('.xhtml') and not name.endswith(('nav.xhtml', 'vision-about.xhtml'))}
    return docs, reader, result


@pytest.mark.parametrize('punctuation', ['’', ',', '.'])
def test_neighbor_punctuation_candidate_cannot_borrow_terminal_star(tmp_path, monkeypatch, punctuation):
    prepared = fixture(punctuation=punctuation)
    decoy, true = prepared[3:5]
    assert decoy[3] - decoy[1] < 3.2
    docs, reader, _ = convert(tmp_path, monkeypatch, prepared,
        [{'label': '3', 'bbox': decoy, 'kind': 'bare'}, {'label': '*', 'bbox': true, 'kind': 'bare'}])
    refs, notes = h.assert_exact_returns(docs, 1)
    assert ''.join(refs[0][1].itertext()) == '*'
    assert prepared[6] in ''.join(notes[0][1].itertext())
    assert h.body_texts(docs) == [prepared[5].rstrip('*')]


@pytest.mark.parametrize('mark', ['*', '†'])
def test_genuine_fullheight_symbol_keeps_its_local_note_and_return(tmp_path, monkeypatch, mark):
    prepared = fixture(mark=mark)
    docs, _, _ = convert(tmp_path, monkeypatch, prepared,
                        [{'label': mark, 'bbox': prepared[4], 'kind': 'bare'}])
    refs, notes = h.assert_exact_returns(docs, 1)
    assert ''.join(refs[0][1].itertext()) == mark
    assert prepared[6] in ''.join(notes[0][1].itertext())


@pytest.mark.parametrize('response', [{'label': '9', 'uncertain': False}, {'label': '', 'uncertain': True}])
def test_conflicting_or_uncertain_true_symbol_reading_still_fails(tmp_path, monkeypatch, response):
    prepared = fixture(symbol_size=3)
    assert prepared[4][3] - prepared[4][1] <= 3.2
    from tests.unit.round7_public_note_contract_support import source_visible
    strict=vision_pipeline._literal_note_refs;rejected=[]
    def observed(*args,**kwargs):
        try:return strict(*args,**kwargs)
        except ValueError as error:rejected.append(str(error));raise
    monkeypatch.setattr(vision_pipeline,'_literal_note_refs',observed)
    docs,reader,report=convert(tmp_path,monkeypatch,prepared,
            [{'label':'*','bbox':prepared[4],'kind':'bare'}],response=response)
    assert rejected and any('Source-proven' in message and 'note reference' in message for message in rejected),'strict selected-marker protocol remains rejecting'
    source_visible(tmp_path/'synthetic.epub',report,{0})
    refs,notes=h.refs_and_notes(docs)
    assert refs==[],'conflicting or uncertain original glyph must not invent a normal owner'
    assert len(notes)==1 and prepared[6] in ''.join(notes[0][1].itertext())
    assert reader.marker_calls==1,'failed original glyph was dispatched once, never erased by recovery'
