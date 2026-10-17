"""Source-script evidence uses synthetic PDF glyphs and original rendered pixels."""
import pymupdf
import pytest

from cps.services.reflow import text_quality, visible_scripts
from cps.services.reflow.source_display import SourceDisplay

pytestmark = pytest.mark.unit
PASSAGE = 'βήτα γάμμα δέλτα'
LATIN = 'A clear synthetic sample remains readable.'


def page(doc, text=PASSAGE, **kwargs):
    source = doc.new_page(width=400, height=220)
    source.insert_font(fontname='script', fontbuffer=pymupdf.Font('helv').buffer)
    source.insert_text((30, 55), text, fontname='script', fontsize=14, **kwargs)
    return source


@pytest.mark.parametrize('hidden', ['invisible', 'zero-opacity', 'white'])
def test_unpainted_source_labels_do_not_require_a_script(hidden):
    with pymupdf.open() as doc:
        options = {'render_mode': 3} if hidden == 'invisible' else {'fill_opacity': 0} if hidden == 'zero-opacity' else {'color': (1, 1, 1)}
        source = page(doc, **options)
        source.insert_text((30, 100), LATIN)
        assert text_quality.script_counts(source.get_text())['GREEK'] >= 4
        proof = visible_scripts.evidence(SourceDisplay(doc, 0))
        assert proof['scripts'] == {}
        assert text_quality.script_mismatches(proof['text'], LATIN) == []
        assert sum(proof['rejected'].values()) >= 4


def test_visible_passage_requires_script_even_without_a_script_ocr_model():
    with pymupdf.open() as doc:
        page(doc)
        proof = visible_scripts.evidence(SourceDisplay(doc, 0))
        assert proof['scripts']['GREEK'] >= 4
        assert text_quality.script_mismatches(proof['text'], LATIN) == ['GREEK']
        assert text_quality.script_mismatches(proof['text'], PASSAGE) == []


def test_latin_confusable_symbols_alone_do_not_require_another_script():
    with pymupdf.open() as doc:
        source = page(doc, 'ΑΒΕΖΗΙΚΜΝΟΡΤΥΧ')
        assert text_quality.script_counts(source.get_text())['GREEK'] >= 4
        proof = visible_scripts.evidence(SourceDisplay(doc, 0))
        assert proof['scripts'] == {}
        assert proof['rejected']['latin_confusable'] == 14


def test_mislabeled_latin_glyphs_are_not_script_pixel_evidence():
    with pymupdf.open() as doc:
        source = page(doc, 'BCBCBCBC')
        font = pymupdf.Font('helv')
        mappings = [(font.has_glyph(ord(letter)), ord(label)) for letter, label in zip('BC', 'βδ')]
        cmap = ('/CIDInit /ProcSet findresource begin 12 dict begin begincmap '
                '/CIDSystemInfo << /Registry (Synthetic) /Ordering (Synthetic) /Supplement 0 >> def '
                '/CMapName /Synthetic def /CMapType 2 def '
                '1 begincodespacerange <0000> <FFFF> endcodespacerange '
                '2 beginbfchar '+ ' '.join('<%04X> <%04X>' % pair for pair in mappings)+
                ' endbfchar endcmap CMapName currentdict /CMap defineresource pop end end').encode()
        xref = doc.get_new_xref(); doc.update_object(xref, '<<>>'); doc.update_stream(xref, cmap)
        doc.xref_set_key(source.get_fonts()[0][0], 'ToUnicode', str(xref)+' 0 R')
        # Reopen to discard the native font/text cache after the CMap mutation.
        with pymupdf.open(stream=doc.tobytes(), filetype='pdf') as reopened:
            assert text_quality.script_counts(reopened[0].get_text())['GREEK'] == 8
            proof = visible_scripts.evidence(SourceDisplay(reopened, 0))
            assert proof['scripts'] == {}
            assert proof['rejected']['glyph_mapping_unproved'] == 8


@pytest.mark.parametrize('rotation,orientation', [(0, 0), (90, 0), (270, 90)])
def test_visible_script_evidence_follows_source_display_coordinates(rotation, orientation):
    with pymupdf.open() as doc:
        source = page(doc); source.set_rotation(rotation)
        provenance = {'layer':'ocr', 'orientation':orientation, 'source_rotation':rotation, 'page_rect':list(source.rect)}
        proof = visible_scripts.evidence(SourceDisplay(doc, 0, provenance))
        assert proof['scripts']['GREEK'] >= 4
        assert text_quality.script_mismatches(proof['text'], LATIN) == ['GREEK']


def test_short_visible_passage_keeps_distinct_lowercase_alpha():
    with pymupdf.open() as doc:
        page(doc, 'αλφα')
        proof = visible_scripts.evidence(SourceDisplay(doc, 0))
        assert proof['scripts']['GREEK'] == 4
        assert text_quality.script_mismatches(proof['text'], LATIN) == ['GREEK']


def test_disclosed_script_region_does_not_require_script_in_retained_sibling():
    with pymupdf.open() as doc:
        source = page(doc)
        source.insert_text((30, 130), LATIN, fontsize=12)
        display = SourceDisplay(doc, 0)
        script_box = [20, 30, 260, 70]
        sibling_box = [20, 105, 360, 145]
        all_evidence = visible_scripts.evidence(display)
        assert text_quality.script_mismatches(all_evidence['text'], LATIN) == ['GREEK']
        script_evidence = visible_scripts.evidence(display, rectangles=[script_box])
        assert script_evidence['scripts']['GREEK'] >= 4
        assert text_quality.script_mismatches(script_evidence['text'], LATIN) == ['GREEK']
        assert visible_scripts.source_text(display, rectangles=[sibling_box]) == ''
        assert visible_scripts.source_text(display, rectangles=[]) == ''
        assert visible_scripts.source_text(display, rectangles=[sibling_box, script_box]) == all_evidence['text']
        assert visible_scripts.evidence(display)['text'] == all_evidence['text']
        assert all(30 <= (g['bbox'][1]+g['bbox'][3])/2 <= 70 for g in script_evidence['glyphs'])


def test_ambiguous_font_name_cannot_borrow_another_fonts_script_mapping(monkeypatch):
    with pymupdf.open() as doc:
        source = page(doc)
        key = visible_scripts._font_key(source.get_texttrace()[0]['font'])
        monkeypatch.setattr(visible_scripts, '_fonts', lambda display: {
            key: [pymupdf.Font('helv'), pymupdf.Font('cjk')]})
        proof = visible_scripts.evidence(SourceDisplay(doc, 0))
        assert proof['scripts'] == {}
        assert proof['rejected']['glyph_mapping_unproved'] >= 4
