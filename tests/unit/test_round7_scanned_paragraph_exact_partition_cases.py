"""Additive Source partition predicates, frozen after C51 writer/before execution.

Direct predicate cases do not claim public recovery-ladder credit. The separate
public punctuation case and preimplementation hook control exercise that ladder.
"""
import copy
import importlib.util
from pathlib import Path

import pymupdf
import pytest
from cps.services.reflow import vision_pipeline as vp
from cps.services.reflow.source_display import SourceDisplay

s = importlib.util.spec_from_file_location('scanned_partition_fixture',
    Path(__file__).with_name('test_round7_scanned_source_paragraph_ownership.py'))
c = importlib.util.module_from_spec(s)
s.loader.exec_module(c)
h = c.h


def source_case(mode='plain'):
    p, expected = c.fixture(mode)
    image, words, answer = p.finish()
    doc = pymupdf.open()
    page = doc.new_page(width=h.W, height=h.H)
    page.insert_image(page.rect, stream=image)
    display = SourceDisplay(doc, 0)
    box = [23, 38, 282, 151]
    regions = {'body0': [box]}
    return doc, display, words, answer, regions, {'body0': box}, expected


@pytest.mark.parametrize('case,reason', [
    ('duplicate_window', 'ambiguous response ownership'),
    ('punctuation', 'punctuation disagrees'),
    ('reversed_windows', 'no ordered response partition'),
])
def test_actual_original_partition_predicates_reject_unproved_model_order(case, reason):
    doc, display, words, answer, regions, owners, expected = source_case()
    try:
        block = answer['blocks'][0]
        tokens = block['text'].split()
        first = len(expected[0].split())
        second = first + len(expected[1].split())
        if case == 'duplicate_window':
            block['text'] += ' ' + ' '.join(tokens[first - 3:first + 3])
        elif case == 'punctuation':
            tokens[first - 1] = tokens[first - 1].rstrip('.')
            block['text'] = ' '.join(tokens)
        else:
            block['text'] = ' '.join(tokens[second - 3:second + 3] + tokens[first - 3:first + 3])
        original = copy.deepcopy(answer)
        original_regions = copy.deepcopy(regions)
        with pytest.raises(ValueError, match=reason):
            vp._source_paragraph_boundaries(answer, words, display, regions, owners)
        assert answer == original and regions == original_regions, 'Rejected partitions may not commit a partial Source owner map'
    finally:
        doc.close()


def test_derived_partition_ID_collision_terminates_without_overwriting_original_owner():
    doc, display, words, answer, regions, owners, expected = source_case()
    try:
        bid = 'syntheticbodyownerwithlongidentityxx'
        answer['blocks'][0]['id'] = bid
        regions[bid] = regions.pop('body0')
        owners[bid] = owners.pop('body0')
        reserved = [bid[:30] + '-p2']
        for index in range(1, 20):
            suffix = '-p2-' + str(index)
            reserved.append(bid[:39 - len(suffix)] + suffix)
        for ident in reserved:
            answer['blocks'].append(h.model_block('123', ident, 'folio', [24, 355, 40, 365]))
        original = copy.deepcopy(answer)
        result, repairs = vp._source_paragraph_boundaries(answer, words, display, regions, owners)
        assert answer == original
        identifiers = [b['id'] for b in result['blocks']]
        assert len(identifiers) == len(set(identifiers))
        assert all(sum(b['id'] == ident and b['role'] == 'folio' for b in result['blocks']) == 1 for ident in reserved)
        parts = [b for b in result['blocks'] if b['role'] == 'body']
        assert [b['text'] for b in parts] == expected
        assert len(repairs) == 1 and repairs[0]['derived_blocks'] == [b['id'] for b in parts]
    finally:
        doc.close()


def test_exact_token_confidence_partition_owns_only_original_paragraph_rows():
    doc, display, words, answer, regions, owners, expected = source_case('uncertain')
    try:
        original = copy.deepcopy(answer)
        result, repairs = vp._source_paragraph_boundaries(answer, words, display, regions, owners)
        assert answer == original
        parts = [b for b in result['blocks'] if b['role'] == 'body']
        assert [b['text'] for b in parts] == expected
        assert [b['low_confidence'] for b in parts] == [[], [expected[1].split().index('careful')], []]
        assert ' '.join(b['text'] for b in parts).split() == original['blocks'][0]['text'].split()
        assert len(repairs) == 1
        for part in parts:
            ident = part['id']
            assert regions[ident] == [owners[ident]]
            owned = vp.owned_words(words, owners[ident])
            assert [w['text'] for w in owned] == part['text'].split()
            assert all(regions['_instances'][vp._source_instance(w)] == ident for w in owned)
    finally:
        doc.close()


def test_public_original_punctuation_disagreement_is_visible_source_not_guessed_split(tmp_path, monkeypatch):
    p, expected = c.fixture()
    p.blocks[0]['text'] = p.blocks[0]['text'].replace('bank.', 'bank')
    original = copy.deepcopy(p.blocks)
    _, report, docs = h.convert_ink(tmp_path, monkeypatch, [p])
    assert p.blocks == original
    assert report['degraded_units']
    assert all(case['page'] == 0 for case in report['degraded_units'])
    assert any(case.get('stage') == 'source_word_coverage' and 'punctuation disagrees' in case.get('reason', '') for case in report['degraded_units'])
    assert c.source_returns(docs)


def test_new_recovered_paragraph_failure_retains_its_geometry_and_healthy_text(tmp_path, monkeypatch):
    page, paragraphs = c.fixture()
    page.blocks.clear()
    healthy = 'The final clear sample remains readable beside the window.'
    page.text(healthy, 'healthy', y=240, size=8)
    cover = vp.cover_missing_lines
    recovered_ids = set()

    def unproved_recovered_response(*args, **kwargs):
        answer, recovered = cover(*args, **kwargs)
        for block in answer['blocks']:
            if block['id'] not in recovered:
                continue
            recovered_ids.add(block['id'])
            block['role'] = 'body'
            tokens = block['text'].split()
            cut = len(paragraphs[0].split())
            block['text'] += ' ' + ' '.join(tokens[cut-3:cut+3])
        return answer, recovered

    monkeypatch.setattr(vp, 'cover_missing_lines', unproved_recovered_response)
    _, report, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    record = report['pages'][0]
    assert record['source'] == 'vision'
    assert recovered_ids and {u['block'] for u in record['degraded_units']} == recovered_ids
    assert all('ambiguous response ownership' in u['reason'] for u in record['degraded_units'])
    assert ' '.join(h.body_texts(docs)).count(healthy) == 1
    assert c.source_returns(docs)
    assert not any(e.get('class') == 'source-original' for root in docs.values() for e in root.iter())


@pytest.mark.parametrize('side', ['before', 'after'])
def test_one_optical_character_error_does_not_discard_proved_paragraphs(side):
    doc, display, words, answer, regions, owners, expected = source_case()
    try:
        target = 'bank.' if side == 'before' else 'young'
        changed = 'bnk.' if side == 'before' else 'oung'
        occurrence = next(word for word in words if word['text'] == target)
        occurrence['text'] = changed
        original = copy.deepcopy(answer)
        result, repairs = vp._source_paragraph_boundaries(answer, words, display, regions, owners)
        assert answer == original, 'Boundary inference cannot rewrite the cached reading'
        assert [b['text'] for b in result['blocks'] if b['role'] == 'body'] == expected
        assert len(repairs) == 1
        assert ' '.join(b['text'] for b in result['blocks']).split() == original['blocks'][0]['text'].split()
    finally:
        doc.close()


@pytest.mark.parametrize('side', ['before', 'after'])
def test_public_single_optical_letter_error_keeps_printed_prose_live(tmp_path, monkeypatch, side):
    from cps.services.reflow import pixel_adjudication
    page, expected = c.fixture()
    finish = page.finish
    printed = []
    target = 'bank.' if side == 'before' else 'young'
    changed = 'bnk.' if side == 'before' else 'oung'

    def optical_error():
        image, words, answer = finish()
        printed.extend(copy.deepcopy(words))
        next(word for word in words if word['text'] == target)['text'] = changed
        return image, words, answer

    page.finish = optical_error
    # The second synthetic observer reads the independently printed inventory,
    # including the letter omitted only from the first observer's word record.
    def second_read(display, boxes, cache, **options):
        return copy.deepcopy([w for w in printed if any(
            box[0] <= (w['bbox'][0] + w['bbox'][2]) / 2 <= box[2] and
            box[1] <= (w['bbox'][1] + w['bbox'][3]) / 2 <= box[3]
            for box in boxes)])

    monkeypatch.setattr(pixel_adjudication, '_read_crop', second_read)
    _, report, docs = h.convert_ink(tmp_path, monkeypatch, [page])
    c.invariant(report, docs, expected)
    assert c.lexical(docs) == expected


@pytest.mark.parametrize('case', ['two_alternatives', 'two_word_errors'])
def test_near_paragraph_window_needs_unique_single_character_correspondence(case):
    doc, display, words, answer, regions, owners, expected = source_case()
    try:
        next(w for w in words if w['text'] == 'bank.')['text'] = 'bnk.'
        block = answer['blocks'][0]
        tokens = block['text'].split()
        first = len(expected[0].split())
        if case == 'two_alternatives':
            tokens[-6:] = tokens[first-3:first+3]
        else:
            next(w for w in words if w['text'] == 'young')['text'] = 'oung'
        block['text'] = ' '.join(tokens)
        original = copy.deepcopy(answer)
        original_regions = copy.deepcopy(regions)
        with pytest.raises(ValueError, match='ambiguous response ownership'):
            vp._source_paragraph_boundaries(answer, words, display, regions, owners)
        assert answer == original and regions == original_regions
    finally:
        doc.close()


@pytest.mark.parametrize('public', [False, True])
def test_paragraph_cut_uses_its_local_window_when_unrelated_optical_words_are_fused(tmp_path,monkeypatch,public):
    from cps.services.reflow import pixel_adjudication
    page, expected = c.fixture()
    finish = page.finish
    printed=[]
    def optical_error():
        image, words, answer = finish()
        printed.extend(copy.deepcopy(words))
        first,second=words[:2]
        words[:2]=[dict(first,text=first['text']+second['text'],
            bbox=[first['bbox'][0],first['bbox'][1],second['bbox'][2],second['bbox'][3]])]
        next(word for word in words if word['text']=='bank.')['text']='bnk.'
        return image,words,answer
    if public:
        page.finish=optical_error
        monkeypatch.setattr(pixel_adjudication,'_read_crop',lambda *a,**k:copy.deepcopy(printed))
        _,report,docs=h.convert_ink(tmp_path,monkeypatch,[page])
        c.invariant(report,docs,expected)
        assert c.lexical(docs)==expected
    else:
        image,words,answer=optical_error()
        doc=pymupdf.open();p=doc.new_page(width=h.W,height=h.H);p.insert_image(p.rect,stream=image)
        display=SourceDisplay(doc,0);box=[23,38,282,151]
        original=copy.deepcopy(answer)
        try:
            result,repairs=vp._source_paragraph_boundaries(answer,words,display,{'body0':[box]},{'body0':box})
            assert answer==original
            assert [b['text']for b in result['blocks']]==expected and len(repairs)==1
        finally:doc.close()
