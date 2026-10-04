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
