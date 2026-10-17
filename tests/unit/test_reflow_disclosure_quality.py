"""Synthetic lexical controls for chosen readings and image descriptions."""
import pymupdf
import pytest

from cps.services.reflow import build_epub, text_quality, vision_pipeline
from cps.services.reflow.source_display import SourceDisplay
from tests.unit.test_reflow_vision_pilot import answer, block

pytestmark = pytest.mark.unit
CLEAN = 'Green baskets contain ripe apples while purple carts carry fresh bread.'
LEXICON = frozenset(CLEAN.casefold().rstrip('.').split())


@pytest.fixture
def compilation(tmp_path, monkeypatch):
    monkeypatch.setattr(text_quality, 'english_lexicon', lambda: LEXICON)
    package = build_epub._Package(str(tmp_path / 'description.epub'))
    document = pymupdf.open()
    document.new_page(width=400, height=400)
    yield SourceDisplay(document, 0), package
    package.abandon()
    document.close()


def words(text):
    return [dict(text=word, bbox=[20 + (n % 7) * 45, 40 + (n // 7) * 20,
                                 60 + (n % 7) * 45, 55 + (n // 7) * 20])
            for n, word in enumerate(text.split())]


@pytest.mark.parametrize('reading,forced', [
    ('green baskets blorple ripe zzqxv while purple qvxzz carry fresh bread zzvqx qzvxz qvxqz', True),
    ('green blorple zzqxv', False),
])
def test_garbled_short_chosen_reading_requires_source_image_recovery(compilation, reading, forced):
    display, package = compilation
    # These samples evade the long-page threshold. Disclosure text has its own
    # short-reading gate, so excluding annotations cannot publish these words.
    assert not text_quality.inspect(reading)['needs_read']
    uncertain = () if forced else tuple(range(len(reading.split())))
    with pytest.raises(ValueError, match='Unreliable chosen reading'):
        vision_pipeline.compile_page(
            answer(block(reading, bbox=(0, 0, 1000, 1000), low=uncertain)),
            words(reading if forced else 'green baskets fresh'), display, 0, package, {},
            forced_uncertain=('b1',) if forced else ())


def test_garbled_image_description_cannot_be_published_as_alt_text(compilation):
    display, package = compilation
    with pytest.raises(ValueError, match='Unreliable image description'):
        vision_pipeline.compile_page(
            answer(block('green blorple zzqxv', role='figure')), [],
            display, 0, package, {})


def test_bad_short_source_quote_is_suppressed_but_good_model_reading_stays(compilation):
    display, package = compilation
    published, report = vision_pipeline.compile_page(
        answer(block('green baskets fresh bread', bbox=(0, 0, 1000, 1000))),
        words('green blorple zzqxv bread'), display, 0, package, {})
    assert 'green baskets' in ''.join(published['body'])
    assert report['source_quote_suppressions']
    assert all('blorple' not in value and 'zzqxv' not in value for value in published['asides'])
    assert any('Original printed reading' in value and '<img ' in value for value in published['asides'])


@pytest.mark.parametrize('reading', [CLEAN, 'GREEN BASKETS', 'αλφαβήτα γαμμαδέλτα καππαλάμδα'])
def test_clear_readings_and_non_latin_letters_are_kept(compilation, reading):
    display, package = compilation
    published, report = vision_pipeline.compile_page(
        answer(block(reading, bbox=(0, 0, 1000, 1000))), words(reading),
        display, 0, package, {}, forced_uncertain=('b1',))
    assert report['blocks'][0]['fallback']
    assert all(word in ''.join(published['body']) for word in reading.split())
    assert any('<img ' in value for value in published['asides'])


def test_clean_surrounding_prose_cannot_dilute_a_garbled_chosen_fragment(compilation):
    display, package = compilation
    reading = (CLEAN + ' ') * 4 + 'blorple zzqxv qvxzz ' + (CLEAN + ' ') * 4
    source = (CLEAN + ' ') * 4 + 'baskets contain apples ' + (CLEAN + ' ') * 4
    assert not text_quality.inspect(reading)['needs_read']
    with pytest.raises(ValueError, match='Unreliable chosen reading'):
        vision_pipeline.compile_page(
            answer(block(reading, bbox=(0, 0, 1000, 1000))), words(source),
            display, 0, package, {})


@pytest.mark.parametrize('source', ['green baskets blorple', '. -'])
def test_catastrophic_fallback_contraction_requires_pixels_even_without_many_dictionary_misses(
        compilation, source):
    display, package = compilation
    reading = (CLEAN + ' ') * 5
    assert not text_quality.inspect_disclosure(source)['needs_read']
    with pytest.raises(ValueError, match='Unreliable contracted fallback'):
        vision_pipeline.compile_page(
            answer(block(reading, bbox=(0, 0, 1000, 1000))), words(source),
            display, 0, package, {})


def test_one_quarter_source_token_coverage_is_not_catastrophic_contraction(compilation, monkeypatch):
    display, package = compilation
    monkeypatch.setattr(text_quality, 'english_lexicon', lambda: LEXICON | {'silver', 'copper'})
    published, report = vision_pipeline.compile_page(
        answer(block('green baskets contain ripe apples while purple carts', bbox=(0, 0, 1000, 1000))),
        words('silver copper'), display, 0, package, {})
    assert report['blocks'][0]['fallback']
    assert report['blocks'][0]['published_tokens'] == ['silver', 'copper']
    assert 'silver copper' in ''.join(published['body'])


def test_source_bound_alphanumeric_labels_are_not_english_dictionary_misses(compilation):
    display, package = compilation
    labels = 'RackLeft00 RackRight01 BinUpper02 BinLower03'
    published, report = vision_pipeline.compile_page(
        answer(block(labels, bbox=(0, 0, 1000, 1000))), words(labels),
        display, 0, package, {}, forced_uncertain=('b1',))
    assert report['blocks'][0]['fallback']
    assert report['blocks'][0]['published_tokens'] == labels.split()
    assert all(label in ''.join(published['body']) for label in labels.split())


def test_complete_source_paragraph_keeps_its_guarded_tail_when_model_borrows_long_prose(compilation):
    display, package = compilation
    # The source has a complete, healthy paragraph. The model appended a long
    # unrelated passage; its correction is severe locally, but the complete
    # chosen reading still retains the printed paragraph's substantial coverage.
    reading = (CLEAN.rsplit(' ', 1)[0] + ' loaf while careful workers place sturdy wooden crates '
               'near a clean doorway before carrying useful tools into storage')
    published, report = vision_pipeline.compile_page(
        answer(block(reading, bbox=(0, 0, 1000, 1000))), words(CLEAN),
        display, 0, package, {})
    record = report['blocks'][0]
    assert record['fallback']
    assert any(item.get('kind') == 'replace'
               and item['model_end'] - item['model_start'] >= 8
               and item['layer_end'] - item['layer_start'] == 1
               for item in record['guard']), record['guard']
    assert record['published_tokens'] == CLEAN.split()
    assert CLEAN in ''.join(published['body'])
    assert ''.join(published['body']).count('Green baskets') == 1
