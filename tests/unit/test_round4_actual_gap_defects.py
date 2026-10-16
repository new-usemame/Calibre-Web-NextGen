"""Source-free crop/flow witnesses calibrated from observed gap failures."""
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image

_spec = importlib.util.spec_from_file_location('round4_gap_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


class MissingCaptionPage(h.InkPage):
    def finish(self):
        image, words, reading = super().finish()
        # Original caption ink is real but this independent optical recognition
        # omits it, as can happen on small printed figure labels.
        words = [w for w in words if not 180 < w['bbox'][1] < 195]
        return image, words, reading


@pytest.mark.parametrize('side', ['before', 'after'])
def test_short_prose_outside_bound_reading_never_becomes_figure_pixels(tmp_path, monkeypatch, side):
    page = MissingCaptionPage()
    lines = ['A complete ordinary paragraph stays live.',
             'Its coherent source lines establish the paragraph territory.',
             'The final printed fragment remains ordinary prose.']
    if side == 'before':
        page.text(lines, 'first', y=37, color=(1, 0, 0))
        # A valid local anchor set represents the first two rows but not the
        # last short physical row. The model still transcribes every word.
        page.blocks[-1]['bbox'] = [23 / h.W * 1000, 24 / h.H * 1000,
                                   280 / h.W * 1000, 55 / h.H * 1000]
    else:
        page.text('The completed opening paragraph ends here.', 'first', y=50)
    page.figure(caption=False)
    page.text('Plate 4.', 'cap', 'caption', y=190, size=7, model='Plate4.')
    if side == 'after':
        lines = ['The clear opening stays live.',
                 'Its coherent source lines establish the paragraph territory.',
                 'The ordinary paragraph continues with enough literal anchors.',
                 'Every final word remains in the published flowing text.']
        page.text(lines, 'last', y=212, color=(1, 0, 0))
        page.blocks[-1]['bbox'] = [23 / h.W * 1000, 217 / h.H * 1000,
                                   280 / h.W * 1000, 258 / h.H * 1000]
    else:
        page.text('A separate ordinary paragraph follows the illustration.', 'last', y=218)
    path, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    figures = [(name, rec) for name, rec in result['images'].items() if rec['kind'] == 'figure']
    assert len(figures) == 1
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(figures[0][0]))
        image = Image.open(io.BytesIO(archive.read(name))).convert('RGB')
    assert not any(r > 130 and g < 90 and b < 90 for r, g, b in image.getdata()), 'short live prose outside bound anchors must remain outside figure pixels'
    published = ' '.join(h.body_texts(docs))
    assert all(line in published for line in lines)
    box = figures[0][1]['bbox']
    assert box[0] <= 35 and box[1] <= 90 and box[2] >= 260 and box[3] >= 175, 'whole connected illustration remains present'


def test_source_owned_opening_outside_initial_anchor_region_keeps_sentence_intact(tmp_path, monkeypatch):
    page = h.InkPage()
    first = 'The quiet traveller followed the trail which'
    lines = ['is clearly visible under leaves.',
             'Several exact later words establish its source territory.',
             'Every ordinary branch remains part of the same explanation.',
             'The full printed sentence ends without any intervening heading.']
    page.text(first, 'first')
    page.figure()
    page.text(lines, 'last', y=218)
    # Correct optical opening exists. The initially valid reading rectangle
    # begins at the second row; plentiful later anchors accept that rectangle.
    page.blocks[-1]['bbox'] = [23 / h.W * 1000, 223 / h.H * 1000,
                               280 / h.W * 1000, 266 / h.H * 1000]
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    assert h.body_texts(docs) == [first + ' ' + ' '.join(lines)], 'printed opening omitted from initial anchors still belongs to this source-proven sentence'
    nodes = h.body_nodes(docs)
    assert nodes[0].tag.endswith('}p') and nodes[1].tag.endswith('}figure')


@pytest.mark.parametrize('imprint', ['RIVER TOWN: PRINTED BY A SMALL PRESS.',
                                    'SMALL PRESS COMPANY, PRINTERS, RIVER TOWN.'])
def test_isolated_small_printer_imprint_does_not_continue_final_note(tmp_path, monkeypatch, imprint):
    page = h.InkPage()
    page.text('NOTES', 'title', 'heading', y=30, size=12)
    page.text('7 A brief explanation ends with an unfinished clause and',
              'note', 'footnote', y=298, size=7, marker='7',
              model='A brief explanation ends with an unfinished clause and')
    page.page.draw_line((80, 316), (205, 316), width=.5)
    page.text(imprint, 'imprint', 'other', x=60, y=330, size=5)
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    _, notes = h.refs_and_notes(docs)
    assert len(notes) == 1
    assert imprint not in ''.join(notes[0][1].itertext()), 'separate centered small publishing furniture is not note continuation'
    assert imprint not in ' '.join(h.body_texts(docs))


def test_printing_words_inside_source_labelled_note_are_not_furniture(tmp_path, monkeypatch):
    page = h.InkPage()
    page.text('NOTES', 'title', 'heading', y=30, size=12)
    text = 'Printed by a small press is the quoted phrase under discussion.'
    page.text('7 ' + text, 'note', 'footnote', y=298, size=7, marker='7', model=text)
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    _, notes = h.refs_and_notes(docs)
    assert len(notes) == 1 and text in ''.join(notes[0][1].itertext())


class RemotePunctuationPage(h.InkPage):
    def finish(self):
        image, words, reading = super().finish()
        found = [w for w in words if w['text'] == 'warning.)']
        assert len(found) == 1
        return image, words, reading


def test_literal_printed_row_hyphen_wrap_survives_unrelated_punctuation_disagreement(tmp_path, monkeypatch):
    page = RemotePunctuationPage()
    lines = ['A quiet observer chose to co-',
             'operate with a patient companion beside the shelter.',
             'The careful record ends with a warning.)']
    page.text(lines, 'body', model=' '.join(lines).replace('warning.)', 'warning.]'))
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    texts = h.body_texts(docs)
    assert len(texts) == 1
    assert 'co-operate' in texts[0] and 'co- operate' not in texts[0], 'a remote punctuation difference cannot undo exact adjacent printed wrap-word evidence'
    assert 'warning.]' in texts[0], 'literal published disagreement remains uncorrected'
