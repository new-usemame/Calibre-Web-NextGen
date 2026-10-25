"""Synthetic unit failures retain independently readable neighbours."""
import copy
from types import SimpleNamespace

import pytest

from cps.services.reflow import unit_recovery as ur, vision_pipeline as vp
from tests.unit import test_round4_defects as ink

pytestmark = pytest.mark.unit


def test_recovered_omission_keeps_its_geometry_owner_and_safe_sibling(tmp_path, monkeypatch):
    page = ink.InkPage()
    page.text('The clear sample remains readable.', 'healthy', y=45)
    omitted = page.text(['The second printed sample remains clear',
                         'while the soft light crosses the panel.'], 'omitted', y=145, size=8)
    page.blocks.remove(omitted)
    align = vp.vision_reading.align
    def rejected_recovery(text, *args, **kwargs):
        result = align(text, *args, **kwargs)
        if 'second printed sample' in text:
            result.update(tokens=['zzqv', 'rrjx', 'ppqx'], marked={0, 1, 2}, fallback=True)
        return result
    monkeypatch.setattr(vp.vision_reading, 'align', rejected_recovery)
    _, report, docs = ink.convert_ink(tmp_path, monkeypatch, [page])
    assert report['pages'][0]['source'] == 'vision'
    assert 'The clear sample remains readable.' in ' '.join(ink.plain(root) for root in docs.values())
    units = report['pages'][0]['degraded_units']
    assert len(units) == 1 and units[0]['block'].startswith('recovered-')
    assert units[0]['source_image'] and units[0]['source_reference']
    assert not any(word in ' '.join(ink.plain(root) for root in docs.values()) for word in ['zzqv', 'rrjx', 'ppqx'])


def test_whole_page_supersedes_stale_unit_without_erasing_its_history(tmp_path):
    controller = ur.Controller(SimpleNamespace(key=None), [0], tmp_path)
    controller.recover(ur.UnitFailure([dict(page=0, block='failed')], 'synthetic_unit', ValueError('First unit failed.')))
    controller.recover(ur.UnitFailure([dict(page=0, block=None)], 'synthetic_page', ValueError('Page ownership is unproved.')))
    assert set(controller.degraded) == {(0, None)}
    assert controller.events[0]['units'] == [dict(page=0, block='failed')]
    assert controller.events[-1]['source_page_escalations'][0]['previous_units'][0]['block'] == 'failed'


def test_initial_quality_failure_discloses_only_the_bad_unit(tmp_path, monkeypatch):
    page = ink.InkPage()
    page.text('The clear sample remains readable.', 'healthy', y=45)
    page.text(['qVrTx mNpRs wXyZq aBcDe fGhIj kLmNo',
               'pQrSt uVwXy zAbCd eFgHi jKlMn oPqRs'], 'damaged', y=145, size=7)
    _, report, docs = ink.convert_ink(tmp_path, monkeypatch, [page])
    assert report['pages'][0]['source'] == 'vision'
    assert 'The clear sample remains readable.' in ' '.join(ink.plain(root) for root in docs.values())
    units = report['pages'][0]['degraded_units']
    assert {unit['block'] for unit in units} == {'damaged'}
    assert units[0]['source_image'] and units[0]['source_reference']


def test_joined_quality_failure_retains_only_its_fragment_owner(tmp_path):
    controller = ur.Controller(SimpleNamespace(key=None), [0], tmp_path)
    healthy = '<p><span data-source-page="0" data-source-block="healthy">The clear sample remains readable.</span></p>'
    damaged = '<p><span data-source-page="0" data-source-block="damaged">qVrTx mNpRs wXyZq aBcDe fGhIj kLmNo pQrSt uVwXy zAbCd eFgHi jKlMn oPqRs</span></p>'
    page = dict(pno=0, anchor='<span id="pg_0000"></span>', body=[healthy, damaged], asides=[])
    record = dict(source='vision', blocks=[dict(id='healthy', role='body'), dict(id='damaged', role='body')])
    controller.records = {0: record}
    controller.register_page(page, record)
    with ur.activated(controller), pytest.raises(ur.UnitFailure) as failure:
        vp._compiled_text_quality([page], controller.records, [0])
    assert failure.value.units == [dict(page=0, block='damaged')]


def test_post_join_image_alt_is_gated_at_its_source_unit(tmp_path):
    controller = ur.Controller(SimpleNamespace(key=None), [0], tmp_path)
    healthy = '<p><span data-source-page="0" data-source-block="healthy">The clear sample remains readable.</span></p>'
    damaged = '<figure id="art"><img src="images/sample.png" alt="zzqv rrjx ppqx"/></figure>'
    page = dict(pno=0, anchor='<span id="pg_0000"></span>', body=[healthy, damaged], asides=[])
    record = dict(source='vision', blocks=[dict(id='healthy', role='body'), dict(id='art', role='figure')])
    controller.records = {0: record}
    controller.register_page(page, record)
    with ur.activated(controller), pytest.raises(ur.UnitFailure) as failure:
        vp._compiled_text_quality([page], controller.records, [0])
    assert failure.value.units == [dict(page=0, block='art')]


def test_hidden_script_does_not_discard_the_visible_latin_reading(tmp_path, monkeypatch):
    import pymupdf
    from cps.services.reflow import vision_pixels
    from tests.unit.test_reflow_text_quality import SyntheticReader
    from tests.unit.test_reflow_vision_pilot import answer, block
    text = 'The clear sample remains readable.'
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=300)
        page.insert_text((30, 60), text)
        page.insert_font(fontname='alphabet', fontbuffer=pymupdf.Font('helv').buffer)
        page.insert_text((30, 100), 'αλφαβήτα γαμμαδέλτα', fontname='alphabet', render_mode=3)
        assert 'αλφα' in page.get_text('text')
        optical = [dict(text=w[4], bbox=list(w[:4])) for w in page.get_text('words') if w[1] < 70]
        monkeypatch.setattr(vp, 'clean_native', lambda *args: False)
        monkeypatch.setattr(vision_pixels, 'words', lambda *args: copy.deepcopy(optical))
        reader = SyntheticReader(answer(block(text, bbox=(0, 0, 1000, 1000))))
        report = vp.convert(doc, tmp_path/'visible.epub', reader)
    assert report['pages'][0]['source'] == 'vision'
    assert not any(unit.get('block') is None for unit in report.get('degraded_units', []))
    assert report['pages'][0]['words'] == len(text.split())


def test_joined_note_rejection_keeps_clean_original_and_body_units(tmp_path):
    controller = ur.Controller(SimpleNamespace(key=None), [0, 1], tmp_path)
    aside = ('<aside id="v4-note-0-noteA" class="footnote"><p>'
             '<span data-source-page="0" data-source-block="noteA">The clear sample remains readable.</span></p><p>'
             '<span data-source-page="1" data-source-block="noteB">qVrTx mNpRs wXyZq aBcDe fGhIj kLmNo pQrSt uVwXy zAbCd eFgHi jKlMn oPqRs</span></p></aside>')
    pages = [dict(pno=0, anchor='<span id="pg_0000"></span>', body=[], asides=[aside]),
             dict(pno=1, anchor='<span id="pg_0001"></span>', body=['<p><span data-source-page="1" data-source-block="healthy">The second clear sample remains readable.</span></p>'], asides=[])]
    records = {0: dict(source='vision', blocks=[dict(id='noteA', role='footnote')]),
               1: dict(source='vision', blocks=[dict(id='noteB', role='footnote'), dict(id='healthy', role='body')])}
    controller.records = records
    for page in pages:
        controller.register_page(page, records[page['pno']])
    with ur.activated(controller), pytest.raises(ur.UnitFailure) as failure:
        vp._compiled_text_quality(pages, records, [0, 1])
    assert failure.value.units == [dict(page=1, block='noteB')]


def test_rejected_graphic_wording_cannot_demand_a_removed_note_owner(tmp_path):
    from cps.services.reflow import vision_reading
    controller = ur.Controller(SimpleNamespace(key=None), [0], tmp_path)
    template = dict(marker='', low_confidence=[], continues_previous=False, continues_next=False, bbox=[0, 0, 1000, 1000])
    raw = dict(blocks=[dict(template, id='graphic', role='table', text='A clear row <n:note>'),
                       dict(template, id='note', role='footnote', text='A short printed note.'),
                       dict(template, id='healthy', role='body', text='The clear sample remains readable.')])
    controller.answers[0] = copy.deepcopy(raw)
    controller.degraded = {(0, ident): dict(page=0, block=ident) for ident in ['graphic', 'note']}
    selected = controller.filter_answer(0, raw)
    vision_reading.validate(selected)
    assert next(block['text'] for block in selected['blocks'] if block['id'] == 'healthy') == raw['blocks'][2]['text']
    assert next(block['text'] for block in selected['blocks'] if block['id'] == 'graphic') == 'Original source image'
    assert controller.answers[0] == raw


def test_bad_joined_paragraph_tail_does_not_discard_its_clean_source_page(tmp_path):
    controller = ur.Controller(SimpleNamespace(key=None), [0, 1], tmp_path)
    clean = 'The clear sample remains readable. ' * 5
    damaged = 'qVrTx mNpRs wXyZq aBcDe fGhIj kLmNo pQrSt uVwXy zAbCd eFgHi jKlMn oPqRs'
    fragment = ('<p><span data-source-page="0" data-source-block="good">'+clean+'</span>'
                '<span id="pg_0001"></span><span data-source-page="1" data-source-block="bad">'+damaged+'</span></p>')
    pages = [dict(pno=0, anchor='<span id="pg_0000"></span>', body=[fragment], asides=[]),
             dict(pno=1, anchor='', body=[], asides=[])]
    records = {0: dict(source='vision', blocks=[dict(id='good', role='body')]),
               1: dict(source='vision', blocks=[dict(id='bad', role='body')])}
    controller.records = records
    for page in pages:
        controller.register_page(page, records[page['pno']])
    with ur.activated(controller), pytest.raises(ur.UnitFailure) as failure:
        vp._compiled_text_quality(pages, records, [0, 1])
    assert failure.value.units == [dict(page=1, block=None)]


def test_rejected_table_retains_its_admitted_geometry_and_safe_heading(tmp_path):
    page = ink.InkPage()
    page.text('A clear sample heading', 'heading', role='heading', y=45)
    graphic = page.text('A clear sample row', 'graphic', role='table', y=145)
    graphic['bbox'] = [-5, -5, -1, -1]
    _, words, raw = page.finish()
    display = SimpleNamespace(rect=ink.pymupdf.Rect(0, 0, ink.W, ink.H))
    admitted, _ = vp.bind_geometry(copy.deepcopy(raw), words, display)
    admitted_box = next(b['bbox'] for b in admitted['blocks'] if b['id'] == 'graphic')
    assert admitted_box != graphic['bbox']
    controller = ur.Controller(SimpleNamespace(key=None), [0], tmp_path)
    controller.answers[0] = copy.deepcopy(raw)
    controller.admit_answer(0, admitted)
    saved = copy.deepcopy(controller.admitted_answers)
    controller.degraded[(0, 'graphic')] = dict(page=0, block='graphic')
    selected = controller.filter_answer(0, raw)
    assert next(b['bbox'] for b in selected['blocks'] if b['id'] == 'graphic') == admitted_box
    rebound, _ = vp.bind_geometry(selected, words, display)
    assert next(b['text'] for b in rebound['blocks'] if b['id'] == 'heading') == 'A clear sample heading'
    assert next(b['text'] for b in rebound['blocks'] if b['id'] == 'graphic') == 'Original source image'
    assert controller.admitted_answers == saved
    assert controller.answers[0] == raw
    controller.admitted_answers.clear()
    unproved = controller.filter_answer(0, raw)
    assert next(b['bbox'] for b in unproved['blocks'] if b['id'] == 'graphic') == graphic['bbox']
    with pytest.raises(ValueError, match='cannot be bound to printed pixels'):
        vp.bind_geometry(unproved, words, display)


@pytest.mark.parametrize('side', ['left', 'right'])
@pytest.mark.parametrize('defect', ['disclosed', 'unmapped'])
def test_leading_art_cannot_join_across_a_disclosed_or_unmapped_body(side, defect):
    left = dict(id='previous-tail', role='body', source_regions=[[20, 500, 300, 550]], continues_next=True)
    barrier = dict(id='barrier', role='body', source_regions=[[20, 200, 300, 250]])
    selected = left if side == 'left' else barrier
    if defect == 'disclosed':
        selected['degraded_source_context'] = True
    else:
        selected['source_regions'] = []
    after = [dict(id='art', role='figure', degraded_source_context=True),
             dict(id='caption', role='caption'), barrier,
             dict(id='healthy', role='body', source_regions=[[20, 400, 300, 500]], continues_previous=False)]
    current = dict(body=['<figure>Original source image</figure>',
                         '<p class="caption">A clear caption.</p>',
                         '<p>A retained sample.</p>', '<p>A clear sample remains readable.</p>'])
    before = [left]
    saved = copy.deepcopy((before, after, current))
    assert vp._leading_art_continuation(dict(blocks=before), current, before, after,
                                       '<p>A clear previous sample continues</p>', after) is None
    assert (before, after, current) == saved


def _compile_ownership_sample(tmp_path, rows, blocks, regions=None, bind=False):
    """Procedural source words and geometry written for these ownership tests."""
    import pymupdf
    from cps.services.reflow.source_display import SourceDisplay
    with pymupdf.open() as doc:
        source = doc.new_page(width=300, height=400)
        for text, y in rows:
            source.insert_text((30, y), text, fontsize=9)
        optical = [dict(text=w[4], bbox=list(w[:4])) for w in source.get_text('words', sort=True)]
        display = SourceDisplay(doc, 0)
        answer = dict(blocks=copy.deepcopy(blocks))
        regions = copy.deepcopy(regions or {})
        owners = {}
        if bind:
            answer, _ = vp.bind_geometry(answer, optical, display, regions, owners)
        controller = ur.Controller(SimpleNamespace(key=None), [0], tmp_path)
        controller.admit_answer(0, answer)
        package = SimpleNamespace(image=lambda *args: None)
        with ur.activated(controller):
            try:
                return vp.compile_page(answer, [], display, 0, package, {},
                                       pixel_words=optical, regions=regions, body_owners=owners)
            except ur.UnitFailure as error:
                # Recompile only the surviving units to prove the failure is local.
                controller.recover(error)
                remaining = controller.filter_answer(0, answer)
                error.surviving_page, error.surviving_record = vp.compile_page(
                    remaining, [], display, 0, package, {}, pixel_words=optical,
                    regions=regions, body_owners=owners)
                raise


def _ownership_block(text, ident, rect, role='body'):
    return dict(id=ident, role=role, text=text, marker='',
                bbox=[rect[0]/300*1000, rect[1]/400*1000,
                      rect[2]/300*1000, rect[3]/400*1000],
                low_confidence=[], continues_previous=False, continues_next=False)


def test_displaced_heading_cannot_publish_a_neighbouring_optical_line(tmp_path):
    title = 'A CLEAR SAMPLE HEADING'
    neighbour = 'The clear sample remains readable near the open window.'
    healthy = 'Every healthy source unit remains readable.'
    blocks = [_ownership_block(title, 'title', [29, 72, 285, 105], 'heading'),
              _ownership_block(healthy, 'healthy', [29, 145, 285, 165])]
    with pytest.raises(ur.UnitFailure) as caught:
        _compile_ownership_sample(tmp_path,
            [('A CLEAR', 60), ('SAMPLE HEADING', 80), (neighbour, 100), (healthy, 160)],
            blocks, bind=True)
    assert {(unit['page'], unit['block']) for unit in caught.value.units} == {(0, 'title')}
    assert all('optical heading' in unit['source_failure_reason'] for unit in caught.value.units)
    assert [b['id'] for b in caught.value.surviving_record['blocks']] == ['healthy']
    assert healthy in vp._seam_text(' '.join(caught.value.surviving_page['body']))


def test_exact_optical_heading_keeps_its_authored_words(tmp_path):
    title = 'A CLEAR SAMPLE HEADING'
    page, record = _compile_ownership_sample(tmp_path,
        [('A CLEAR', 60), ('SAMPLE HEADING', 80)],
        [_ownership_block(title, 'title', [29, 45, 285, 85], 'heading')], bind=True)
    assert vp._seam_text(' '.join(page['body'])) == title
    assert record['blocks'][0]['published_tokens'] == title.split()
    assert not record['blocks'][0]['fallback']


@pytest.mark.parametrize('second_fallback', [False, True])
def test_optical_fallback_cannot_republish_another_units_source_instance(tmp_path, second_fallback):
    first = 'A clear sample heading'
    second = 'The clear sample remains readable near the open window.'
    blocks = [_ownership_block(first, 'first', [29, 45, 285, 105]),
              _ownership_block('Readable' if second_fallback else second,
                               'second', [29, 85, 285, 105])]
    regions = {'first': [[29, 45, 285, 105]], 'second': [[29, 85, 285, 105]]}
    with pytest.raises(ur.UnitFailure) as caught:
        _compile_ownership_sample(tmp_path, [(first, 60), (second, 100)], blocks, regions)
    expected = {'first', 'second'} if second_fallback else {'first'}
    assert {unit['block'] for unit in caught.value.units} == expected
    assert all(unit['page'] == 0 for unit in caught.value.units)
    if not second_fallback:
        assert vp._seam_text(' '.join(caught.value.surviving_page['body'])) == second
        assert [b['id'] for b in caught.value.surviving_record['blocks']] == ['second']


def test_equal_fallback_words_on_distinct_source_instances_remain_distinct(tmp_path):
    text = 'The clear sample remains readable near the open window.'
    blocks = [_ownership_block('Readable', name, [29, y-15, 285, y+5])
              for name, y in [('first', 60), ('second', 160)]]
    page, record = _compile_ownership_sample(tmp_path, [(text, 60), (text, 160)], blocks)
    assert len(record['blocks']) == 2
    assert all(block['fallback'] for block in record['blocks'])
    assert all(block['published_tokens'] == text.split() for block in record['blocks'])
    assert vp._seam_text(' '.join(page['body'])).replace('†', '') == text+' '+text


@pytest.mark.parametrize('edge', ['first', 'last'])
def test_displaced_numeric_boundary_cannot_span_another_source_unit(tmp_path, edge):
    import pymupdf
    from cps.services.reflow.source_display import SourceDisplay
    lines = ['The clear sample remains readable near',
             'the open window while the soft light',
             'crosses the wooden panel beside the door.']
    text = ('2. ' if edge == 'first' else '')+' '.join(lines)+(' 2.' if edge == 'last' else '')
    with pymupdf.open() as doc:
        source = doc.new_page(width=300, height=400)
        source.insert_text((30, 40 if edge == 'first' else 260), '2.', fontsize=9)
        source.insert_text((30, 90 if edge == 'first' else 210), 'A separate clear heading', fontsize=9)
        for line, y in zip(lines, [130, 148, 166]):
            source.insert_text((30, y), line, fontsize=9)
        optical = [dict(text=w[4], bbox=list(w[:4])) for w in source.get_text('words', sort=True)]
        # The correct numeric boundary is unrecognized; a displaced hint covers
        # only the reading's final or initial two rows. Another number is exact.
        hint = [29, 155, 285, 170] if edge == 'first' else [29, 115, 285, 135]
        display = SourceDisplay(doc, 0)
        regions, owners = {}, {}
        bound, repairs = vp.bind_geometry(dict(blocks=[_ownership_block(text, 'reading', hint)]),
                                          optical, display, regions, owners)
        box = vp.block_rect(bound['blocks'][0], display)
        assert 110 < box[1] < box[3] < 180
        assert bound['blocks'][0]['text'] == text
        position = 0 if edge == 'first' else len(text.split())-1
        assert position in bound['blocks'][0]['low_confidence']
        controller = ur.Controller(SimpleNamespace(key=None), [0], tmp_path)
        controller.admit_answer(0, bound)
        with ur.activated(controller):
            page, record = vp.compile_page(bound, [], display, 0,
                SimpleNamespace(image=lambda *args: None), {}, pixel_words=optical,
                regions=regions, body_owners=owners)
        assert record['blocks'][0]['published_tokens'] == text.split()
        assert position in record['blocks'][0]['marked']
        assert 'separate clear heading' not in vp._seam_text(' '.join(page['body']))


def test_adjacent_numeric_body_boundary_keeps_its_exact_physical_instance(tmp_path):
    text = '2. The clear sample remains readable near the open window.'
    page, record = _compile_ownership_sample(tmp_path, [(text, 130)],
        [_ownership_block(text, 'reading', [29, 115, 285, 140])], bind=True)
    reading = record['blocks'][0]
    assert reading['published_tokens'] == text.split()
    assert reading['physical_head']['text'] == '2.'
    assert reading['marked'] == []
    assert vp._seam_text(' '.join(page['body'])) == text


@pytest.mark.parametrize('incoming', ['empty', 'conflicting'])
def test_rejected_exact_source_instance_stays_reserved_across_retries(tmp_path, incoming):
    controller = ur.Controller(SimpleNamespace(key=None), [0], tmp_path)
    original = ('sample', (20, 40, 50, 50))
    distinct = ('sample', (20, 140, 50, 150))
    controller.register_regions(0, {'failed': [[20, 40, 50, 50]],
                                    '_instances': {original: 'failed'}})
    controller.degraded[(0, 'failed')] = dict(page=0, block='failed')
    for _ in range(2):
        claims = {} if incoming == 'empty' else {original: 'live'}
        claims[distinct] = 'live'
        controller.register_regions(0, {'_instances': claims})
        coverage = controller.coverage_regions(0, {'_instances': {original: 'live', distinct: 'live'}})
        assert coverage['_instances'][original] == 'failed'
        assert coverage['_instances'][distinct] == 'live'
        assert controller.regions[0]['_instances'][original] == 'failed'
    assert controller.regions[0]['failed'] == [[20, 40, 50, 50]]


def test_successful_cached_reread_rebuilds_exact_source_ownership(tmp_path):
    replacement = dict(answer=dict(blocks=[_ownership_block('The clear sample remains readable.',
        'reading', [29, 115, 285, 140])]), config_sha256='new-reading')
    reader = SimpleNamespace(key=None, cached_reread=lambda *args: copy.deepcopy(replacement))
    controller = ur.Controller(reader, [0], tmp_path)
    image = tmp_path/'source.jpg'
    image.write_bytes(b'synthetic source identity')
    controller.images[0] = dict(path=str(image), sha256='synthetic-source')
    controller.readings[0] = dict(config_sha256='old-reading')
    controller.regions[0] = {'_instances': {('sample', (20, 40, 50, 50)): 'reading'}}
    controller.omission_coverage[0] = {'reading': [[20, 40, 50, 50]]}
    controller.admitted_answers[0] = copy.deepcopy(replacement['answer'])
    controller.prepared_pages[0] = ('old-preparation', tmp_path/'old.pickle')
    controller.recover(ur.UnitFailure([dict(page=0, block='reading')],
        'synthetic_reading', ValueError('A synthetic source unit needs another reading.')))
    assert controller.readings[0] == replacement
    assert 0 not in controller.regions
    assert 0 not in controller.admitted_answers
    assert 0 not in controller.omission_coverage
    assert 0 not in controller.prepared_pages
    assert controller.events[-1]['transition'] == 'TARGETED_REREAD_REASSEMBLE'


@pytest.mark.parametrize('stage', ['publication_text_quality', 'compiled_publication_text_quality'])
@pytest.mark.parametrize('repeat_first', [False, True])
def test_quality_batch_makes_unit_progress_before_retrying_an_existing_disclosure(tmp_path, stage, repeat_first):
    controller = ur.Controller(SimpleNamespace(key=None), [0], tmp_path)
    controller.answers[0] = dict(blocks=[dict(id=bid, role='body', text='The printed row is clear.')
                                        for bid in ['healthy', 'earlier', 'damaged']])
    controller.recover(ur.UnitFailure([dict(page=0, block='earlier')], 'page_compilation',
                                    ValueError('The first reading needs source disclosure.')))
    units = [dict(page=0, block=bid) for bid in
             (['earlier', 'damaged'] if repeat_first else ['damaged', 'earlier'])]
    controller.recover(ur.UnitFailure(units, stage, ValueError('Joined reading has unresolved text quality')))
    assert not controller.source_pages()
    assert set(controller.blocked(0)) == {'earlier', 'damaged'}
    assert controller.degraded[(0, 'earlier')]['stage'] == 'page_compilation'
    assert controller.events[-1]['deferred_source_unit_rechecks'] == [dict(page=0, block='earlier')]
    # A new assembly must check the original disclosure again. If it still
    # fails without another rejected unit to remove, escalation remains bounded.
    controller.recover(ur.UnitFailure([dict(page=0, block='earlier')], stage,
                                    ValueError('Original source presentation still fails.')))
    assert controller.source_pages() == {0}
    assert set(controller.degraded) == {(0, None)}


def test_quality_progress_on_another_page_does_not_defer_a_failing_source_unit(tmp_path):
    controller = ur.Controller(SimpleNamespace(key=None), [0, 1], tmp_path)
    controller.recover(ur.UnitFailure([dict(page=0, block='earlier')], 'page_compilation',
                                    ValueError('The first reading needs source disclosure.')))
    controller.recover(ur.UnitFailure([dict(page=0, block='earlier'), dict(page=1, block='damaged')],
                                    'publication_text_quality', ValueError('Original source presentation fails.')))
    assert controller.source_pages() == {0}
    assert set(controller.blocked(1)) == {'damaged'}


def test_a_disclosed_row_keeps_its_previous_omission_coverage(tmp_path, monkeypatch):
    page = ink.InkPage()
    page.text('The clear sample remains readable.', 'healthy', y=45)
    original = 'Abacus guide .................. 11, 12, 41, 52, 67, 84'
    page.text(original, 'rejected', y=130)
    omitted = page.text(['The separate amber panel remains readable',
                         'while soft light crosses the clear window.'], 'omitted', y=220)
    page.blocks.remove(omitted)
    finish = page.finish
    def optical_dots():
        image, words, answer = finish()
        dots = next(word for word in words if word['text'] == '..................')
        at = words.index(dots); left, top, right, bottom = dots['bbox']
        width = (right-left)/3
        words[at:at+1] = [dict(text=text, bbox=[left+i*width, top, left+(i+1)*width, bottom], confidence=0)
                          for i, text in enumerate(['6...', 'eee', 'eee'])]
        return image, words, answer
    monkeypatch.setattr(page, 'finish', optical_dots)
    # A valid model rectangle can retain its default omission footprint without
    # earning relocated geometry or exact lexical-source-instance claims.
    monkeypatch.setattr(vp, 'bind_geometry', lambda answer, *args: (copy.deepcopy(answer), []))
    compile_page = vp.compile_page
    def reject_original(answer, words, display, pno, *args, **kwargs):
        if any(block['id'] == 'rejected' for block in answer['blocks']):
            raise ur.UnitFailure([dict(page=pno, block='rejected')], 'page_compilation',
                                 ValueError('Synthetic original reading rejection'))
        return compile_page(answer, words, display, pno, *args, **kwargs)
    monkeypatch.setattr(vp, 'compile_page', reject_original)
    _, report, docs = ink.convert_ink(tmp_path, monkeypatch, [page])
    record = report['pages'][0]
    assert record['source'] == 'vision', repr(report['unit_recovery_events'])
    text = ' '.join(ink.plain(root) for root in docs.values())
    assert 'The clear sample remains readable.' in text
    assert 'The separate amber panel remains readable' in text
    assert 'while soft light crosses the clear window.' in text
    assert 'eee' not in text and '6...' not in text
    assert {unit['block'] for unit in record['degraded_units']} == {'rejected'}
    assert record['degraded_units'][0]['source_image'] and record['degraded_units'][0]['source_reference']


def test_disclosed_omission_coverage_stays_separate_from_source_proof(tmp_path):
    controller = ur.Controller(SimpleNamespace(key=None), [0], tmp_path)
    boxes = [[20, 40, 50, 50], [20, 140, 50, 150]]
    controller.register_regions(0, {}, {'failed': boxes})
    boxes[0][0] = 999
    controller.degraded[(0, 'failed')] = dict(page=0, block='failed')
    controller.register_regions(0, {}, {'failed': [[0, 0, 300, 300]], 'live': [[80, 80, 100, 90]]})
    coverage = controller.coverage_regions(0, {})
    assert coverage['_degraded_territories'] == [[20, 40, 50, 50], [20, 140, 50, 150]]
    assert not controller.regions[0].get('failed')
    assert not coverage.get('_instances')
    assert 'failed' not in coverage
    assert controller.omission_coverage[0]['live'] == [[80, 80, 100, 90]]


def test_dot_leader_ink_cannot_be_substituted_as_dictionary_words(tmp_path, monkeypatch):
    page = ink.InkPage()
    page.text('The clear sample remains readable.', 'healthy', y=45)
    page.text('Abacus sample .................. 11, 12, 41', 'leader', y=130)
    finish = page.finish
    def optical_dots():
        image, words, answer = finish()
        dot = next(word for word in words if word['text'] == '..................')
        at = words.index(dot); left, _, right, _ = dot['bbox']
        # The printed dots occupy only the baseline. The injected OCR readings
        # are ordinary dictionary words, so spelling checks cannot reject them.
        words[at:at+1] = [dict(text=text, bbox=[left+i*(right-left)/3, 128.7,
            left+(i+1)*(right-left)/3, 130.3], confidence=0)
            for i, text in enumerate(['see', 'ease', 'use'])]
        return image, words, answer
    monkeypatch.setattr(page, 'finish', optical_dots)
    align = vp.vision_reading.align
    def substitute(text, words, *args, **kwargs):
        result = align(text, words, *args, **kwargs)
        if any(word['text'] == 'ease' for word in words):
            result.update(tokens=[word['text'] for word in words],
                          boxes=[word['bbox'] for word in words],
                          marked=set(range(len(words))), fallback=True)
        return result
    monkeypatch.setattr(vp.vision_reading, 'align', substitute)
    _, report, docs = ink.convert_ink(tmp_path, monkeypatch, [page])
    record = report['pages'][0]
    assert record['source'] == 'vision'
    assert 'The clear sample remains readable.' in ' '.join(ink.plain(root) for root in docs.values())
    assert {unit['block'] for unit in record.get('degraded_units', [])} == {'leader'}
    assert record['degraded_units'][0]['source_image']
    assert not any('ease' in block.get('published_tokens', []) for block in record['blocks'])


@pytest.mark.parametrize('size,y,anchor_size', [(9, 130, 9), (1, 130, 9),
                                                (3, 120, 9), (3, 130, 9),
                                                (3, 140, 9), (1, 130, 1)])
def test_real_repeated_letters_and_small_text_are_not_dot_leaders(size, y, anchor_size):
    from cps.services.reflow import source_display
    page = ink.InkPage()
    page.text('Abacus sample', 'anchor', y=130, size=anchor_size)
    page.page.insert_text((120, y), 'eeeeeeeeeeeeeeeeeeee oooooooooooooooooooo', fontsize=size)
    words = [dict(text=word[4], bbox=list(word[:4])) for word in page.page.get_text('words')]
    try:
        assert not vp._unproved_optical_letter_clusters('Abacus sample', words,
            source_display.SourceDisplay(page.doc, 0))
    finally:
        page.doc.close()
