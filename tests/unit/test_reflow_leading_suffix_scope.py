"""Synthetic leading contractions distinguish whole words from cropped tails."""
import pytest
from cps.services.reflow import pixel_adjudication as pa, reading_tiers

pytestmark = pytest.mark.unit


def leading_case(*, complete=False, prefix=False, ambiguous=False, enlarged=False):
    model = '7. CHECK THE READING: Notice whether the printed token is a bookcase. The next sentence remains readable.'.split()
    source = [dict(text=t, bbox=[20 + i * 35, 35, 48 + i * 35, 45], confidence=99)
              for i, t in enumerate('case. The next sentence remains readable.'.split())]
    witness = pa.CropWitness([dict(w) for w in source])
    witness.model_reading = True
    witness.neighbor_occurrences = []
    if prefix:
        witness.neighbor_occurrences.append(dict(text='book-', bbox=[140, 20, 170, 30], confidence=99))
    if ambiguous:
        witness.neighbor_occurrences.append(dict(text='book-', bbox=[180, 20, 210, 30], confidence=99))
    if complete:
        witness.word_readings = lambda word: [[dict(word, confidence=99)]] * 2
        witness.word_scope = lambda word: ([15, 35, 48, 45] if enlarged else list(word['bbox']))
    return model, source, witness


@pytest.mark.parametrize('complete,prefix,ambiguous,enlarged', [
    (False, False, False, False),
    (True, True, False, False),
    (True, True, True, False),
    (True, False, False, True),
])
def test_leading_multiword_span_cannot_be_certified_by_its_cropped_suffix(
        complete, prefix, ambiguous, enlarged):
    """Matching the next sentence locates a tail; it cannot prove completeness."""
    model, source, witness = leading_case(complete=complete, prefix=prefix,
                                          ambiguous=ambiguous, enlarged=enlarged)
    result = pa.adjudicate(model, source, witness)
    note = result['word_notes'][0]
    assert note['status'] == 'UNRESOLVED'
    assert note['source_image_only']
    assert note['method'] in {'unproved_complete_divided_word', 'unproved_complete_word_scope'}
    assert result['tokens'][1:] == [w['text'] for w in source[1:]]
    reading_tiers.prepare(result)
    assert 0 not in result['flagged_readings'], 'An incomplete suffix is print-only, not a plausible whole word'
    assert result['word_notes'][0]['publication']['tier'] == 'print'
    if prefix and not ambiguous:
        assert note['source_parts'] == [[140, 20, 170, 30], source[0]['bbox']]
    else:
        assert not note.get('source_parts'), 'Ambiguous or absent prefixes cannot acquire invented ownership'


def test_complete_standalone_word_can_reject_an_invented_model_opening():
    model, source, witness = leading_case(complete=True)
    result = pa.adjudicate(model, source, witness)
    assert result['tokens'] == [w['text'] for w in source]
    note = result['word_notes'][0]
    assert note['status'] == 'RESOLVED'
    assert not note.get('source_image_only')
    assert note['method'] == 'independent_upright_crop'


def test_compiler_keeps_both_divided_parts_when_a_whole_opening_maps_to_one_tail(tmp_path, monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pipeline as vp
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import answer, block
    model, _, _ = leading_case()
    with pymupdf.open() as drawn, pymupdf.open() as scan:
        page = drawn.new_page(width=350, height=100)
        page.insert_text((240, 30), 'book-', fontsize=10)
        page.insert_text((20, 47), 'case. The next sentence remains readable.', fontsize=10)
        words = [dict(text=w[4], bbox=list(w[:4]), confidence=99) for w in page.get_text('words')]
        prefix, body = words[0], words[1:]
        target = scan.new_page(width=350, height=100)
        target.insert_image(target.rect, pixmap=page.get_pixmap(matrix=pymupdf.Matrix(3, 3)))
        monkeypatch.setattr(pa, 'read_crop', lambda *a, **k: pa.CropWitness(body))

        class Package:
            def __init__(self):
                self.images = {}

            def image(self, name, data):
                self.images[name] = data

        package = Package()
        images = {}
        compiled, receipt = vp.compile_page(
            answer(block(' '.join(model), bbox=(0, 0, 1000, 1000))), [],
            SourceDisplay(scan, 0), 0, package, images, pixel_words=words,
            regions={'b1': [[15, 34, 330, 65]]}, body_owners={},
            note_context={'cache': tmp_path / 'cache', 'labels': {}, 'catalogue': {}, 'refs': {}})
    record = receipt['blocks'][0]
    assert 0 in record['source_image_words']
    assert '0' not in record['flagged_readings']
    assert record['published_tokens'][1:] == [w['text'] for w in body[1:]]
    evidence = [(name, item) for name, item in images.items() if item.get('source_parts')]
    assert evidence, 'The incomplete tail must retain its positively located prefix as inspectable print'
    for name, item in evidence:
        assert item['source_parts'] == [prefix['bbox'], body[0]['bbox']]
        assert name in package.images
    assert 'source-word-image' in ''.join(compiled['body'])
