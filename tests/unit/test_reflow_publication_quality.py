"""Synthetic publication-text controls with independently authored word lists."""
import pytest

from cps.services.reflow import text_quality as q

pytestmark = pytest.mark.unit
CLEAR = 'Green baskets contain ripe apples while purple carts carry fresh bread'
LEXICON = frozenset(CLEAR.lower().split()) | {'container', 'paintwork', 'threading'}


def convergent_damage():
    # Forty words; three recognized spellings have repeated internal case flips.
    known = ('coNtAiner paInTwork thReAding ' + ' '.join(CLEAR.split() * 3)).split()[:30]
    unknown = ['zqx' + chr(97 + n) for n in range(10)]
    return ' '.join(known + unknown)


def test_convergent_subthreshold_signals_reject_a_damaged_reading():
    metric = q.inspect(convergent_damage(), lexicon=LEXICON)
    assert metric['latin_words'] == 40
    assert metric['dictionary_miss_rate'] == .25
    assert metric['case_flip_rate'] == .075
    assert metric['worst_window_miss_rate'] == .625
    assert metric['severe_case_words'] == 0
    assert metric['needs_read']
    assert 'combined_case_dictionary_window' in metric['reasons']
    assert q.inspect_disclosure(convergent_damage(), lexicon=LEXICON)['needs_read']


def test_recognized_case_variants_and_unfamiliar_normal_case_are_independent_controls():
    assert not q.inspect(' '.join(CLEAR.split() * 5), lexicon=LEXICON)['needs_read']
    assert not q.inspect(convergent_damage().lower(), lexicon=LEXICON)['needs_read']
    assert not q.inspect(convergent_damage(), lexicon=LEXICON | {'zqx' + chr(97+n) for n in range(10)})['needs_read']
    assert not q.inspect('BrightForms maker joins CedarWorks today', lexicon={'maker','joins','today'})['needs_read']


def test_partitioned_failure_preserves_an_unrelated_clean_unit():
    metric = q.inspect_partitioned([('clear', CLEAR), ('damaged', convergent_damage())], lexicon=LEXICON)
    assert metric['needs_read']
    assert metric['rejected_units'] == ['damaged']
    assert not metric['units']['clear']['needs_read']
    assert metric['units']['damaged']['needs_read']


def test_repeated_owner_does_not_reorder_a_cross_unit_window():
    unknown = ['zqx' + chr(97+n) for n in range(12)]
    units = [('left', ' '.join(unknown[:6])), ('middle', ' '.join(unknown[6:])), ('left', CLEAR)]
    metric = q.inspect_partitioned(units, lexicon=LEXICON)
    assert metric['needs_read']
    assert metric['rejected_units'] == ['left', 'middle']


def test_window_crossing_units_is_attributed_to_its_misses_only():
    words = ['zqx' + chr(97+n) for n in range(12)]
    units = [('clear', CLEAR), ('left', ' '.join(words[:6])), ('right', ' '.join(words[6:]))]
    metric = q.inspect_partitioned(units, lexicon=LEXICON)
    assert metric['needs_read']
    assert metric['rejected_units'] == ['left', 'right']
    assert not metric['units']['clear']['needs_read']


@pytest.mark.parametrize('markup', [
    '<img alt="{text}" src="figure.png"/>',
    '<aside class="vision-disclosure">{text}</aside>',
    '<p class="source-reading-annotation">{text}</p>',
    '<a aria-label="{text}" href="#detail">{clear}</a>',
    '<p title="{text}">{clear}</p>',
])
def test_every_reader_received_text_channel_is_swept(markup):
    page = dict(pno=0, body=['<p>'+CLEAR+'</p>', markup.format(text=convergent_damage(), clear=CLEAR)], asides=[])
    before = q.reading_text(page)
    published = q.publication_text(page)
    assert convergent_damage() in published
    sweep = q.sweep_publication_pages([page], {0:dict(source='vision')}, [0], lexicon=LEXICON)
    assert not sweep['passed'] and sweep['pages'][0]['needs_read']
    with pytest.raises(ValueError, match='Every source page'):
        q.validate_sweep(sweep, [0])
    assert q.reading_text(page) == before


def test_short_image_description_does_not_hide_behind_the_body():
    page = dict(pno=0, body=['<p>'+(CLEAR+' ')*20+'</p>', '<img alt="zqxone zqxtwo zqxthree"/>'], asides=[])
    sweep = q.sweep_publication_pages([page], {0:dict(source='vision')}, [0], lexicon=LEXICON)
    assert not sweep['passed']
    assert any('short_disclosure_dictionary_misses' in m['reasons'] for m in sweep['pages'][0]['units'].values())


def test_publication_projection_keeps_inline_words_and_excludes_nonreading_code():
    page = dict(pno=0, body=['<p>con<span>tain</span>er</p><script>zqxone zqxtwo zqxthree</script>',
        '<style>zqxone zqxtwo zqxthree</style>', '<p>'+CLEAR+'</p>'], asides=[])
    parts = q.publication_parts(page)
    assert any(part['text'] == 'container' for part in parts)
    assert 'zqxone' not in q.publication_text(page)
    sweep = q.sweep_publication_pages([page], {0:dict(source='native')}, [0], lexicon=LEXICON)
    assert sweep['passed']
    q.validate_sweep(sweep, [0])


def test_generated_controls_use_installed_vocabulary_without_exempting_source_quotes(monkeypatch):
    installed = LEXICON | {'original', 'source', 'page', 'return'}
    monkeypatch.setattr(q, 'publication_lexicon', lambda: installed)
    page = dict(pno=0, body=['<p>container</p>',
        '<aside class="vision-disclosure"><p>Original source page</p><a>Return</a></aside>'], asides=[])
    assert q.sweep_publication_pages([page], {0:dict(source='native')}, [0], lexicon={'container'})['passed']
    page['body'][1] = '<aside class="vision-disclosure">Original source page: zqxone zqxtwo zqxthree zqxfour</aside>'
    assert not q.sweep_publication_pages([page], {0:dict(source='native')}, [0], lexicon={'container'})['passed']
    monkeypatch.setattr(q, 'publication_lexicon', lambda: None)
    assert not q.sweep_publication_pages([page], {0:dict(source='native')}, [0], lexicon={'container'})['passed']


def test_generated_controls_cannot_dilute_the_source_reading_gate(monkeypatch):
    monkeypatch.setattr(q, 'publication_lexicon', lambda: LEXICON)
    unknown = ['zqx' + chr(97+n) for n in range(20)]
    # Distribute misses so no sixteen-word window reaches the independent limit.
    words = [word for pair in zip(CLEAR.split()*2, unknown) for word in pair]
    units = [('reading-'+str(n), ' '.join(words[n:n+4]), 'reading') for n in range(0,40,4)]
    units += [('control', (CLEAR+' ')*100, 'disclosure')]
    metric = q.inspect_partitioned(units, lexicon=LEXICON)
    assert metric['dictionary_miss_rate'] < .02
    assert metric['source_reading']['dictionary_miss_rate'] == .5
    assert metric['needs_read']
    assert all(owner != 'control' for owner in metric['rejected_units'])


def test_note_continuation_parts_keep_their_own_source_owners():
    page = dict(pno=0, body=[], asides=[
        '<aside class="footnote"><p><span data-source-page="0" data-source-block="note-a">'+CLEAR+'</span></p>'
        '<p><span data-source-page="1" data-source-block="note-b">'+convergent_damage()+'</span></p></aside>'])
    parts = q.publication_parts(page)
    assert parts[0]['owners'] == [dict(page=0, block='note-a')]
    assert parts[1]['owners'] == [dict(page=1, block='note-b')]
    metric = q.inspect_partitioned([(p['id'],p['text'],p['kind']) for p in parts], lexicon=LEXICON)
    assert metric['rejected_units'] == [parts[1]['id']]
    assert [owner for p in parts if p['id'] in metric['rejected_units'] for owner in p['owners']] == [dict(page=1, block='note-b')]


def test_inline_source_owner_changes_do_not_split_a_damaged_word():
    page = dict(pno=0, body=['<p><span data-source-page="0" data-source-block="left">coNt</span>'
        '<span data-source-page="1" data-source-block="right">AiNeR</span></p>'], asides=[])
    parts = q.publication_parts(page)
    assert parts[0]['text'] == 'coNtAiNeR'
    assert parts[0]['owners'] == [dict(page=0,block='left'),dict(page=1,block='right')]
    assert parts[0]['spans'] == [dict(start=0,end=4,owners=[dict(page=0,block='left')]),
        dict(start=4,end=9,owners=[dict(page=1,block='right')])]
    assert q.inspect(parts[0]['text'], lexicon={'container'})['needs_read']


def test_missing_installed_dictionary_cannot_borrow_a_callers_source_vocabulary(monkeypatch):
    custom = {'container', 'original', 'source', 'page', 'return'}
    monkeypatch.setattr(q, 'publication_lexicon', lambda: None)
    monkeypatch.setattr(q, 'english_lexicon', lambda: custom)
    page = dict(pno=0, body=['<p>container</p>', '<aside class="vision-disclosure">Original source page</aside>'], asides=[])
    sweep = q.sweep_publication_pages([page], {0:dict(source='native')}, [0], lexicon=custom)
    assert not sweep['passed']
    metric = sweep['pages'][0]
    assert not metric['dictionary_available']
    assert 'dictionary_unavailable' in metric['reasons']
