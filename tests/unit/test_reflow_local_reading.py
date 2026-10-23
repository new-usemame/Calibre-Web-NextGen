"""Synthetic raster readings retain their own source occurrence and uncertainty."""
import shutil
import copy

import pymupdf
import pytest
from tests.unit import test_round4_defects as ink
from cps.services.reflow import local_reading, vision_pixels

from cps.services.reflow import unit_recovery, vision_pipeline, vision_reading

pytestmark = pytest.mark.unit


def test_unavailable_reading_uses_local_words_without_releasing_a_financial_hold(tmp_path):
    from cps.services.reflow.ledger import Ledger
    ledger = Ledger(tmp_path/'ledger.jsonl', 1)
    ledger.reserve_attempt('synthetic-request', .25)
    class Reader:
        def read_unit(self, image, **kwargs):
            return dict(degraded_source=True, failure='Reading unavailable',
                        recovery_events=[dict(transition='UNKNOWN_FULL_BOUND_RETAINED')])
    controller = unit_recovery.Controller(Reader(), [0], tmp_path)
    answer = {'blocks': [dict(id='o1', role='body', text='A small bridge crosses the stream.',
        marker='', bbox=[10, 10, 900, 100], low_confidence=list(range(6)),
        continues_previous=False, continues_next=False)]}
    called = []
    def local(reason):
        called.append(reason)
        return dict(answer=answer, local_ocr=True, cached=False, new_cost_usd=0.)
    reading = controller.read(0, b'synthetic-raster', local_read=local)
    assert reading['answer'] == answer and reading['local_ocr'] is True
    assert called == ['Reading unavailable']
    assert controller.events == [dict(transition='UNKNOWN_FULL_BOUND_RETAINED')]
    assert Ledger(tmp_path/'ledger.jsonl', 1).pending_usd() == .25
    assert controller.read(0, b'synthetic-raster', local_read=lambda reason: pytest.fail('repeated OCR')) == reading


def test_local_ocr_agreement_with_a_layer_never_certifies_the_words(tmp_path):
    from cps.services.reflow.source_display import SourceDisplay
    from tests.unit.test_reflow_vision_pilot import block
    doc = pymupdf.open()
    doc.new_page(width=300, height=400)
    text = 'The bridge crosses the small stream.'
    boxes = [dict(text=token, bbox=[20+i*35, 30, 53+i*35, 42])
             for i, token in enumerate(text.split())]
    answer = {'blocks': [block(text, bbox=(50, 50, 950, 200), low=range(6))]}
    class Package:
        def image(self, *args): pass
    page, report = vision_pipeline.compile_page(answer, boxes, SourceDisplay(doc, 0),
        0, Package(), {}, pixel_words=boxes, local_ocr=True)
    body = ''.join(page['body'])
    assert text.replace(' ', '') in ''.join(__import__('re').sub('<[^>]+>', '', body).split())
    assert 'class="uncertain"' in body
    assert report['marks'] == 1
    assert 'Unverified local OCR reading' in ''.join(page['asides'])
    assert 'Low-confidence model reading' not in ''.join(page['asides'])
    doc.close()


@pytest.mark.skipif(not shutil.which('tesseract'), reason='needs installed OCR engine')
def test_empty_cache_no_key_raster_has_live_source_linked_unverified_words(tmp_path):
    from cps.services.reflow.ledger import Ledger
    from tests.unit.test_reflow_vision_pilot import documents, assert_links_resolve
    source = pymupdf.open()
    page = source.new_page(width=420, height=500)
    lines = ['The narrow bridge crosses the quiet stream.',
             'The green garden contains a small wooden bench.',
             'A visitor walks along the path before sunset.']
    for index, line in enumerate(lines):
        page.insert_text((30, 70+index*35), line, fontsize=13)
    image = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes('png')
    doc = pymupdf.open()
    doc.new_page(width=420, height=500).insert_image(pymupdf.Rect(0, 0, 420, 500), stream=image)
    cache = tmp_path / 'readings'
    reader = vision_reading.Reader(None, cache, Ledger(tmp_path/'ledger.jsonl', 0))
    path = tmp_path / 'local.epub'
    result = vision_pipeline.convert(doc, path, reader)
    docs = documents(path)
    assert_links_resolve(docs)
    live = ''.join(''.join(element.itertext()) for name, root in docs.items()
        if not name.endswith('/vision-about.xhtml')
        for element in root.iter() if element.get('data-source-block'))
    for line in lines:
        assert line in live
    assert result['pages'][0]['source'] == 'vision'
    assert result['pages'][0]['local_ocr'] is True
    assert result['pages'][0]['unverified_source_reading'] is True
    assert result['pages'][0]['marks'] >= 1
    assert result['pages'][0]['new_cost_usd'] == 0
    assert list(cache.glob('*.json')) == [], 'OCR receipts must not impersonate paid readings'
    doc.close()
    source.close()


def test_local_recognition_uses_bounded_owned_scratch_and_retains_occurrence_scores(tmp_path, monkeypatch):
    from cps.services.reflow import local_reading, ocr
    from cps.services.reflow.source_display import SourceDisplay
    doc = pymupdf.open()
    doc.new_page(width=300, height=400)
    monkeypatch.setattr(ocr, '_engine', lambda language: ('synthetic-engine', 'synthetic-version', 'synthetic-data'))
    calls = []
    def invoke(args, directory, **kwargs):
        calls.append((args, kwargs))
        assert directory.parent == tmp_path
        assert kwargs == dict(timeout=120, should_stop=None, watched=(directory/'result.tsv',))
        assert (directory/'page.png').exists()
        (directory/'result.tsv').write_text('level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
            '5\t1\t1\t1\t1\t1\t20\t40\t50\t20\t96\tbridge\n'
            '5\t1\t1\t1\t1\t2\t90\t40\t50\t20\t61\tbridge\n')
        return 0, '', ''
    monkeypatch.setattr(ocr, '_invoke', invoke)
    record = local_reading.read(SourceDisplay(doc, 0), tmp_path, 'No reading available')
    assert record['answer']['blocks'][0]['text'] == 'bridge bridge'
    assert record['answer']['blocks'][0]['low_confidence'] == [0, 1]
    owned = record['local_ocr_units'][0]
    assert owned['low_confidence_tokens'] == [1]
    assert owned['words'][0]['bbox'] != owned['words'][1]['bbox']
    assert [w['confidence'] for w in owned['words']] == [96, 61]
    again = local_reading.read(SourceDisplay(doc, 0), tmp_path, 'No reading available')
    assert again['local_ocr_evidence']['cached'] and len(calls) == 1
    assert not list(tmp_path.glob('local-ocr-*')), 'owned invocation scratch must be removed'
    doc.close()


def test_low_confidence_local_paragraph_does_not_drop_clear_neighbour(tmp_path,monkeypatch):
    page=ink.InkPage()
    healthy='The clear sample remains readable.'
    weak='The narrow bridge crosses the quiet stream.'
    page.text(healthy,'healthy',y=50)
    page.text(weak,'weak',y=145)
    image,words,_=page.finish()
    for word in words:
        upper=word['bbox'][1]<100
        word.update(paragraph=[0,1 if upper else 2,1],line=1,confidence=99 if upper else 20)
    monkeypatch.setattr(local_reading,'recognize',lambda display,cache:(copy.deepcopy(words),dict(cached=False)))
    # This case isolates paragraph rejection; the orientation path has separate controls.
    def unchanged_orientation(display, cache, reason):
        return local_reading.read(display, cache, reason)
    monkeypatch.setattr(local_reading, 'read_oriented', unchanged_orientation)
    monkeypatch.setattr(local_reading, 'reading_display', lambda display, reading: display)
    monkeypatch.setattr(vision_pixels,'words',lambda display,cache:copy.deepcopy(words))
    class Reader:
        key=None
        cache=tmp_path/'reader'
        def read_unit(self,*args,**kwargs):return dict(degraded_source=True,failure='No local test reading')
    doc=pymupdf.open();p=doc.new_page(width=ink.W,height=ink.H);p.insert_image(p.rect,stream=image)
    try:result=vision_pipeline.convert(doc,tmp_path/'review.epub',Reader())
    finally:doc.close()
    assert result['pages'][0]['source']=='vision'
    assert result['pages'][0]['local_ocr']
    assert {u['block'] for u in result['pages'][0]['degraded_units']}=={'ocr1'}
    import zipfile,xml.etree.ElementTree as ET
    with zipfile.ZipFile(tmp_path/'review.epub') as z:
        roots=[ET.fromstring(z.read(n)) for n in z.namelist() if n.endswith('.xhtml')]
    nodes=[el for root in roots for el in root.iter() if el.get('data-source-block')=='ocr0']
    assert healthy in ' '.join(''.join(el.itertext()) for el in nodes)
    spans=[el for root in roots for el in root.iter() if el.get('class')=='uncertain']
    assert spans
    assert healthy.replace(' ','') in ''.join(''.join(el.itertext()) for el in spans).replace(' ','')

def test_accepted_reading_never_invokes_local_substitute(tmp_path,monkeypatch):
    monkeypatch.setattr(local_reading,'read',lambda *args,**kwargs:pytest.fail('accepted model reading must win'))
    page=ink.InkPage();page.text('The clear sample remains readable.','healthy',y=50)
    path,result,docs=ink.convert_ink(tmp_path,monkeypatch,[page])
    assert not result['pages'][0].get('local_ocr')


def _partition_occurrences():
    """Identical authored words have distinct positions and confidence scores."""
    words = [dict(text='bridge', bbox=[12, 20+i*14, 42, 30+i*14],
                  confidence=confidence, paragraph=[0, 1, 1], line=i+1)
             for i, confidence in enumerate([88, 60, 99, 83, 95, 40])]
    units = [dict(id='ocr0', words=words, mean_confidence=77.5,
                  low_confidence_tokens=[1, 3, 5]),
             dict(id='ocr1', words=[dict(text='garden', bbox=[90, 140, 120, 150],
                  confidence=99, paragraph=[0, 2, 1], line=1)],
                  mean_confidence=99, low_confidence_tokens=[])]
    repair = dict(method='original_indented_paragraph_partition', block='ocr0',
                  derived_blocks=['ocr0', 'ocr0-p2', 'ocr0-p3'],
                  whitespace_token_cuts=[2, 4], original_row_cuts=[2, 4])
    return units, repair


@pytest.mark.parametrize('method', [
    'original_indented_paragraph_partition',
    'original_delimited_hanging_paragraph_partition',
])
def test_local_source_word_partition_uses_occurrence_indices_not_repeated_text(method):
    units, repair = _partition_occurrences()
    repair['method'] = method
    original, original_repair = copy.deepcopy(units), copy.deepcopy(repair)
    actual = local_reading.partition_units(units, [repair])
    assert units == original and repair == original_repair
    assert [unit['id'] for unit in actual] == ['ocr0', 'ocr0-p2', 'ocr0-p3', 'ocr1']
    assert [word for unit in actual for word in unit['words']] == [
        word for unit in original for word in unit['words']]
    assert [unit['mean_confidence'] for unit in actual[:3]] == [74, 91, 67.5]
    assert [unit['low_confidence_tokens'] for unit in actual[:3]] == [[1], [1], [1]]
    for index, unit in enumerate(actual[:3]):
        assert unit['source_unit'] == 'ocr0'
        assert tuple(unit['source_word_range']) == (index*2, index*2+2)
        assert unit['words'] == original[0]['words'][index*2:index*2+2]
    actual[0]['words'][0]['bbox'][0] = 999
    actual[3]['words'][0]['bbox'][0] = 999
    assert units == original, 'a derived report cannot mutate the original OCR receipt'


@pytest.mark.parametrize('cuts', [
    [], [0], [6], [-1], [1, 1], [4, 2], [True], [2.5], ['2'],
])
def test_local_source_word_partition_rejects_invalid_or_ambiguous_cuts(cuts):
    units, repair = _partition_occurrences()
    repair['whitespace_token_cuts'] = cuts
    repair['derived_blocks'] = ['ocr0'] + [f'piece{i}' for i in range(len(cuts))]
    original = copy.deepcopy(units)
    with pytest.raises(ValueError):
        local_reading.partition_units(units, [repair])
    assert units == original


@pytest.mark.parametrize('damage', [
    'missing_source', 'wrong_first_id', 'duplicate_derived_id', 'existing_id_collision',
    'missing_derived_id', 'unsupported_method', 'duplicate_repair', 'non_atomic_word',
])
def test_local_source_word_partition_rejects_unproved_owner_maps(damage):
    units, repair = _partition_occurrences()
    repairs = [repair]
    if damage == 'missing_source':
        repair['block'] = 'absent'
    elif damage == 'wrong_first_id':
        repair['derived_blocks'][0] = 'replacement'
    elif damage == 'duplicate_derived_id':
        repair['derived_blocks'][2] = repair['derived_blocks'][1]
    elif damage == 'existing_id_collision':
        repair['derived_blocks'][2] = 'ocr1'
    elif damage == 'missing_derived_id':
        repair['derived_blocks'].pop()
    elif damage == 'unsupported_method':
        repair['method'] = 'synthetic_unproved_partition'
    elif damage == 'duplicate_repair':
        repairs.append(copy.deepcopy(repair))
    elif damage == 'non_atomic_word':
        units[0]['words'][0]['text'] = 'two words'
    original, original_repairs = copy.deepcopy(units), copy.deepcopy(repairs)
    with pytest.raises(ValueError):
        local_reading.partition_units(units, repairs)
    assert units == original and repairs == original_repairs


def test_local_partition_report_passes_unchanged_source_geometry_and_visible_word_contract(tmp_path, monkeypatch):
    from tests.unit.test_round7_scanned_paragraph_exact_partition_cases import c
    from tests.unit.round7_public_note_contract_support import local_ocr_visible

    # The cleared raster fixture has three authored paragraphs. One recognizer
    # paragraph deliberately groups them, so the real source-bound partition
    # must create both the live blocks and their exact source-word report owners.
    page, expected = c.fixture()
    image, words, _ = page.finish()
    for word in words:
        word.update(paragraph=[0, 1, 1], confidence=99)
    receipt = dict(cached=False, rect=[0, 0, ink.W, ink.H],
                   image='synthetic-source-raster-identity')
    monkeypatch.setattr(local_reading, 'recognize',
        lambda display, cache: (copy.deepcopy(words), copy.deepcopy(receipt)))
    monkeypatch.setattr(local_reading, 'read_oriented',
        lambda display, cache, reason: local_reading.read(display, cache, reason))
    monkeypatch.setattr(local_reading, 'reading_display', lambda display, reading: display)
    monkeypatch.setattr(vision_pixels, 'words', lambda display, cache: copy.deepcopy(words))
    class Reader:
        key = None
        cache = tmp_path/'reader'
        def read_unit(self, *args, **kwargs):
            return dict(degraded_source=True, failure='Synthetic unavailable reading')
    doc = pymupdf.open()
    page = doc.new_page(width=ink.W, height=ink.H)
    page.insert_image(page.rect, stream=image)
    path = tmp_path/'partition.epub'
    try:
        result = vision_pipeline.convert(doc, path, Reader())
    finally:
        doc.close()
    page = result['pages'][0]
    assert len(page['source_paragraph_repairs']) == 1
    repair = page['source_paragraph_repairs'][0]
    assert repair['method'] == 'original_indented_paragraph_partition'
    assert repair['derived_blocks'] == ['ocr0', 'ocr0-p2', 'ocr0-p3']
    verified = local_ocr_visible(path, result, {0: expected}, no_note_refs=True)
    assert [unit['id'] for unit in page['local_ocr_units']] == repair['derived_blocks']
    assert [len(unit['words']) for unit in page['local_ocr_units']] == [len(text.split()) for text in expected]
    assert verified[0]['checked_runs'] == 3


def test_local_detail_crop_contains_final_source_row_missing_from_optical_witness(tmp_path, monkeypatch):
    from tests.unit.round7_public_note_contract_support import local_ocr_visible

    # The recognizer owns two printed rows. The independent witness retains
    # only the first; agreement on that prefix cannot erase the last row's ink.
    lines = ['The small bridge crosses a quiet stream beside the green garden gate.',
             'before sunset.']
    neighbour = 'The green garden remains quiet.'
    page = ink.InkPage()
    page.text(lines, 'two_rows', y=50, size=8)
    page.text(neighbour, 'neighbour', y=150, size=8)
    image, words, _ = page.finish()
    for word in words:
        word.update(paragraph=[0, 1 if word['bbox'][1] < 100 else 2, 1],
                    line=1 if word['bbox'][1] < 50 else 2, confidence=99)
    optical = [copy.deepcopy(word) for word in words
               if word['bbox'][1] < 50 or word['bbox'][1] > 100]
    assert len(words) - len(optical) == 2
    receipt = dict(cached=False, rect=[0, 0, ink.W, ink.H],
                   image='synthetic-two-row-raster')
    monkeypatch.setattr(local_reading, 'recognize',
        lambda display, cache: (copy.deepcopy(words), copy.deepcopy(receipt)))
    monkeypatch.setattr(local_reading, 'read_oriented',
        lambda display, cache, reason: local_reading.read(display, cache, reason))
    monkeypatch.setattr(local_reading, 'reading_display', lambda display, reading: display)
    monkeypatch.setattr(vision_pixels, 'words', lambda display, cache: copy.deepcopy(optical))
    class Reader:
        key = None
        cache = tmp_path/'reader'
        def read_unit(self, *args, **kwargs):
            return dict(degraded_source=True, failure='Synthetic unavailable reading')
    doc = pymupdf.open()
    raster = doc.new_page(width=ink.W, height=ink.H)
    raster.insert_image(raster.rect, stream=image)
    path = tmp_path/'two-rows.epub'
    try:
        result = vision_pipeline.convert(doc, path, Reader())
    finally:
        doc.close()
    owned = result['pages'][0]
    assert [unit['id'] for unit in owned['local_ocr_units']] == ['ocr0', 'ocr1']
    assert [word['text'] for word in owned['local_ocr_units'][0]['words']] == ' '.join(lines).split()
    assert [word['text'] for word in owned['local_ocr_units'][1]['words']] == neighbour.split()
    verified = local_ocr_visible(path, result, {0: [' '.join(lines), neighbour]})
    assert verified[0]['checked_runs'] == 2
    details = [record for name, record in result['images'].items()
               if name.endswith('.jpg') and 'v4-d-0-' in name]
    assert len(details) == 2
    for record in details:
        box = record['bbox']
        assert 0 <= box[0] < box[2] <= ink.W
        assert 0 <= box[1] < box[3] <= ink.H
        assert box[3] - box[1] < 60, 'a detail cannot hide wrong ownership in a whole-page crop'
    first_end = len(' '.join(lines).split()) - 1
    first = result['images'][f'images/v4-d-0-ocr0-{first_end}.jpg']['bbox']
    second = result['images']['images/v4-d-0-ocr1-4.jpg']['bbox']
    assert first[3] < second[1], 'independent same-page paragraphs retain distinct detail crops'


def _local_compilation_fixture():
    """Independent raster ink and optical occurrences written for these tests."""
    from cps.services.reflow.source_display import SourceDisplay
    page = ink.InkPage()
    page.text('The clear bridge crosses a quiet stream beside the green garden gate.',
              'sample', y=60, size=8)
    image, optical, _ = page.finish()
    for word in optical:
        word.pop('confidence', None)
    doc = pymupdf.open()
    raster = doc.new_page(width=ink.W, height=ink.H)
    raster.insert_image(raster.rect, stream=image)
    class Package:
        def __init__(self):
            self.images = {}
        def image(self, name, value):
            self.images[name] = value
    return doc, SourceDisplay(doc, 0), optical, Package()


@pytest.mark.parametrize('route', ['independent_fallback', 'recovered'])
def test_local_final_source_owner_tracks_exact_independent_optical_occurrences(route):
    doc, display, optical, package = _local_compilation_fixture()
    original_optical = copy.deepcopy(optical)
    raw_words = [dict(copy.deepcopy(word), confidence=99) for word in optical]
    ident = 'ocr0' if route == 'independent_fallback' else 'recovered-synthetic'
    if route == 'independent_fallback':
        for word in raw_words[:8]:
            word['text'] = 'river'
    raw_units = ([dict(id=ident, words=raw_words, mean_confidence=99,
                       low_confidence_tokens=[])] if route == 'independent_fallback' else [])
    original_units = copy.deepcopy(raw_units)
    text = ' '.join(word['text'] for word in
                    (raw_words if route == 'independent_fallback' else optical))
    block = ink.model_block(text, ident, 'body', [20, 40, 280, 75])
    block['low_confidence'] = list(range(len(text.split())))
    images = {}
    try:
        page, report = vision_pipeline.compile_page({'blocks': [block]}, [], display,
            0, package, images, pixel_words=optical, local_ocr=True,
            local_ocr_units=raw_units,
            forced_uncertain=[ident] if route == 'recovered' else [])
    finally:
        doc.close()
    assert raw_units == original_units and optical == original_optical
    assert len(report['local_ocr_units']) == 1
    owner = report['local_ocr_units'][0]
    compiled = report['blocks'][0]
    assert owner['id'] == compiled['id'] == ident
    assert [word['text'] for word in owner['words']] == compiled['published_tokens'] == [
        word['text'] for word in optical]
    assert [word['bbox'] for word in owner['words']] == [word['bbox'] for word in optical]
    assert owner['source_word_indices'] == list(range(len(optical)))
    assert owner['source_method'] and 'pixel' in owner['source_method']
    assert all(word.get('confidence') is None for word in owner['words'])
    assert owner.get('mean_confidence') is None, 'an independent witness cannot inherit a raw reading score'
    assert compiled['marked'] == list(range(len(optical)))
    guard = 'omitted_pixel_line_recovery' if route == 'recovered' else 'independent_pixel_line_fallback'
    assert any(item['kind'] == guard for item in compiled['guard'])
    from tests.unit.test_reflow_reading_tiers import assert_flagged_print
    root,flags=assert_flagged_print(''.join(page['body']), [w['text']for w in optical])
    uncertain=[n for n in root.iter()if n.get('class')=='uncertain']
    assert uncertain and all(n.get('title')=='Unverified local OCR reading'for n in uncertain)
    assert sum(len(''.join(n.itertext()).replace('†','').split())for n in uncertain)==len(optical)
    detail = images[f'images/v4-d-0-{ident}-{len(optical)-1}.jpg']
    assert detail['page'] == 0
    box = detail['bbox']
    assert all(box[0] <= (word['bbox'][0]+word['bbox'][2])/2 <= box[2]
               and box[1] <= (word['bbox'][1]+word['bbox'][3])/2 <= box[3]
               for word in optical)
    assert 0 <= box[0] < box[2] <= ink.W and 0 <= box[1] < box[3] <= ink.H
    assert box[3]-box[1] < 50


@pytest.mark.parametrize('damage', ['neither_inventory_exact', 'duplicate_raw_owner', 'duplicate_optical_occurrence'])
def test_local_compilation_rejects_unproved_final_occurrence_inventory_before_detail_emission(damage):
    doc, display, optical, package = _local_compilation_fixture()
    raw_words = [dict(copy.deepcopy(word), confidence=99) for word in optical]
    ident = 'ocr0'
    forced = []
    if damage == 'neither_inventory_exact':
        raw_words[0]['text'] = 'A'
        text = 'One ' + ' '.join(word['text'] for word in optical[1:])
    elif damage == 'duplicate_raw_owner':
        text = ' '.join(word['text'] for word in raw_words)
    else:
        ident = 'recovered-synthetic'
        optical.insert(1, optical[0])
        text = ' '.join(word['text'] for word in optical)
        forced = [ident]
    units = [dict(id=ident, words=raw_words, mean_confidence=99, low_confidence_tokens=[])]
    if damage == 'duplicate_raw_owner':
        units.append(copy.deepcopy(units[0]))
    elif damage == 'duplicate_optical_occurrence':
        units = []
    original_units = copy.deepcopy(units)
    block = ink.model_block(text, ident, 'body', [20, 40, 280, 75])
    block['low_confidence'] = list(range(len(text.split())))
    images = {}
    try:
        with pytest.raises(vision_pipeline._DisclosureQualityRejection):
            vision_pipeline.compile_page({'blocks': [block]}, [], display,
                0, package, images, pixel_words=optical, local_ocr=True,
                local_ocr_units=units, forced_uncertain=forced)
    finally:
        doc.close()
    assert units == original_units
    assert not any('v4-d-0-' in name for name in images)
    assert not any('v4-d-0-' in name for name in package.images)


def test_local_reading_canonicalizes_only_token_boundary_whitespace_without_mutating_recognition(tmp_path, monkeypatch):
    doc, display, _, _ = _local_compilation_fixture()
    tokens = [' bridge', 'garden.\t', '(quiet)']
    words = [dict(text=text, bbox=[20+index*55, 45, 65+index*55, 57],
                  confidence=96-index, paragraph=[0, 1, 1], line=1)
             for index, text in enumerate(tokens)]
    original = copy.deepcopy(words)
    receipt = dict(cached=False, image='synthetic-token-boundary-raster')
    original_receipt = copy.deepcopy(receipt)
    monkeypatch.setattr(local_reading, 'recognize', lambda *args: (words, receipt))
    try:
        reading = local_reading.read(display, tmp_path, 'Synthetic unavailable reading')
    finally:
        doc.close()
    assert words == original and receipt == original_receipt
    assert reading['answer']['blocks'][0]['text'] == 'bridge garden. (quiet)'
    owned = reading['local_ocr_units'][0]['words']
    assert [word['text'] for word in owned] == ['bridge', 'garden.', '(quiet)']
    assert [word['bbox'] for word in owned] == [word['bbox'] for word in original]
    assert [word['confidence'] for word in owned] == [word['confidence'] for word in original]
    for index in (0, 1):
        assert owned[index].get('recognition_text', owned[index].get('original_text')) == tokens[index]


@pytest.mark.parametrize('text', ['bridge garden', 'bridge\tgarden', 'bridge\ngarden', ' \t '])
def test_local_reading_rejects_non_atomic_recognizer_word_without_changing_receipt(text, tmp_path, monkeypatch):
    from cps.services.reflow import ocr
    doc, display, _, _ = _local_compilation_fixture()
    words = [dict(text=text, bbox=[20, 45, 65, 57], confidence=96,
                  paragraph=[0, 1, 1], line=1)]
    receipt = dict(cached=False, image='synthetic-non-atomic-token-raster')
    original, original_receipt = copy.deepcopy(words), copy.deepcopy(receipt)
    monkeypatch.setattr(local_reading, 'recognize', lambda *args: (words, receipt))
    try:
        with pytest.raises((ValueError, ocr.OCRFailed)):
            local_reading.read(display, tmp_path, 'Synthetic unavailable reading')
    finally:
        doc.close()
    assert words == original and receipt == original_receipt
