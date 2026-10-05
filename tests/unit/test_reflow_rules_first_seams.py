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


def _scan_spread(page, head, *, height=6, gap=12, body_height=12):
    lines = []
    for x in (35, 450):
        box = (x, 20, x+125, 20+height)
        lines.append(extract.Line([extract.Span(head, 15, 'OCR', 0, box)], box))
        for row in range(6):
            y = 20+height+gap+row*body_height
            box = (x, y, x+335, y+body_height)
            text = 'Ordinary source prose continues across the spread' + ('.' if row==5 else '')
            lines.append(extract.Line([extract.Span(text,
                         11, 'OCR', 0, box)], box))
    raw = extract.RawPage(page, 842, 595, [extract.Block(0,(35,20,785,130),lines)])
    raw.text_layer_invisible = True
    raw.images = [extract.Image((0,0,842,595), 1.0)]
    for line in lines:
        if line.bbox[1] == 20:
            line.transcription_uncertain = True
    return raw


def test_damaged_scan_spread_heads_use_repeated_lettering_and_measured_ink_height():
    raws = [_scan_spread(i, text) for i, text in enumerate([
        '28 PTE RE AE VSD Rey', 'ame) HHE REAL Ved Robey', '26 PTE RE AE VSD Robey'])]
    style = skeleton.book_style(raws)
    for raw in raws:
        heads = [line for line in raw.blocks[0].lines if line.bbox[1] == 20]
        for head in heads:
            assert skeleton._furniture_reason(head, raw, style) == 'repeated_scan_spread_head'
        body = raw.blocks[0].lines[1]
        assert skeleton._furniture_reason(body, raw, style) is None
        skel = skeleton.page_skeleton(raw, style)
        assert len([region for region in skel.regions if region.kind=='furniture']) == 2
        assert not any(region.kind=='artwork' and region.bbox[1] == 20 for region in skel.regions)
    book = assemble.assemble([skeleton.page_skeleton(raw,style) for raw in raws],style,raw_pages=raws)
    assert book.conservation.ok, book.conservation.to_dict()
    with pytest.raises(ValueError, match='pixels are required'):
        build_epub._printed_furniture(book,{i:'ch001.xhtml' for i in range(3)},'en')
    evidence = {}
    for pno, inventory in book.source_inventory.items():
        evidence[pno] = dict(href='original-p%04d.xhtml' % pno, details=[
            dict(id='furniture_%d'%i, src='images/head_%d_%d.jpg'%(pno,i))
            for i,region in enumerate(inventory['regions']) if region['suggested_kind']=='furniture'])
    html = build_epub._printed_furniture(book,{i:'ch001.xhtml' for i in range(3)},'en',evidence)
    root = ET.fromstring(html)
    assert len(list(root.iter('{http://www.w3.org/1999/xhtml}img'))) == 6
    assert 'PTE RE AE' not in ''.join(root.itertext())
    assert 'View larger' in ''.join(root.itertext())


def test_badly_damaged_head_requires_five_local_placements_and_repeated_label_template():
    raws = [_scan_spread(i,text) for i,text in enumerate([
        'Pith RENT Vo lRerbary', 'THE REAL ASTROLOGY', 'THE REAL ASTROLOGY',
        'THE REAL ASTROLOGY', 'THE REAL ASTROLOGY'])]
    style = skeleton.book_style(raws)
    assert len(style.scan_spread_head_boxes[0]) == 2
    # Two plausible neighbors cannot turn a different source label into a head.
    assert 0 not in skeleton.book_style(raws[:3]).scan_spread_head_boxes
    # Repetition far away does not establish this page's local template.
    for raw in raws[1:]:raw.pno += 20
    assert 0 not in skeleton.book_style(raws).scan_spread_head_boxes


@pytest.mark.parametrize('long_rows',[False,True])
def test_repeated_column_labels_stay_attached_to_short_table_rows(long_rows):
    # Independent review reproduced a loss of context which token counts miss.
    left = ['Planets','Planerz','Planeta','Planetr','Planety']
    right = ['Houses','Houzes','Housey','Housen','Houser']
    raws = []
    for i in range(5):
        lines = []
        for x,text in ((35,left[i]),(660,right[i])):
            box = (x,20,x+125,26)
            lines.append(extract.Line([extract.Span(text,6,'OCR',0,box)],box))
        for row in range(6):
            texts = ((35,'Mars %d in Aries'%(row+1)),(450,'%dth house: action'%(row+1)))
            if long_rows:
                texts = ((35,'Mars is the planetary symbol used for dynamic action.'),
                         (450,'This house describes a separate area of human experience.'))
            for x,text in texts:
                box = (x,38+row*12,x+335,50+row*12)
                lines.append(extract.Line([extract.Span(text,11,'OCR',0,box)],box))
        raws.append(extract.RawPage(i,842,595,[extract.Block(0,(35,20,785,128),lines)],
                    images=[extract.Image((0,0,842,595),1.0)]))
    style = skeleton.book_style(raws)
    assert not style.scan_spread_head_boxes
    skels = [skeleton.page_skeleton(raw,style) for raw in raws]
    for raw,skel in zip(raws,skels):
        labels = {id(line) for line in raw.blocks[0].lines[:2]}
        assert not any(region.kind=='furniture' and any(id(line) in labels for line in region.lines)
                       for region in skel.regions)
    book = assemble.assemble(skels,style,raw_pages=raws)
    assert book.conservation.ok, book.conservation.to_dict()


def test_staggered_spread_heads_retain_left_then_right_source_order():
    from dataclasses import replace
    raws = [_scan_spread(i,'THE REAL ASTROLOGY') for i in range(3)]
    for raw in raws:
        line = raw.blocks[0].lines[7]
        box = (line.bbox[0],19.75,line.bbox[2],25.75)
        raw.blocks[0].lines[7] = replace(line,bbox=box,
             spans=[replace(span,bbox=box) for span in line.spans])
    style = skeleton.book_style(raws)
    skels = [skeleton.page_skeleton(raw,style) for raw in raws]
    for skel in skels:
        heads = [region for region in skel.regions if region.reason=='repeated_scan_spread_head']
        assert [region.bbox[0] for region in heads] == [35,450]
    book = assemble.assemble(skels,style,raw_pages=raws)
    assert book.conservation.ok, book.conservation.to_dict()
    for inventory in book.source_inventory.values():
        heads = [region for region in inventory['regions'] if region['reason']=='repeated_scan_spread_head']
        assert [region['bbox'][0] for region in heads] == [35,450]


@pytest.mark.parametrize('kind', ['body-sized', 'touching', 'unrelated', 'single-page', 'native'])
def test_spread_head_proof_does_not_remove_real_content(kind):
    texts = ['28 PTE RE AE VSD Rey', 'ame) HHE REAL Ved Robey', '26 PTE RE AE VSD Robey']
    if kind == 'unrelated': texts = ['Original source first label', 'Distinct words second title', 'Another unrelated third heading']
    raws = [_scan_spread(i, text, height=12 if kind=='body-sized' else 6,
                         gap=0 if kind=='touching' else 12) for i,text in enumerate(texts)]
    if kind == 'single-page': raws = raws[:1]
    if kind == 'native':
        for raw in raws: raw.images = []
    style = skeleton.book_style(raws)
    assert not getattr(style, 'scan_spread_head_boxes', {})


def test_scan_heads_publish_original_pixels_and_return_outside_reading_prose(tmp_path):
    import zipfile
    import pymupdf
    raws = [_scan_spread(i,text) for i,text in enumerate([
        '28 PTE RE AE VSD Rey', 'ame) HHE REAL Ved Robey', '26 PTE RE AE VSD Robey'])]
    style = skeleton.book_style(raws)
    book = assemble.assemble([skeleton.page_skeleton(raw,style) for raw in raws],style,raw_pages=raws)
    doc = pymupdf.open()
    try:
        for raw in raws:
            page = doc.new_page(width=raw.width,height=raw.height)
            for line in raw.blocks[0].lines:
                page.insert_text((line.bbox[0],line.bbox[3]),line.text,
                                 fontsize=6 if line.bbox[1]==20 else 11)
        out = tmp_path/'scan-heads.epub'
        build_epub.build(book,str(out),doc=doc)
        with zipfile.ZipFile(out) as z:
            root = ET.fromstring(z.read('OEBPS/printed-furniture.xhtml'))
            imgs = list(root.iter('{http://www.w3.org/1999/xhtml}img'))
            assert len(imgs)==6
            for image in imgs:
                pix = pymupdf.Pixmap(z.read('OEBPS/'+image.attrib['src']))
                assert min(pix.samples)<100  # real lettering, not a blank placeholder
            links = list(root.iter('{http://www.w3.org/1999/xhtml}a'))
            for link in links:
                href,anchor = link.attrib['href'].split('#')
                target = ET.fromstring(z.read('OEBPS/'+href))
                assert target.find('.//*[@id="%s"]'%anchor) is not None
            chapters = ''.join(z.read(name).decode() for name in z.namelist()
                              if name.startswith('OEBPS/ch') and name.endswith('.xhtml'))
            assert 'PTE RE AE' not in chapters
            assert 'Ordinary source prose' in chapters
    finally:
        doc.close()


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

@pytest.mark.parametrize('control',[None,'no_folio','wrong_sequence','too_large','not_detached'])
def test_separate_progressing_folio_proves_a_running_head_despite_scan_size_noise(control):
    raws=[]
    for p in range(6):
        title=_line('Essays on the Source',90,37,11 if control=='too_large' else 9.1)
        title.bbox=(90,37,220,46.5);title.spans[0].bbox=title.bbox
        text='159' if control=='wrong_sequence' else str(156+p)
        folio=_line(text,352,37,8.8);folio.bbox=(352,37,367,46.5);folio.spans[0].bbox=folio.bbox
        body=_line('The ordinary source paragraph continues in the same source order.',42,49 if control=='not_detached' else 74,8.6)
        lines=[title,body] if control=='no_folio' else [title,folio,body]
        raws.append(extract.RawPage(p,415,638,[extract.Block(i,l.bbox,[l]) for i,l in enumerate(lines)]))
    style=skeleton.book_style(raws)
    skels=[skeleton.page_skeleton(r,style) for r in raws]
    book=assemble.assemble(skels,style,raws);assert book.conservation.ok
    for r,s in zip(raws,skels):
        furniture=[x.text for x in s.regions if x.kind=='furniture']
        assert ('Essays on the Source' in furniture) is (control is None)
        if control is None:assert str(156+r.pno) in furniture

@pytest.mark.parametrize('left,right,expected',[
    ('I believe that the','7th house is more accurately described.',1),
    ('This was a','21st century account.',1),
    ('The source sentence ends.','7th house is more accurately described.',0),
    ('The next source section','7th house is more accurately described.',0),
    ('I believe that the','7th House',0),
])
def test_article_and_printed_ordinal_continue_without_furniture_intrusion(left,right,expected):
    notice='<p class="source-evidence-notice">Open original page.</p>'
    pages=build_epub._page_blocks({0:'<p>'+left+'</p>'+notice,1:'<p>'+right+'</p>'})
    assert build_epub._join_page_turns(pages)==expected
    if expected:
        root=ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+pages[0]['body'][0]+'</root>')
        assert ''.join(root.itertext())==left+' '+right
        assert pages[0]['body'][1]==notice
        assert root.find('.//*[@id="pg_0001"]') is not None


def test_printed_superscript_ordinal_can_continue_after_an_article():
    pages=build_epub._page_blocks({0:'<p>I believe that the</p>',1:'<p>7<sup>th</sup> house is more accurately described.</p>'})
    assert build_epub.block_text(pages[1]['body'][0]).startswith('7 th house')
    assert build_epub._join_page_turns(pages)==1
    root=ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+pages[0]['body'][0]+'</root>')
    assert ''.join(root.itertext())=='I believe that the 7th house is more accurately described.'
    assert root.find('.//sup').text=='th'


def test_an_article_can_continue_with_a_printed_proper_noun():
    pages=build_epub._page_blocks({0:'<p>the third place was known to the</p>',1:'<p>Greeks as the house of the Moon Goddess.</p>'})
    assert build_epub._join_page_turns(pages)==1
    root=ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+pages[0]['body'][0]+'</root>')
    assert ''.join(root.itertext())=='the third place was known to the Greeks as the house of the Moon Goddess.'


@pytest.mark.parametrize('left,right,joined', [
    ('The Sun lights the sky. The', 'Moon reflects its light.', True),
    ('The printed source continues with An', 'Example of the next phrase.', True),
    ('The source compares theory A', 'Moon observations follow.', False),
    ('This source sentence ends.', 'The Moon follows.', False),
])
def test_capitalized_articles_keep_native_fragments_in_their_source_paragraph(left, right, joined):
    first = assemble.Element('p', [['t', left]], bbox=(40, 50, 350, 62), pages=[0], line_boxes=[(40,50,350,62)])
    second = assemble.Element('p', [['t', right]], bbox=(40, 66, 300, 78), pages=[0], line_boxes=[(40,66,300,78)])
    output = assemble._join_within_page([first, second], set())
    assert len(output) == (1 if joined else 2)
    assert [e.text for e in output] == ([left+' '+right] if joined else [left,right])
    if joined:
        assert output[0].line_boxes == [(40,50,350,62),(40,66,300,78)]


def test_capitalized_article_at_page_turn_retains_source_marker_and_note_channel():
    notice='<p class="source-evidence-notice">Open original page.</p>'
    pages=build_epub._page_blocks({0:'<p>The source continues. The</p>'+notice,1:'<p>Moon remains in the source paragraph.</p>'})
    assert build_epub._join_page_turns(pages)==1
    root=ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+pages[0]['body'][0]+'</root>')
    assert ''.join(root.itertext())=='The source continues. The Moon remains in the source paragraph.'
    assert root.find('.//*[@id="pg_0001"]') is not None
    assert pages[0]['body'][1]==notice
