"""Source-free paragraph-tail ownership before source-figure expansion.

The reader's source-proven wording stays unchanged; optical fused/truncated
words cannot make a real paragraph's short final row part of the illustration.
"""
import importlib.util
import io
from pathlib import Path
import zipfile
import pymupdf
from PIL import Image
from cps.services.reflow import vision_pixels

helper_path = Path(__file__).with_name('test_round4_defects.py')
if not helper_path.exists():
    helper_path = Path(__file__).parents[1] / 'test_round4_defects.py'
spec = importlib.util.spec_from_file_location('coherent_tail_helpers', helper_path)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
FIRST = 'We now examine several clear examples and their useful simple forms'

class ShortTailPage(h.InkPage):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode
        self.page.insert_text((24, 50), FIRST, fontsize=8)
        if mode == 'note':
            self.tail = 'below.'
            self.page.insert_text((24, 69), self.tail, fontsize=8, color=(1, 0, 0))
            end = 24 + pymupdf.get_text_length(self.tail, fontsize=8)
            self.page.insert_text((end + 1, 65), '7', fontsize=5, color=(1, 0, 0))
            model = FIRST + ' below. <n:n1>'
        elif mode == 'wrap':
            self.tail = 'kpieces:'
            end = 24 + pymupdf.get_text_length(FIRST, fontsize=8)
            self.page.insert_text((end + 3, 50), 'wor-', fontsize=8, color=(1, 0, 0))
            self.page.insert_text((24, 69), self.tail, fontsize=8, color=(1, 0, 0))
            model = FIRST + ' workpieces:'
        else:
            self.tail = 'workpieces:'
            self.page.insert_text((24, 69), self.tail, fontsize=8, color=(1, 0, 0))
            model = FIRST + ' workpieces:'
        self.blocks.append(h.model_block(model, 'coherent', 'body', [23, 38, 282, 74]))
        self.figure(hint=[110, 115, 165, 140])
        if mode == 'note':
            self.text('7 A printed source note explains the final word.', 'n1', 'footnote',
                y=320, size=7, marker='7', model='A printed source note explains the final word.')

    def finish(self):
        image, words, answer = super().finish()
        tail = [w for w in words if 55 < w['bbox'][1] < 73 and w['bbox'][0] < 80]
        assert tail, 'actual short tail source geometry exists'
        box = [min(w['bbox'][0] for w in tail), min(w['bbox'][1] for w in tail),
               max(w['bbox'][2] for w in tail), max(w['bbox'][3] for w in tail)]
        if self.mode == 'note':
            words = [w for w in words if w not in tail]
            words.append(dict(text='below.?7', bbox=box, confidence=99))
        elif self.mode == 'partial':
            words = [w for w in words if w not in tail]
            words.append(dict(text='kpieces:', bbox=box, confidence=99))
        else:
            assert any(w['text'] == 'wor-' for w in words)
            assert any(w['text'] == 'kpieces:' for w in words)
        words.sort(key=lambda w: (w['bbox'][1], w['bbox'][0]))
        return image, words, answer

def figure_picture(path, result):
    figures = [(src, rec) for src, rec in result['images'].items() if rec['kind'] == 'figure']
    assert len(figures) == 1, 'one real source illustration is published'
    src, rec = figures[0]
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(src))
        picture = Image.open(io.BytesIO(archive.read(name))).convert('RGB')
    return picture, rec

def assert_complete_figure_without_body_red(path, result):
    picture, rec = figure_picture(path, result)
    assert rec['bbox'][0] <= 35 and rec['bbox'][2] >= 260
    assert rec['bbox'][1] <= 90 and rec['bbox'][3] >= 175
    assert not any(r > 130 and g < 90 and b < 90 for r, g, b in picture.getdata()), 'complete illustration must exclude source paragraph tail and raised note ink'

def test_coherent_last_row_with_fused_optical_note_stays_live_and_outside_figure(tmp_path, monkeypatch):
    page = ShortTailPage('note')
    # Geometry controls do not perform character OCR; explicit printed n1 note
    # ownership is already part of the source-reading protocol.
    monkeypatch.setattr(vision_pixels, 'raised_markers', lambda *args: [])
    path, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    assert_complete_figure_without_body_red(path, result)
    assert any(FIRST + ' below.' in text for text in h.body_texts(docs)), 'correct source-reading final word remains live without an optical rewrite'
    refs, notes = h.assert_exact_returns(docs, 1)
    assert ''.join(refs[0][1].itertext()) == '7'

def test_coherent_last_row_printed_hyphen_wrap_stays_live_and_outside_figure(tmp_path, monkeypatch):
    page = ShortTailPage('wrap')
    path, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    assert_complete_figure_without_body_red(path, result)
    assert any(FIRST + ' workpieces:' in text for text in h.body_texts(docs)), 'correct whole printed word remains live; printed suffix must not become figure text'

def test_coherent_last_row_partial_optical_letters_stays_live_and_outside_figure(tmp_path, monkeypatch):
    page = ShortTailPage('partial')
    path, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    assert_complete_figure_without_body_red(path, result)
    assert any(FIRST + ' workpieces:' in text for text in h.body_texts(docs)), 'correct whole printed word remains live; optical suffix cannot replace it'

def test_genuine_red_diagram_label_is_not_removed_as_paragraph_tail(tmp_path, monkeypatch):
    page = h.InkPage()
    page.text('Ordinary readable prose ends before the following illustration.', 'body')
    page.figure(hint=[110, 115, 165, 140])
    page.page.insert_text((80, 130), 'workpieces:', fontsize=8, color=(1, 0, 0))
    path, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    picture, rec = figure_picture(path, result)
    assert any(r > 130 and g < 90 and b < 90 for r, g, b in picture.getdata()), 'authored diagram lettering remains in the full figure'
    assert not any('workpieces:' in text for text in h.body_texts(docs)), 'diagram label must not be manufactured as body prose'
