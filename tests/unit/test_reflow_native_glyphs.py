"""Native glyph meaning must not be inferred from legacy character codes."""
import pytest
from cps.services.reflow import assemble, build_epub, extract
from cps.services.reflow.native_text import descriptor, normalize_blocks, unresolved_fonts


def test_unmapped_glyph_is_pixels_with_explicit_source_disclosure():
    record=descriptor(4,(20,30,25,38),8,'LegacySymbols')
    html=build_epub._runs_html([['glyph','!',record]],set(),{})
    assert '<img ' in html and 'encoding' in html.lower()
    assert 'original-p0004.xhtml#page' in html
    assert '>!</' not in html


def test_raised_ordinal_is_not_a_footnote_link():
    html=build_epub._runs_html([['t','the 1'],['raised','st'],['t',' house']],set(),{})
    assert html=='the 1<sup>st</sup> house'
    assert assemble.plain_text([['t','1'],['raised','st']])=='1st'


def test_superscript_uses_pdf_character_gap_not_string_proportion():
    body=extract.Span('1 house',10,'Times',0,(10,20,55,30),char_boxes=((0,1,10,20,15,30),(1,2,22,20,25,30),(2,3,25,20,30,30)))
    marker=extract.Span('st',6,'Times',0,(15.5,18,21,24))
    line=extract.Line([body],body.bbox);raised=extract.Line([marker],marker.bbox)
    blocks=[extract.Block(0,(10,18,55,30),[line,raised])]
    result=normalize_blocks(blocks)
    assert len(result[0].lines)==1
    assert [s.text for s in result[0].lines[0].spans]==['1','st',' house']


def test_real_unicode_map_overrides_symbolic_font_name():
    class Doc:
        def xref_get_key(self,xref,key):return ('xref','9 0 R') if key=='ToUnicode' else ('int','4')
    class Page:
        parent=Doc()
        def get_fonts(self):return [(1,'pfa','Type1','ABCDEF+LegacySymbols','F1','')]
    assert unresolved_fonts(Page())==set()


def test_missing_unicode_symbol_encoding_is_explicitly_unsupported():
    class Doc:
        def xref_get_key(self,xref,key):return ('null','null') if key=='ToUnicode' else ('int','4')
    class Page:
        parent=Doc()
        def get_fonts(self):return [(1,'pfa','Type1','ABCDEF+LegacyGreek','F1','')]
    assert unresolved_fonts(Page())=={'LegacyGreek'}


def test_ambiguous_raised_position_retains_all_source_lines():
    span=extract.Span('1 house',10,'Times',0,(10,20,55,30),char_boxes=((0,1,10,20,15,30),))
    marker=extract.Span('st',6,'Times',0,(15.5,18,21,24))
    lines=[extract.Line([span],span.bbox),extract.Line([span],span.bbox),extract.Line([marker],marker.bbox)]
    result=normalize_blocks([extract.Block(0,(10,18,55,30),lines)])
    assert [line.text for line in result[0].lines]==['1 house','1 house','st']


def test_native_note_glyph_matches_its_position_not_same_letters_in_prose():
    from types import SimpleNamespace
    from cps.services.reflow.native_text import note_glyph_runs
    plain=extract.Span('3 Ordinary A before ',10,'Times',0,(0,0,100,10))
    greek=extract.Span('A',10,'LegacyGreek',0,(100,0,110,10),encoding_unresolved=True)
    tail=extract.Span(' ends.',10,'Times',0,(110,0,150,10))
    region=SimpleNamespace(number=3,lines=[extract.Line([plain,greek,tail],(0,0,150,10))])
    runs=note_glyph_runs(region,'Ordinary A before A ends.',0)
    assert [r[:2] for r in runs]==[['t','Ordinary A before '],['glyph','A'],['t',' ends.']]
    assert assemble.plain_text(runs)=='Ordinary A before A ends.'
    assert note_glyph_runs(region,'Changed text.',0)==[]


def test_missing_mapping_does_not_turn_ordinary_latin_into_images():
    class Doc:
        def xref_get_key(self,xref,key):return ('null','null') if key=='ToUnicode' else ('int','32')
    class Page:
        parent=Doc()
        def get_fonts(self):return [(1,'pfa','Type1','TimesNewRoman','F1','WinAnsiEncoding')]
    assert unresolved_fonts(Page())==set()


def test_drop_initial_joins_only_when_geometry_proves_same_word():
    cap=extract.Span('A',30,'Decorative',0,(10,10,25,40))
    first=extract.Span('fter the opening',10,'Times',0,(26,10,140,20))
    second=extract.Span('the paragraph continues.',10,'Times',0,(26,24,140,34))
    blocks=[extract.Block(i,s.bbox,[extract.Line([s],s.bbox)]) for i,s in enumerate([cap,first,second])]
    result=normalize_blocks(blocks)
    assert result[0].lines[0].text=='After the opening'
    first.bbox=(60,10,160,20)
    blocks=[extract.Block(i,s.bbox,[extract.Line([s],s.bbox)]) for i,s in enumerate([cap,first,second])]
    assert normalize_blocks(blocks)[0].lines[0].text=='A'


def test_repeated_callout_ids_stay_unique_and_return_to_first():
    from lxml import etree
    ids={}
    html=build_epub._runs_html([['sup','1',0],['sup','1',0],['sup','1',0]],{'1'},ids)
    nodes=etree.fromstring(('<div xmlns:epub="http://www.idpf.org/2007/ops">'+html+'</div>').encode())
    assert len({node.get('id') for node in nodes})==3
    assert ids['1']==nodes[0].get('id')


def test_glyph_package_links_to_packaged_original_page(tmp_path):
    import pymupdf, zipfile
    from cps.services.reflow import skeleton
    doc=pymupdf.open();page=doc.new_page();page.insert_text((40,50),'Printed source')
    record=descriptor(0,(40,38,100,52),12,'LegacySymbols')
    element=assemble.Element('p',pno=0,bbox=(40,38,100,52),runs=[['glyph','X',record]])
    book=assemble.Book(elements=[element],pages={0:[element]},style=skeleton.BookStyle(body_size=12))
    target=tmp_path/'glyph.epub';build_epub.build(book,str(target),doc=doc)
    with zipfile.ZipFile(target) as archive:
        assert 'OEBPS/original-p0000.xhtml' in archive.namelist()
        assert b'id="page"' in archive.read('OEBPS/original-p0000.xhtml')
    doc.close()
