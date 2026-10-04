"""Running heads must stay traceable without breaking the reader's sentence."""
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import assemble, build_epub, extract, skeleton

pytestmark = pytest.mark.unit


def _line(text, x, y, size=10):
    box = (x, y, x + 200, y + size)
    return extract.Line([extract.Span(text, size, 'Serif', 0, box)], box)


def _raw(page, header, y=44):
    lines = [_line(header, 50, y), _line('Ordinary source body continues here.', 50, y+27, 11),
             _line('SPECIAL LUNAR CONSIDERATIONS remain in body.', 50, y+55, 11)]
    return extract.RawPage(page, 432, 648, [extract.Block(0,(50,y,300,y+66),lines)])


@pytest.mark.parametrize('y,headers', [
    (44, ['88 The Signs', 'Traditional Natal Astrology 89', '90 The Signs',
          'Traditional Natal Astrology 91', '92 The Signs', 'Traditional Natal Astrology 93']),
    (100, ['SPECIAL LUNAR CONSIDERATIONS'] * 6),
])
def test_repeated_detached_heads_outside_fixed_band_leave_prose_but_survive_accounting(y, headers):
    raws = [_raw(i, text, y) for i, text in enumerate(headers)]
    style = skeleton.book_style(raws)
    skels = [skeleton.page_skeleton(raw, style) for raw in raws]
    for raw, skel in zip(raws, skels):
        furniture = [r.text for r in skel.regions if r.kind == 'furniture']
        assert raw.text_blocks[0].lines[0].stripped in furniture
        assert 'SPECIAL LUNAR CONSIDERATIONS remain in body.' not in furniture
    book = assemble.assemble(skels, style, raw_pages=raws)
    assert book.conservation.ok, book.conservation.to_dict()
    assert len(book.furniture) == len(raws)
    # Furniture has a readable inspection channel, with each original occurrence.
    for raw in raws:
        html = build_epub._printed_furniture(book, {p: 'ch001.xhtml' for p in book.pages}, 'en')
        root = ET.fromstring(html)
        retained = root.find(".//{http://www.w3.org/1999/xhtml}section[@id='furniture_p%04d']" % raw.pno)
        assert retained is not None
        assert raw.text_blocks[0].lines[0].stripped in ''.join(retained.itertext())


def test_unrepeated_heading_and_undetached_repetition_remain_body():
    raws = [_raw(i, 'A real heading' if i == 0 else 'A distinct heading %d' % i) for i in range(6)]
    # Repeated text without separation from the next line is ordinary content.
    for raw in raws:
        raw.blocks[0].lines.insert(1, _line('Repeated opening text', 50, 56, 11))
    style = skeleton.book_style(raws)
    for raw in raws:
        skel = skeleton.page_skeleton(raw, style)
        assert not any(r.kind == 'furniture' for r in skel.regions)


def test_three_page_sentence_keeps_notes_and_notices_after_complete_prose():
    """The second seam must find the first seam's paragraph, past page-local notes."""
    notice = '<p class="source-evidence-notice">Check the original.</p>'
    pages = build_epub._page_blocks({
        0: '<p>The first part of the sentence</p><aside class="footnote" id="fn_1"><p>1 Printed note.</p></aside>'+notice,
        1: '<p>continues on the middle page and</p>'+notice,
        2: '<p>finishes on the last page.</p>',
    })
    assert build_epub._join_page_turns(pages) == 2
    chapters = build_epub._chapters(pages)
    xml = build_epub._document('Sentence', ''.join(b for c in chapters for b in c.blocks))
    root = ET.fromstring(xml)
    paragraphs = list(root.iter('{http://www.w3.org/1999/xhtml}p'))
    assert ''.join(paragraphs[0].itertext()) == ('The first part of the sentence continues on the middle page and '
                                               'finishes on the last page.')
    assert 'Printed note.' in ''.join(root.itertext())
    assert xml.count(notice) == 2
    for pno in (0,1,2):assert root.find('.//*[@id="pg_%04d"]' % pno) is not None


def test_gap_in_source_pages_does_not_join_unrelated_paragraphs():
    pages = build_epub._page_blocks({0:'<p>An unfinished sentence</p>',2:'<p>another sampled page.</p>'})
    assert build_epub._join_page_turns(pages) == 0


@pytest.mark.parametrize('left,right,words,compounds,expected',[
    ('reflective or self-','aware.',{'selfaware'},{'self-aware'},'reflective or self-aware.'),
    ('their emo-','tional landscape.',{'emotional'},set(),'their emotional landscape.'),
    ('a one-off unknown-','token.',{'other'},set(),'a one-off unknown-token.'),
])
def test_page_wraps_need_source_word_evidence_and_preserve_printed_compounds(left,right,words,compounds,expected):
    pages = build_epub._page_blocks({0:'<p>'+left+'</p>',1:'<p>'+right+'</p>'})
    assert build_epub._join_page_turns(pages,words=words,compounds=compounds)==1
    root = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+pages[0]['body'][0]+'</root>')
    assert ''.join(root.itertext()) == expected


def test_white_figure_candidate_cannot_block_a_real_page_turn(tmp_path):
    """Blank figure admission must happen before deciding the paragraph seam."""
    import zipfile
    import pymupdf
    doc = pymupdf.open()
    try:
        for _ in range(2): doc.new_page(width=300, height=400)
        doc[0].insert_text((40,80), 'This ordinary source sentence continues', fontsize=12)
        doc[1].insert_text((40,80), 'over the next printed page.', fontsize=12)
        first = assemble.Element(kind='p',pno=0,runs=[['t','This ordinary source sentence continues']])
        blank = assemble.Element(kind='fig',pno=0,bbox=(40,300,260,380))
        last = assemble.Element(kind='p',pno=1,runs=[['t','over the next printed page.']])
        book = assemble.Book(pages={0:[first,blank],1:[last]},
            figures=[dict(pno=0,bbox=blank.bbox,needs_ink=True)],style=skeleton.BookStyle(body_size=12))
        out=tmp_path/'blank-seam.epub'
        result=build_epub.build(book,str(out),doc=doc)
        assert result.page_joins==1
        with zipfile.ZipFile(out) as z:
            paragraphs=[p for name in z.namelist() if name.startswith('OEBPS/ch') and name.endswith('.xhtml')
                for p in ET.fromstring(z.read(name)).iter('{http://www.w3.org/1999/xhtml}p')]
        assert any(''.join(p.itertext())=='This ordinary source sentence continues over the next printed page.' for p in paragraphs)
    finally:
        doc.close()


def test_blank_image_with_printed_caption_still_separates_source_prose():
    chapter=build_epub.Chapter(index=1,title='',blocks=[
        '<p>An unfinished source paragraph</p>',
        '<figure><img src="images/blank.jpg"/><figcaption>A separate printed caption.</figcaption></figure>',
    ])
    build_epub._drop_images([chapter],['images/blank.jpg'])
    pages=build_epub._page_blocks({0:''.join(chapter.blocks),1:'<p>the following body paragraph.</p>'})
    assert build_epub._join_page_turns(pages)==0
    assert 'A separate printed caption.' in ''.join(pages[0]['body'])


def _slightly_smaller_note(*, gap=30, raised=True):
    body = _line('The source sentence is still continuing', 40, 300, 12)
    y = body.bbox[3]+gap
    label = extract.Span('1', 6 if raised else 11.5, 'Serif', 0, (40,y,44,y+6))
    words = extract.Span('See the earlier discussion.',11.5,'Serif',0,(46,y+3,250,y+14.5))
    note = extract.Line([label, words],(40,y,250,y+14.5))
    return extract.RawPage(0,300,500,[extract.Block(0,body.bbox,[body]),extract.Block(1,note.bbox,[note])])


def test_detached_raised_note_need_not_be_ten_percent_smaller():
    raw = _slightly_smaller_note()
    skel = skeleton.page_skeleton(raw,skeleton.BookStyle(body_size=12))
    assert len(skel.note_regions)==1
    assert skel.note_regions[0].number==1
    assert 'See the earlier discussion.' in skel.note_regions[0].text
    assert not any('See the earlier discussion.' in r.text for r in skel.regions if r.kind=='body')


@pytest.mark.parametrize('gap,raised',[(2,True),(30,False)])
def test_slightly_smaller_numbered_body_needs_detachment_and_raised_marker(gap,raised):
    raw = _slightly_smaller_note(gap=gap,raised=raised)
    skel = skeleton.page_skeleton(raw,skeleton.BookStyle(body_size=12))
    assert not skel.note_regions
    assert any('See the earlier discussion.' in r.text for r in skel.regions if r.kind=='body')


@pytest.mark.parametrize('kind',['sup','mark'])
def test_leading_note_marker_does_not_split_a_source_sentence(kind):
    left=assemble.Element('p',runs=[['t','The succession of rulers in time']],bbox=(40,100,260,130))
    right=assemble.Element('p',runs=[[kind,'1'],['t',' and their corresponding signs.']],bbox=(40,125,260,170))
    joined=assemble._join_within_page([left,right],set())
    assert len(joined)==1
    assert any(run[0]==kind and run[1]=='1' for run in joined[0].runs)
    assert joined[0].text.endswith('and their corresponding signs.')


@pytest.mark.parametrize('completed,marker',[(True,True),(False,False)])
def test_marker_join_keeps_completed_paragraphs_and_literal_numbered_text(completed,marker):
    left=assemble.Element('p',runs=[['t','A complete sentence.' if completed else 'An unfinished sentence']],bbox=(40,100,260,130))
    runs=[['mark','1'],['t',' another source sentence.']] if marker else [['t','1 another source sentence.']]
    right=assemble.Element('p',runs=runs,bbox=(40,125,260,170))
    assert len(assemble._join_within_page([left,right],set()))==2


def _damaged_folio_pages():
    raws=[]
    for p,text in enumerate(('50','51','5S','53','54')):
        body=_line('A source paragraph continues in its proper order',40,425,12)
        folio=_line(text,40,450,10);folio.bbox=(40,450,52,460);folio.spans[0].bbox=folio.bbox
        raws.append(extract.RawPage(p,300,500,[extract.Block(0,body.bbox,[body]),extract.Block(1,folio.bbox,[folio])]))
    return raws


def test_damaged_short_footer_uses_proven_neighbor_geometry_without_repairing_text():
    raws=_damaged_folio_pages();style=skeleton.book_style(raws)
    skel=skeleton.page_skeleton(raws[2],style)
    retained=[r for r in skel.regions if r.kind=='furniture']
    assert len(retained)==1 and retained[0].text=='5S'
    book=assemble.assemble([skeleton.page_skeleton(r,style) for r in raws],style,raws)
    assert book.conservation.ok
    assert '5S' in build_epub._printed_furniture(book,{p:'ch001.xhtml' for p in book.pages},'en')


def test_short_lower_page_text_without_matching_neighbors_remains_body():
    raws=_damaged_folio_pages();raws[4].blocks[-1].lines[0].spans[0].text='92'
    style=skeleton.book_style(raws)
    assert not any(r.kind=='furniture' and r.text=='5S' for r in skeleton.page_skeleton(raws[2],style).regions)


def test_damaged_secondary_note_label_keeps_its_own_source_channel():
    raw=_slightly_smaller_note()
    first=raw.text_blocks[-1]
    marker=extract.Span('"',8.5,'Serif',0,(40,370,44,378.5))
    words=extract.Span(' Another complete printed note.',11.5,'Serif',0,(46,370,250,381.5))
    line=extract.Line([marker,words],(40,370,250,381.5))
    raw.blocks.append(extract.Block(2,line.bbox,[line]))
    folio=_line('2',40,489,8)
    raw.blocks.append(extract.Block(3,folio.bbox,[folio]))
    skel=skeleton.page_skeleton(raw,skeleton.BookStyle(body_size=12))
    assert len(skel.note_regions)==2
    assert skel.note_regions[0].number==1
    assert skel.note_regions[1].number is None
    assert skel.note_regions[1].text=='" Another complete printed note.'
    assert skel.note_regions[1].uncertain
    book=assemble.assemble([skel],skeleton.BookStyle(body_size=12),[raw])
    assert book.conservation.ok
