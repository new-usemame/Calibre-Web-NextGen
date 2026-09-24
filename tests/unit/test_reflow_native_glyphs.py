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


def test_native_stacked_fractions_follow_the_slash_and_keep_other_raised_marks():
    def body(text, x, y):
        boxes=tuple((i,i+1,x+i*5,y,x+(i+1)*5,y+9) for i in range(len(text)))
        span=extract.Span(text,9,'Times',0,(x,y,x+len(text)*5,y+9),char_boxes=boxes)
        return extract.Line([span],span.bbox)

    # The numerator and denominator are separate PDF lines around a slash in
    # the body line. A footnote marker sits beside ordinary prose on that line.
    line=body('at   /   of the signs',100,100)
    slash=next(c for c in line.spans[0].char_boxes if line.text[c[0]]=='/')
    num=extract.Span('3',6,'Times',0,(slash[2]-3,98.3,slash[2],104.3))
    den=extract.Span('4',6,'Times',0,(slash[4],104.6,slash[4]+3,110.6))
    marker=extract.Span('2',6,'Times',0,(line.bbox[2],98.3,line.bbox[2]+3,104.3))
    blocks=[extract.Block(0,(100,98,200,111),[line]+[extract.Line([s],s.bbox) for s in (num,den,marker)])]
    result=normalize_blocks(blocks)
    joined=' '.join(ln.text for b in result for ln in b.lines)
    assert '3/4' in joined
    assert '4 at' not in joined
    assert joined.count('2')==1
    runs=assemble._line_runs(result[0].lines[0],0,{2},set(),[],[],preserve_style=True)
    assert ['sup','2',0] in runs


def test_slash_without_both_measured_fraction_parts_keeps_source_order():
    main=extract.Span('Chapter 2 / 4',9,'Times',0,(100,100,170,109),
                      char_boxes=((10,11,150,100,154,109),))
    raised=extract.Span('3',6,'Times',0,(147,98,150,104))
    blocks=[extract.Block(0,(100,98,170,109),[extract.Line([main],main.bbox),extract.Line([raised],raised.bbox)])]
    result=normalize_blocks(blocks)
    assert not any('3/4' in line.text for block in result for line in block.lines)


def test_native_inline_numeric_slash_does_not_recruit_unrelated_small_digits():
    text='A 3/4 turn is ordinary inline text.'
    span=extract.Span(text,9,'Times',0,(100,100,100+len(text)*5,109),
                      char_boxes=tuple((i,i+1,100+i*5,100,105+i*5,109) for i in range(len(text))))
    folio=extract.Span('4',6,'Times',0,(300,10,303,16))
    blocks=[extract.Block(0,span.bbox,[extract.Line([span],span.bbox)]),
            extract.Block(1,folio.bbox,[extract.Line([folio],folio.bbox)])]
    result=normalize_blocks(blocks)
    assert [line.text for block in result for line in block.lines]==[text,'4']


def test_unmapped_native_digit_is_not_guessed_into_a_fraction():
    body=extract.Span('a / b',9,'Times',0,(100,100,125,109),
                      char_boxes=((2,3,110,100,114,109),))
    numerator=extract.Span('3',6,'Unmapped',0,(107,98,110,104),encoding_unresolved=True)
    denominator=extract.Span('4',6,'Times',0,(114,104.5,117,110.5))
    blocks=[extract.Block(0,(100,98,125,111),
            [extract.Line([s],s.bbox) for s in (body,numerator,denominator)])]
    result=normalize_blocks(blocks)
    assert not any('3/4' in line.text for block in result for line in block.lines)
    assert sorted(line.text for block in result for line in block.lines)==['3','4','a / b']


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('shared', ['both', 'numerator', 'denominator'])
def test_competing_native_slashes_keep_all_ambiguous_source_atoms(reverse, shared):
    def prose(text, y):
        span=extract.Span(text,9,'Times',0,(100,y,125,y+9),
                          char_boxes=((2,3,110,y,114,y+9),))
        return extract.Line([span],span.bbox)
    first,second=prose('a / b',100),prose('c / d',100.5)
    upper=extract.Span('3',6,'Times',0,(107,98.5,110,104.5))
    lower=extract.Span('4',6,'Times',0,(114,105,117,111))
    # The private extra pair fits only one slash when just one half is shared.
    local_upper=extract.Span('2',6,'Times',0,(107,97.8,110,100.2))
    local_lower=extract.Span('5',6,'Times',0,(114,105.7,117,109.2))
    digits=[upper,lower]
    if shared=='numerator':digits.append(local_lower)
    if shared=='denominator':digits.append(local_upper)
    lines=([second,first] if reverse else [first,second]) + [extract.Line([s],s.bbox) for s in digits]
    result=normalize_blocks([extract.Block(0,(100,97,125,112),lines)])
    resulting=[line for block in result for line in block.lines]
    found=[line.text for line in resulting]
    assert set(found[:2])=={'a / b','c / d'}
    assert not any('/' in text and any(d in text for d in ('2','3','4','5')) for text in found[:2])
    assert sorted(found[2:])==sorted(s.text for s in digits)
    assert sorted((line.text,line.bbox) for line in resulting)==sorted(
        (line.text,line.bbox) for line in lines)


def test_competing_slashes_do_not_suppress_unrelated_fraction():
    def line(text,y,x):
        span=extract.Span(text,9,'Times',0,(x,y,x+25,y+9),
                          char_boxes=((2,3,x+10,y,x+14,y+9),))
        return extract.Line([span],span.bbox)
    prose=[line('a / b',100,100),line('c / d',100.5,100),line('e / f',140,200)]
    digits=[extract.Span('3',6,'Times',0,(107,98.5,110,104.5)),
            extract.Span('4',6,'Times',0,(114,105,117,111)),
            extract.Span('1',6,'Times',0,(207,138,210,144)),
            extract.Span('2',6,'Times',0,(214,144.5,217,150.5))]
    result=normalize_blocks([extract.Block(0,(100,98,225,151),prose+
                 [extract.Line([s],s.bbox) for s in digits])])
    found=[line.text for block in result for line in block.lines]
    assert found[:3]==['a / b','c / d','e 1/2 f']
    assert found[3:]==['3','4']


def test_separated_native_fractions_in_one_line_keep_character_boundaries():
    text='a / b / c'
    boxes=tuple((i,i+1,100+5*i,100,105+5*i,109) for i in range(len(text)))
    main=extract.Span(text,9,'Times',0,(100,100,145,109),char_boxes=boxes)
    digits=[]
    for x,numerator,denominator in ((110,'3','4'),(130,'1','2')):
        for value,bbox in ((numerator,(x-3,98,x,104)),
                           (denominator,(x+5,104.5,x+8,110.5))):
            span=extract.Span(value,6,'Times',0,bbox)
            digits.append(extract.Line([span],bbox))
    result=normalize_blocks([extract.Block(0,(100,98,145,112),
               [extract.Line([main],main.bbox)]+digits)])
    found=[line.text for block in result for line in block.lines]
    assert found==['a 3/4 b 1/2 c']
    final=result[0].lines[0].spans[0]
    assert [(a,b) for a,b,*_ in final.char_boxes]==[(i,i+1) for i in range(len(final.text))]


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


@pytest.mark.parametrize("block_note", [False, True])
def test_glyph_package_links_to_packaged_original_page(tmp_path, block_note):
    import pymupdf, zipfile
    from cps.services.reflow import skeleton
    doc=pymupdf.open();page=doc.new_page();page.insert_text((40,50),'Printed source')
    record=descriptor(0,(40,38,100,52),12,'LegacySymbols')
    element=assemble.Element('p',pno=0,bbox=(40,38,100,52),runs=[['glyph','X',record]])
    book=assemble.Book(elements=[element],pages={0:[element]},style=skeleton.BookStyle(body_size=12))
    if block_note:
        element.runs=[['t','Body'],['sup','1',0]]
        book.notes=[assemble.Note(1,'Unmapped source note',0,marked=True,bbox=(40,38,100,52),glyph_fallback=True)]
    target=tmp_path/'glyph.epub';build_epub.build(book,str(target),doc=doc)
    assert build_epub.validate(str(target)) == []
    with zipfile.ZipFile(target) as archive:
        assert 'OEBPS/original-p0000.xhtml' in archive.namelist()
        assert b'id="page"' in archive.read('OEBPS/original-p0000.xhtml')
    doc.close()


def test_invalid_unicode_trace_replaces_only_affected_word_with_source_pixels():
    from cps.services.reflow.native_text import mark_unmapped_words
    span=extract.Span('Reăase the text',10,'Mapped',0,(0,0,75,10),
        char_boxes=tuple((i,i+1,i*5,0,(i+1)*5,10) for i in range(15)))
    trace=[{'font':'Mapped','chars':[(0xfffd,103,(10,8),(10,1,15,9))]}]
    spans=mark_unmapped_words([span],trace)
    assert ''.join(s.text for s in spans)==span.text
    assert [s.text for s in spans if s.encoding_unresolved]==['Reăase']
    marker=extract.Span('Ą•',6,'Mapped',1,(0,0,10,6),char_boxes=((0,1,0,0,5,6),(1,2,5,0,10,6)))
    result=mark_unmapped_words([marker],[{'font':'Mapped','chars':[(0xfffd,104,(0,5),(0,0,5,6)),(0x2022,103,(5,5),(5,0,10,6))]}])
    assert len(result)==1 and result[0].encoding_unresolved and result[0].text=='Ą•'


def test_valid_mapped_non_latin_word_stays_searchable_text():
    from cps.services.reflow.native_text import mark_unmapped_words
    span=extract.Span('Καί',10,'Mapped',0,(0,0,15,10),char_boxes=tuple((i,i+1,i*5,0,(i+1)*5,10) for i in range(3)))
    trace=[{'font':'Mapped','chars':[(ord(c),i,(i*5,8),(i*5,0,(i+1)*5,10)) for i,c in enumerate(span.text)]}]
    assert mark_unmapped_words([span],trace)==[span]
    assert not span.encoding_unresolved


def test_unmapped_raised_marker_preserves_position_without_guessing_digits():
    marker=extract.Span('Ą•',6,'BrokenMapped',0,(50,8,60,14),encoding_unresolved=True)
    body=extract.Span('word',12,'Times',0,(10,10,50,22))
    line=extract.Line([body,marker],(10,8,60,22))
    runs=assemble._line_runs(line,0,{},set(),[],[],preserve_style=True)
    html=build_epub._runs_html(runs,set(),{})
    assert '<sup><a class="source-glyph"' in html
    assert 'Ą•' not in html and '>24<' not in html


@pytest.mark.parametrize('boundary', ['joined', 'space', 'column', 'raised'])
def test_invalid_word_crosses_only_adjacent_same_line_font_spans(boundary):
    from cps.services.reflow.native_text import mark_unmapped_words
    first='Before Re' + (' ' if boundary=='space' else '')
    left=extract.Span(first,10,'Latin',0,(0,0,len(first)*5,10),
        char_boxes=tuple((i,i+1,i*5,0,(i+1)*5,10) for i in range(len(first))))
    x=45 if boundary in ('joined','raised') else 50 if boundary=='space' else 75
    y=-5 if boundary=='raised' else 0
    right=extract.Span('ăase:',10,'BrokenMapped',0,(x,y,x+25,y+10),
        char_boxes=tuple((i,i+1,x+i*5,y,x+(i+1)*5,y+10) for i in range(5)))
    trace=[{'font':'BrokenMapped','chars':[(0xfffd,103,(x,y+8),(x,y,x+5,y+10))]}]
    result=mark_unmapped_words([left,right],trace)
    affected=[s.text for s in result if s.encoding_unresolved]
    assert affected==(['Reăase:'] if boundary=='joined' else ['ăase:'])
    assert ''.join(s.text for s in result)==first+'ăase:'
    if boundary=='joined':
        assert ''.join(s.text for s in result if not s.encoding_unresolved)=='Before '


def test_whitespace_between_unmapped_words_remains_real_spacing():
    from cps.services.reflow.native_text import mark_unmapped_words
    span=extract.Span('AB CD',10,'LegacyGreek',0,(0,0,25,10),encoding_unresolved=True,
        char_boxes=tuple((i,i+1,i*5,0,(i+1)*5,10) for i in range(5)))
    result=mark_unmapped_words([span],[])
    assert [(s.text,s.encoding_unresolved) for s in result]==[('AB',True),(' ',False),('CD',True)]


@pytest.mark.parametrize('attack', ['css_suffix','other_element','wrong_parent','wrong_image','wrong_page','missing_disclosure'])
def test_glyph_style_exception_does_not_authorize_other_markup(attack):
    from xml.etree import ElementTree as ET
    from cps.services.reflow.native_text import glyph_html,image_name
    record=descriptor(0,(0,0,10,10),10,'Legacy')
    root=ET.fromstring('<html xmlns="http://www.w3.org/1999/xhtml"><body>'+glyph_html(record)+'</body></html>')
    anchor=list(list(root)[0])[0];img=list(anchor)[0]
    if attack=='css_suffix':img.set('style',img.get('style')+';position:fixed')
    elif attack=='other_element':img.tag='{http://www.w3.org/1999/xhtml}span'
    elif attack=='wrong_parent':anchor.set('class','ordinary-link')
    elif attack=='wrong_image':img.set('src','images/ordinary.jpg')
    elif attack=='wrong_page':anchor.set('href','original-p0001.xhtml#page')
    else:img.set('alt','')
    names={'OEBPS/'+image_name(record),'OEBPS/images/ordinary.jpg','OEBPS/original-p0000.xhtml','OEBPS/original-p0001.xhtml'}
    assert build_epub._active_markup('OEBPS/ch001.xhtml',root,names)


def test_multiple_raised_suffixes_use_independent_native_gaps_on_one_line():
    body=extract.Span('1 sign and 12 sign',10,'Times',0,(10,20,110,30),
        char_boxes=((0,1,10,20,15,30),(1,2,22,20,25,30),
                    (11,12,65,20,70,30),(12,13,70,20,75,30),(13,14,83,20,86,30)))
    one=extract.Span('st',6,'Times',0,(15.5,18,21,24))
    two=extract.Span('th',6,'Times',0,(75.5,18,82,24))
    result=normalize_blocks([extract.Block(0,(10,18,110,30),
        [extract.Line([body],body.bbox),extract.Line([one],one.bbox),extract.Line([two],two.bbox)])])
    assert len(result[0].lines)==1
    spans=result[0].lines[0].spans
    assert ''.join(s.text for s in spans)=='1st sign and 12th sign'
    assert [s.text for s in spans if s.superscript]==['st','th']


def test_detached_ordinary_letters_are_not_attached_as_an_ordinal():
    body=extract.Span('a label',10,'Times',0,(10,20,55,30),char_boxes=((0,1,10,20,15,30),))
    marker=extract.Span('st',6,'Times',0,(15.5,18,21,24))
    result=normalize_blocks([extract.Block(0,(10,18,55,30),
        [extract.Line([body],body.bbox),extract.Line([marker],marker.bbox)])])
    assert [line.text for line in result[0].lines]==['a label','st']


def test_raised_suffix_can_occupy_the_source_whitespace_before_punctuation():
    number=extract.Span('10',10,'Times',0,(10,20,20,30),
        char_boxes=((0,1,10,20,15,30),(1,2,15,20,20,30)))
    space=extract.Span(' ',10,'Times',0,(20,20,27,30),char_boxes=((0,1,20,20,27,30),))
    rest=extract.Span('. Next',10,'Times',0,(27,20,60,30),char_boxes=((0,1,27,20,30,30),))
    marker=extract.Span('th',6,'Times',0,(20.5,18,26,24))
    body=extract.Line([number,space,rest],(10,20,60,30))
    result=normalize_blocks([extract.Block(0,(10,18,60,30),[body,extract.Line([marker],marker.bbox)])])
    assert len(result[0].lines)==1
    assert ''.join(s.text for s in result[0].lines[0].spans)=='10th . Next'
    assert [s.text for s in result[0].lines[0].spans if s.superscript]==['th']
