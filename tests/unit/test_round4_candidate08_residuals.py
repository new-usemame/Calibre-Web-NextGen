"""Source-free publication witnesses for residual actual reader failures."""
import importlib.util
from pathlib import Path
import pytest
from cps.services.reflow import vision_pipeline

_spec = importlib.util.spec_from_file_location('round4_residual_helpers', Path(__file__).with_name('test_round4_defects.py'))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


class UnrecognizedCaptionPage(h.InkPage):
    def finish(self):
        image, words, reading = super().finish()
        words = [w for w in words if not 178 < w['bbox'][1] < 194]
        return image, words, reading


@pytest.mark.parametrize('terminal', [False, True])
def test_stale_unrecognized_caption_hint_cannot_define_body_sentence_boundary(tmp_path, monkeypatch, terminal):
    page = UnrecognizedCaptionPage()
    first = 'The quiet traveller followed the trail' + ('.' if terminal else ' which')
    lines = ['is clearly visible beside the shelter.',
             'Several later literal words identify the separate printed body.',
             'The ordinary explanation ends with a complete sentence.']
    page.text(first, 'first')
    page.figure()
    caption = page.blocks[-1]
    # Small source caption ink is not recognized. A valid model hint drifts
    # down into the clear, independently corroborated next body opening.
    caption['bbox'] = [30 / h.W * 1000, 211 / h.H * 1000,
                       250 / h.W * 1000, 227 / h.H * 1000]
    page.text(lines, 'last', y=218)
    _, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    assert len([r for r in result['images'].values() if r['kind'] == 'figure']) == 1
    texts = h.body_texts(docs)
    if terminal:
        assert texts == [first, ' '.join(lines)], 'a complete sentence remains a real paragraph boundary'
    else:
        assert texts == [first + ' ' + ' '.join(lines)], 'unrecognized caption geometry cannot interrupt a source-proven unfinished body sentence'
        assert h.body_nodes(docs)[1].tag.endswith('}figure'), 'artwork follows the complete joined paragraph once'


class DegreeFooterPage(h.InkPage):
    def finish(self):
        image, words, reading = super().finish()
        found = [w for w in words if w['text'] == 'O' and w['bbox'][0] > 180 and w['bbox'][1] > 360]
        assert len(found) == 1
        found[0]['text'] = '°'
        return image, words, reading


def test_local_footer_signature_is_not_hidden_by_far_left_body_descender_union(tmp_path, monkeypatch):
    page = DegreeFooterPage()
    first = 'A clear ordinary passage occupies the lower page.'
    page.text([first, 'Another readable line belongs to the same explanation.'], 'body', y=338)
    page.page.insert_text((24, 375), 'A final sloped edge', fontsize=9)
    page.blocks[-1]['text'] += ' A final sloped edge'
    page.blocks[-1]['bbox'][3] = 379 / h.H * 1000
    page.page.insert_text((196, 379), 'O', fontsize=7)
    page.blocks.append(h.model_block('An isolated footer mark', 'mark', 'figure', [191, 367, 213, 390]))
    _, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    assert not [r for r in result['images'].values() if r['kind'] == 'figure'], 'a small isolated footer signature below its local prose is not artwork merely because a remote descender expands a body union'
    assert first in ' '.join(h.body_texts(docs))


def test_small_internal_body_symbol_is_not_a_footer_signature(tmp_path, monkeypatch):
    page = h.InkPage()
    text = 'The labelled expression O is a meaningful part of ordinary prose.'
    page.text(text, 'body', y=365)
    _, result, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    assert h.body_texts(docs) == [text]
    assert result['images'] == {}


def test_tiny_other_footer_does_not_borrow_an_authored_body_I(tmp_path, monkeypatch):
    page = h.InkPage()
    lines = ['I keep the first ordinary record beside the shelter.',
             'The next sentence says I remember its meaningful lesson.',
             'Every last source word remains part of the live paragraph.']
    page.text(lines, 'body', y=60)
    page.page.insert_text((180, 374), 'I', fontsize=6)
    # The local model footer hint misses the tiny ink, but is still physically
    # marginal. Global one-token matching must not steal an earlier authored I.
    page.blocks.append(h.model_block('I', 'footer', 'other', [175, 380, 190, 388]))
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    assert h.body_texts(docs) == [' '.join(lines)], 'the authored I tokens stay inside body while isolated footer I is omitted'


class PartialYearPage(h.InkPage):
    def finish(self):
        image, words, reading = super().finish()
        found = [w for w in words if '(2014)' in w['text']]
        assert len(found) == 1
        found[0]['text'] = found[0]['text'].replace('(2014)', '(014)')
        return image, words, reading


@pytest.mark.parametrize('year,partial', [('1896', False), ('2014', False), ('2020', False), ('2014', True)])
def test_baseline_bibliographic_year_never_dispatches_marker_read_or_creates_reference(tmp_path, monkeypatch, year, partial):
    page, notes = PartialYearPage() if partial else h.InkPage(), h.InkPage()
    text = 'A careful catalogue lists River Press (' + year + '), volume six.'
    page.text([text, 'The publication year belongs to the ordinary baseline source line.'], 'body')
    notes.text(year + ' A separate selected note is unrelated to the publication date.',
               'note', 'footnote', y=320, size=7, marker=year,
               model='A separate selected note is unrelated to the publication date.')
    actual = vision_pipeline._literal_note_refs
    calls = []
    def callback(image):
        assert image[:2] == b'\xff\xd8'
        calls.append(len(image))
        return dict(label=year, uncertain=False, cached=True, new_cost_usd=0)
    def refs(tokens, optical, display, box, cache, labels, pno, context):
        context['marker_reader'] = callback
        return actual(tokens, optical, display, box, cache, labels, pno, context)
    monkeypatch.setattr(vision_pipeline, '_literal_note_refs', refs)
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [page, notes])
    assert calls == [], 'ordinary baseline publication-year ink cannot be paid as a note label'
    assert h.refs_and_notes(docs)[0] == []
    assert text in ' '.join(h.body_texts(docs))


def test_genuine_raised_four_digit_marker_remains_a_linked_reference(tmp_path, monkeypatch):
    page, notes = h.InkPage(), h.InkPage()
    page.reference(number=2048, raised=True)
    notes.text('2048 A finite labelled note explains the synthetic crystal.',
               'note', 'footnote', y=320, size=7, marker='2048',
               model='A finite labelled note explains the synthetic crystal.')
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [page, notes])
    refs, _ = h.assert_exact_returns(docs, 1)
    assert ''.join(refs[0][1].itertext()) == '2048'


class DisputedOpeningPage(h.InkPage):
    def finish(self):
        image, words, reading = super().finish()
        found = [w for w in words if w['text'] == 'operate']
        assert len(found) == 1
        found[0]['text'] = "‘operate"
        return image, words, reading


@pytest.mark.parametrize('terminal', [False, True])
def test_page_turn_with_extra_model_leading_hyphen_preserves_physical_word_continuity(tmp_path, monkeypatch, terminal):
    first, second = h.InkPage(), DisputedOpeningPage()
    tail = 'A patient companion chooses to co-' if not terminal else 'A patient companion finishes the ordinary explanation.'
    first.text(tail, 'body', y=340, next=True)
    second.text(['operate beside the quiet shelter with a careful companion.',
                 'The remaining source paragraph has enough literal matching words.'],
                'body', y=55, model='-operate beside the quiet shelter with a careful companion. The remaining source paragraph has enough literal matching words.')
    second.blocks[-1]['low_confidence'] = [0]
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [first, second])
    texts = h.body_texts(docs)
    if terminal:
        assert len(texts) == 2, 'a leading model hyphen is not permission to cross a printed sentence stop'
    else:
        assert len(texts) == 1, 'an uncertain extra model head hyphen cannot detach a source-proven page-turn word half into a new paragraph'
        assert 'co-' in texts[0] and 'operate' in texts[0], 'all printed word halves remain in the live paragraph'


def test_disputed_page_head_changed_letters_do_not_prove_a_wrap_word(tmp_path, monkeypatch):
    first, second = h.InkPage(), h.InkPage()
    first.text('A patient companion chooses to co-', 'body', y=340, next=True)
    second.text(['wait beside the quiet shelter with a careful companion.',
                 'The remaining source paragraph has enough literal matching words.'],
                'body', y=55, model='-operate beside the quiet shelter with a careful companion. The remaining source paragraph has enough literal matching words.')
    second.blocks[-1]['low_confidence'] = [0]
    _, _, docs = h.convert_ink(tmp_path, monkeypatch, [first, second])
    assert len(h.body_texts(docs)) == 2, 'different source opening letters cannot prove the model word-half seam'
