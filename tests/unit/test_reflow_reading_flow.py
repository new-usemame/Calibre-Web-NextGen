"""Generated copy and printed notes never separate running source words."""
from xml.etree import ElementTree as ET
import pytest
from cps.services.reflow import assemble, build_epub, reading_flow, extract, skeleton

pytestmark = pytest.mark.unit


def opaque_page_fixture():
    def line(text, size, box):
        return extract.Line([extract.Span(text,size,'Times',0,box)],box)
    rows=[line('RUNNING HEADER',9,(80,25,230,35)),
          line('The source sentence is not yet complete and',11,(60,70,360,82)),
          line('the next page continues it after cotton-folding and',11,(60,86,360,98))]
    note=extract.Line([extract.Span('109',5,'Times',0,(64,148,74,154)),
                      extract.Span(' Original printed footnote.',9,'Times',0,(78,149,360,160))],(64,148,360,160))
    rows += [note,line('The footnote continues below its printed divider.',9,(60,164,360,175))]
    return extract.RawPage(0,420,600,[extract.Block(i,ln.bbox,[ln]) for i,ln in enumerate(rows)])


def test_text_masks_cannot_erase_the_printed_footnote_divider():
    import pymupdf
    doc=pymupdf.open()
    page=doc.new_page(width=420,height=600)
    page.draw_line((60,138),(130,138),width=.5)
    try:
        probe=extract.ScanPixelProbe(doc,0,mask=[(60,139,360,160)])
        assert probe.ink_bounds((50,130,370,145)) is None
        assert probe.source_ink_bounds((50,130,370,145)) is not None
        regions=skeleton.opaque_page_regions(opaque_page_fixture(),
                    skeleton.BookStyle(body_size=11),probe)
        assert any(r.reason=='ocr_uncertain_note' for r in regions)
    finally:
        doc.close()


def test_proven_midpage_footnotes_leave_the_unfinished_body_sentence():
    raw=opaque_page_fixture()
    class Probe:
        def ink_bounds(self,box):return None
        def source_ink_bounds(self,box):return (60,136,130,138)
    body,notes=skeleton._split_off_notes(raw,skeleton.BookStyle(body_size=11),
                    skeleton.PageSkeleton(0,420,600),Probe())
    assert [n.number for n in notes]==[109]
    assert 'cotton-folding and' in ' '.join(b.text for b in body)
    assert 'Original printed footnote' not in ' '.join(b.text for b in body)
    assert len([ln for b in body for ln in b.lines])+sum(len(n.lines) for n in notes)==5


@pytest.mark.parametrize('boundary',[None,'word_spans','no_rule','large_rule','body_size','body_below','word_opening'])
def test_merged_ocr_note_opening_needs_a_printed_divider_and_stays_unnumbered(boundary):
    raw=opaque_page_fixture()
    opening=raw.blocks[3].lines[0]
    # The PDF's OCR says 167; geometry cannot establish the printed 157.
    opening.spans=[extract.Span('167 Original printed footnote.',9,'ocr',0,opening.bbox)]
    if boundary=='word_spans':
        opening.spans=[extract.Span(text,9,'ocr',0,opening.bbox)
                       for text in ('167',' ','Original printed footnote.')]
    if boundary=='body_size':opening.spans[0].size=11
    if boundary=='word_opening':opening.spans[0].text='This is a smaller quotation.'
    if boundary=='body_below':raw.blocks[-1].lines[0].spans[0].size=11
    class Probe:
        def ink_bounds(self,box):
            return None if boundary=='no_rule' else ((60,120,300,143) if boundary=='large_rule' else (60,136,130,138))
    regions=skeleton.opaque_page_regions(raw,skeleton.BookStyle(body_size=11),Probe())
    assert any(r.reason=='ocr_uncertain_note' for r in regions)==(boundary in (None,'word_spans'))
    if boundary not in (None,'word_spans'):return
    body,notes=skeleton._split_off_notes(raw,skeleton.BookStyle(body_size=11),skeleton.PageSkeleton(0,420,600),Probe())
    assert notes and all(n.number is None for n in notes)
    assert notes[0].reason=='pixel_note_territory'
    sk=skeleton.PageSkeleton(0,420,600,is_scan=True,regions=[
        skeleton.Region('body',lines=b.lines,bbox=b.bbox) for b in body]+notes)
    book=assemble.assemble([sk],skeleton.BookStyle(body_size=11),[raw])
    assert book.conservation.ok and book.notes[0].uses_source_image
    html=build_epub.page_fragment(book,0)
    assert '167' not in html and '157' not in html
    assert '<aside class="footnote"' in html and '<img' in html


@pytest.mark.parametrize('ink,notes', [((60,136,130,138),True),
                                      (None,False),((60,120,300,143),False)])
def test_untrusted_page_pixels_separate_only_proven_note_territory(ink,notes):
    raw=opaque_page_fixture()
    class Probe:
        def ink_bounds(self,box):return ink
    regions=skeleton.opaque_page_regions(raw,skeleton.BookStyle(body_size=11),Probe())
    figures=[r for r in regions if r.kind=='figure']
    assert any(r.reason=='ocr_uncertain_note' for r in figures)==notes
    # Crops cover every source pixel once; no OCR word or note digit is adopted.
    assert figures[0].bbox[1]==0 and figures[-1].bbox[3]==raw.height
    assert all(a.bbox[3]==b.bbox[1] for a,b in zip(figures,figures[1:]))
    owned=[ln for r in regions if r.kind=='artwork' for ln in r.lines]
    assert sorted(map(id,owned))==sorted(id(ln) for b in raw.blocks for ln in b.lines)
    book=assemble.assemble([skeleton.PageSkeleton(0,raw.width,raw.height,regions=regions)],
                          skeleton.BookStyle(body_size=11),[raw])
    blocks=build_epub.split_blocks(build_epub.page_fragment(book,0))
    chapter=build_epub.Chapter(1,'',blocks=blocks+['<p>basket weaving finishes the sentence.</p>'])
    reading_flow.finish([chapter],book)
    assert 'source-notes' in chapter.blocks[-1]
    assert not any('<aside' in block for block in chapter.blocks[:-1])
    assert book.conservation.ok


def test_disclosures_wait_for_completed_prose_across_raster_and_chapter_boundaries():
    book = assemble.Book()
    book.figures = [dict(pno=0, bbox=(0, 0, 200, 20), found='ocr_uncertain_region')]
    book.artwork = [dict(pno=0, bbox=(0, 0, 200, 20), text='struct this cabinet.')]
    chapters = [build_epub.Chapter(1, '', blocks=[
        '<p>We shall con-</p>',
        '<p class="source-evidence-notice">Punctuation uncertain</p>',
        '<aside class="footnote" id="fn_3"><p>3 Printed note.</p></aside>']),
        build_epub.Chapter(2, '', blocks=[
            '<p><span class="source-raster"><img src="images/fig_p0000_0.jpg"/></span></p>',
            '<p>aligned with the dial at</p>',
            '<p class="source-evidence-notice">View original passage</p>',
            '<p>24° Clockwise.</p>'])]
    audit = reading_flow.finish(chapters, book)
    assert chapters[0].blocks == ['<p>We shall con-</p>']
    assert chapters[1].blocks[:3] == [
        '<p><span class="source-raster"><img src="images/fig_p0000_0.jpg"/></span></p>',
        '<p>aligned with the dial at</p>', '<p>24° Clockwise.</p>']
    deferred = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+chapters[1].blocks[3]+'</root>')[0]
    assert deferred.get('class') == 'source-notes'
    assert ''.join(deferred[0].itertext()) == 'Punctuation uncertain'
    assert deferred[1].get('id') == 'fn_3'
    assert ''.join(deferred[-1].itertext()) == 'View original passage'
    assert len(chapters[1].blocks) == 4
    assert audit['notes_checked'] == 1


def test_inline_generated_annotation_preserves_word_adjacency_and_return_target():
    chapter = build_epub.Chapter(1, '', blocks=[
        '<p>work<span class="source-reading-annotation" id="reading-return-test">'
        'Reviewed reading <a href="original-p0000.xhtml">Inspect source</a></span>'
        'bench is complete.</p>'])
    reading_flow.finish([chapter], assemble.Book())
    root = ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+''.join(chapter.blocks)+'</root>')
    assert ''.join(root[0].itertext()) == 'workbench is complete.'
    assert root[1].get('class') == 'source-notes'
    assert root[1][0].get('class') == 'source-evidence-notice'
    assert root[1][0].find('span').get('id') == 'reading-return-test'
    assert root[1][0].find('span/a').get('href') == 'original-p0000.xhtml'


def test_raster_notes_live_in_notes_section_with_their_targets_and_returns():
    note='<aside class="footnote" id="fn_p0355_10"><p>10 <img src="images/note.jpg"/></p><a href="ch001.xhtml#reference">Return</a></aside>'
    chapters=[build_epub.Chapter(1,'',blocks=['<p id="reference">The complete source paragraph.</p>',note,'<p>Next source paragraph.</p>'])]
    audit=reading_flow.finish(chapters,assemble.Book())
    assert chapters[0].blocks[:2]==['<p id="reference">The complete source paragraph.</p>','<p>Next source paragraph.</p>']
    assert 'class="source-notes"' in chapters[0].blocks[-1]
    assert note in chapters[0].blocks[-1]
    assert audit['raster_notes_in_notes_section']==1


def test_opaque_prose_ocr_period_does_not_release_notices_inside_printed_sentence():
    book=assemble.Book()
    book.figures=[dict(pno=0,bbox=(0,0,200,20),found='ocr_uncertain_region')]
    book.artwork=[dict(pno=0,bbox=(0,0,200,20),text='Ash supports this.')]
    chapter=build_epub.Chapter(1,'',blocks=[
        '<p><span class="source-raster"><img src="images/fig_p0000_0.jpg"/></span></p>',
        '<p class="source-evidence-notice">OCR uncertain</p>',
        '<p>certificates. The complete live paragraph.</p>'])
    reading_flow.finish([chapter],book)
    assert chapter.blocks[1]=='<p>certificates. The complete live paragraph.</p>'
    deferred=ET.fromstring('<root xmlns:epub="http://www.idpf.org/2007/ops">'+chapter.blocks[2]+'</root>')[0]
    assert deferred.get('class')=='source-notes'
    assert ET.tostring(deferred[0],encoding='unicode')=='<p class="source-evidence-notice">OCR uncertain</p>'


def test_uncertain_footer_crop_can_overlap_neighbouring_live_source_notes():
    body_box=(48,516,384,539)
    body=extract.Line([extract.Span('The unfinished running source sentence continues',11,'ocr',0,body_box)],body_box)
    boxes=[(56,553,288,563),(56,565,288,575),(56,577,288,587)]
    lines=[extract.Line([extract.Span('3 ',4,'ocr',0,(56,b[1],60,b[1]+4)),
        extract.Span('Printed source note.',8,'ocr',0,(61,b[1],288,b[3]))],b) for b in boxes]
    crop=(53,561,292,579)
    raw=extract.RawPage(0,840,620,[extract.Block(i,l.bbox,[l]) for i,l in enumerate([body]+lines)])
    regions=[skeleton.Region('body',lines=[body],bbox=body_box),
        skeleton.Region('note',lines=[lines[0]],bbox=boxes[0]),
        skeleton.Region('figure',bbox=crop,reason='ocr_uncertain_region'),
        skeleton.Region('artwork',lines=[lines[1]],bbox=boxes[1],reason='ocr_uncertain_region'),
        skeleton.Region('note',lines=[lines[2]],bbox=boxes[2])]
    book=assemble.assemble([skeleton.PageSkeleton(0,840,620,regions=regions)],skeleton.BookStyle(body_size=11),[raw])
    assert assemble.source_note_context_figures(book,0)=={1}
    assert book.conservation.ok


def test_generated_caption_disclosures_do_not_interrupt_a_hyphen_join():
    chapter=build_epub.Chapter(1,'',blocks=[
        '<p>The peg is labeled cross-</p>',
        '<figure><img src="images/chart.png"/><figcaption>Printed chart caption. '
        '<span class="source-reading-annotation">Original table · View larger</span>'
        '</figcaption></figure>',
        '<aside class="footnote"><p>Printed note '
        '<span class="source-reading-annotation">Inspect source</span>continues.</p></aside>',
        '<p>braced in the original source.</p>'])
    reading_flow.finish([chapter],assemble.Book())
    assert 'Original table' not in chapter.blocks[1]
    assert chapter.blocks[2]=='<p>braced in the original source.</p>'
    assert any('Printed note continues.' in b for b in chapter.blocks[3:])
    assert any('Original table' in b for b in chapter.blocks[3:])


def test_padded_uncertain_footer_image_is_a_note_without_transcribing_its_digit():
    body_box = (48, 516, 384, 539)
    body = extract.Line([extract.Span('each peg beneath this wooden fixture', 11, 'ocr', 0, body_box)], body_box)
    note_box = (56, 553, 288, 562)
    note = extract.Line([
        extract.Span('3 ', 5.33, 'ocr', 0, (56, 553, 60, 557)),
        extract.Span('Taken from Synthetic Fixture Plans, 1900-1', 8.67, 'ocr', 0, (61, 553, 288, 562))], note_box)
    crop_box = (53, 550, 292, 566)
    raw = extract.RawPage(0, 840, 600, [extract.Block(0, body_box, [body]), extract.Block(1, note_box, [note])])
    skel = skeleton.PageSkeleton(0, 840, 600, regions=[
        skeleton.Region('body', lines=[body], bbox=body_box),
        skeleton.Region('figure', bbox=crop_box, reason='ocr_uncertain_region'),
        skeleton.Region('artwork', lines=[note], bbox=note_box, reason='ocr_uncertain_region')])
    book = assemble.assemble([skel], skeleton.BookStyle(body_size=11), [raw])
    assert book.source_inventory[0]['assets'][0]['covered_line_ids'] == []
    assert assemble.source_note_context_figures(book, 0) == {1}
    blocks = build_epub.split_blocks(build_epub.page_fragment(book, 0))
    assert any(b.startswith('<aside class="source-note-context">') for b in blocks)
    assert book.conservation.ok and not book.notes
    # The same numbered image within continuing body territory is not a note.
    later_box = (48, 570, 384, 590)
    later = extract.Line([extract.Span('Continuing prose beneath the image.', 11, 'ocr', 0, later_box)], later_box)
    raw.blocks.append(extract.Block(2, later_box, [later]))
    skel.regions.append(skeleton.Region('body', lines=[later], bbox=later_box))
    continuing = assemble.assemble([skel], skeleton.BookStyle(body_size=11), [raw])
    assert assemble.source_note_context_figures(continuing, 0) == set()


def test_adjacent_uncertain_footer_notes_allow_padding_overlap_and_damaged_raised_marker():
    body_box=(48,516,384,539)
    body=extract.Line([extract.Span('The unfinished source sentence continues',11,'ocr',0,body_box)],body_box)
    first_box=(56,553,288,562);second_box=(56,564.5,288,574)
    first=extract.Line([extract.Span('* ',4,'ocr',0,(56,553,60,557)),
        extract.Span('Printed source note without terminal punctuation',8,'ocr',0,(61,553,288,562))],first_box)
    second=extract.Line([extract.Span("' ",3,'ocr',0,(56,564.5,60,568)),
        extract.Span('Another printed source note.',8,'ocr',0,(61,564.5,288,574))],second_box)
    raw=extract.RawPage(0,840,600,[extract.Block(i,l.bbox,[l]) for i,l in enumerate([body,first,second])])
    regions=[skeleton.Region('body',lines=[body],bbox=body_box)]
    for line,box in [(first,(53,550,292,566)),(second,(53,562,292,578))]:
        regions.extend([skeleton.Region('figure',bbox=box,reason='ocr_uncertain_region'),
            skeleton.Region('artwork',lines=[line],bbox=line.bbox,reason='ocr_uncertain_region')])
    book=assemble.assemble([skeleton.PageSkeleton(0,840,600,regions=regions)],skeleton.BookStyle(body_size=11),[raw])
    assert assemble.source_note_context_figures(book,0)=={1,2}
    assert book.conservation.ok and not book.notes


def test_known_footer_notes_bound_damaged_intervening_note_without_adopting_its_marker():
    body_box=(48,516,384,539)
    body=extract.Line([extract.Span('The unfinished source sentence continues',11,'ocr',0,body_box)],body_box)
    boxes=[(56,552,288,561),(56,568,288,578),(56,600,288,609)]
    texts=[('9 ',4,'Known opening note.'),(', ',17,'Damaged intervening printed note.'),('17 ',4,'Known closing note.')]
    lines=[extract.Line([extract.Span(mark,size,'ocr',0,box),extract.Span(text,8,'ocr',0,box)],box)
        for box,(mark,size,text) in zip(boxes,texts)]
    raw=extract.RawPage(0,840,620,[extract.Block(i,l.bbox,[l]) for i,l in enumerate([body]+lines)])
    regions=[skeleton.Region('body',lines=[body],bbox=body_box)]
    for i,line in enumerate(lines):
        reason='ocr_uncertain_region' if i==1 else 'ocr_uncertain_note'
        regions.extend([skeleton.Region('figure',bbox=line.bbox,reason=reason),
            skeleton.Region('artwork',lines=[line],bbox=line.bbox,reason=reason)])
    book=assemble.assemble([skeleton.PageSkeleton(0,840,620,regions=regions)],skeleton.BookStyle(body_size=11),[raw])
    assert assemble.source_note_context_figures(book,0)=={1,2,3}
    assert book.conservation.ok and not book.notes
    # One known note does not prove a bounded cluster for the damaged marker.
    book.figures[-1]['found']='ocr_uncertain_region'
    assert 2 not in assemble.source_note_context_figures(book,0)
