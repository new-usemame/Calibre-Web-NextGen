"""Source object ownership for damaged scan transcripts."""
import pytest
import pymupdf
import zipfile
import io
from types import SimpleNamespace
from PIL import Image

from cps.services.reflow import assemble, build_epub, extract, skeleton


def _large_line(text, box):
    return extract.Line([extract.Span(text, 24, 'ocr', 0, box, box[3])], box)


def _contains(outer, inner):
    return all((outer[0] <= inner[0], outer[1] <= inner[1],
                outer[2] >= inner[2], outer[3] >= inner[3]))


def test_absorbed_partial_heading_stays_wholly_in_its_primary_crop():
    title = _large_line('A source heading', (519.36, 125.04, 722.16, 149.28))
    candidate = skeleton.Region('figure', bbox=(546.48, 44.64, 796.48, 193.68),
                                reason='scan_figure_side')
    block = extract.Block(0, title.bbox, [title])
    rest, art = skeleton._absorb_figure_content([(block, [title])], [candidate],
                                                SimpleNamespace(body_size=11))
    assert not rest and any(title in region.lines for region in art)
    assert _contains(candidate.bbox, title.bbox)


def test_weak_overlap_prose_and_neighbor_figure_keep_independent_ownership():
    title = _large_line('A source heading', (519.36, 125.04, 722.16, 149.28))
    prose = line('Ordinary adjacent words remain prose.', 448, 182, 105)
    weak = _large_line('Detached heading', (515, 205, 700, 226))
    main = skeleton.Region('figure', bbox=(546.48, 44.64, 796.48, 193.68),
                           reason='scan_figure_side')
    neighbor = skeleton.Region('figure', bbox=(448.95, 176.07, 550.89, 195.45),
                               reason='scan_figure_side')
    blocks = [(extract.Block(i, item.bbox, [item]), [item])
              for i, item in enumerate((title, prose, weak))]
    class BlankTail:
        def source_has_ink(self, rect): return False
    rest, art = skeleton._absorb_figure_content(blocks, [main, neighbor],
        SimpleNamespace(body_size=11), raw=scan([title, prose, weak]),
        pixel_probe=BlankTail())
    assert _contains(main.bbox, title.bbox)
    assert main.bbox[3] >= 193.68
    assert main.reason == 'unverified_scan_layout'
    assert prose in [ln for _, lines in rest for ln in lines]
    assert weak in [ln for _, lines in rest for ln in lines]
    assert not neighbor.lines


@pytest.mark.parametrize('answer', [False, True, None])
def test_adjacent_source_line_keeps_complete_source_region(answer):
    title = _large_line('A source heading', (519, 125, 722, 149))
    following = line('Independent quotation', 448, 180, 104)
    candidate = skeleton.Region('figure', bbox=(546, 44, 796, 194),
                                reason='scan_figure_side')
    blocks = [(extract.Block(i,item.bbox,[item]),[item])
              for i,item in enumerate((title,following))]
    class Probe:
        def source_has_ink(self, rect): return answer
    probe = Probe() if answer is not None else None
    rest,_ = skeleton._absorb_figure_content(blocks,[candidate],
        SimpleNamespace(body_size=11),raw=scan([title,following]),pixel_probe=probe)
    assert _contains(candidate.bbox,title.bbox) and candidate.bbox[3]==194
    assert following in [ln for _,lines in rest for ln in lines]
    assert candidate.reason=='unverified_scan_layout'
    assert candidate.needs_ink is False


def test_removed_figure_area_below_narrow_neighbor_keeps_printed_ink(tmp_path):
    """The independent finite-PDF counterexample: the old probes missed this strip."""
    title = _large_line('A complete title', (80, 100, 250, 124))
    following = line('Separate quotation', 40, 180, 100)
    candidate = skeleton.Region('figure', bbox=(100, 40, 300, 220),
                                reason='scan_figure_side')
    raw = scan([title, following], width=400, height=300)
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=300)
        page.draw_rect((110, 202, 135, 216), fill=(0, 0, 0), color=None)
        doc.save(tmp_path / 'printed-ink.pdf')
        probe = extract.ScanPixelProbe(doc, 0)
        assert probe.source_has_ink((100, 194, 140, 220))
        skeleton._absorb_figure_content(
            [(block, block.lines) for block in raw.blocks], [candidate],
            SimpleNamespace(body_size=11), raw=raw, pixel_probe=probe)
    assert candidate.bbox[3] >= 216
    assert candidate.reason == 'unverified_scan_layout'


@pytest.mark.parametrize('kind', ['gray_rule', 'gray_symbol', 'color_rule'])
def test_faint_encoded_source_marks_survive_full_primary_crop(tmp_path, kind):
    title = _large_line('A complete title', (80, 100, 250, 124))
    quote = line('Separate quotation', 40, 180, 100)
    main = skeleton.Region('figure', bbox=(100, 40, 300, 220),
                           reason='scan_figure_side')
    raw = scan([title, quote], width=400, height=300)
    raw.source_geometry = {}
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=300)
        if kind == 'gray_rule':
            page.draw_line((110, 207), (190, 207), color=(.65, .65, .65), width=.45)
        elif kind == 'gray_symbol':
            page.insert_text((110, 213), 'X', fontsize=12, color=(.85, .85, .85))
        else:
            page.draw_line((110, 207), (190, 207), color=(.35, .8, .85), width=.45)
        rest, art = skeleton._absorb_figure_content(
            [(block, block.lines) for block in raw.blocks], [main],
            SimpleNamespace(body_size=11), raw=raw,
            pixel_probe=extract.ScanPixelProbe(doc, 0))
        assert main.bbox[3] == 220
        regions = [main] + art + [skeleton.Region('body', lines=group, bbox=block.bbox)
                                  for block, group in rest]
        skel = skeleton.PageSkeleton(0, 400, 300, regions=regions)
        book = assemble.assemble([skel], skeleton.book_style([raw]), [raw])
        target = tmp_path / ('faint-' + kind + '.epub')
        build_epub.build(book, target, doc=doc)
        sample = extract.crop_jpeg(doc, 0, (100, 194, 200, 220))
    assert min(Image.open(io.BytesIO(sample)).convert('L').getdata()) < 240
    assert build_epub.validate(str(target)) == []
    with zipfile.ZipFile(target) as z:
        encoded = z.read('OEBPS/images/fig_p0000_0.jpg')
        assert 'images/fig_p0000_0.jpg' in z.read('OEBPS/ch001.xhtml').decode()
    assert min(Image.open(io.BytesIO(encoded)).convert('L').getdata()) < 240


@pytest.mark.parametrize('retain_prose_mask', [False, True])
def test_full_primary_source_image_survives_peer_masking_in_real_epub(
        tmp_path, retain_prose_mask):
    title = _large_line('A complete title', (80, 100, 250, 124))
    quote = line('Separate quotation', 40, 180, 100)
    raw = scan([title, quote], width=400, height=300)
    raw.source_geometry = {}
    main = skeleton.Region('figure', bbox=(100, 40, 300, 220),
                           reason='scan_figure_side', needs_ink=True)
    peer = skeleton.Region('figure', bbox=(90, 180, 140, 189),
                           reason='scan_figure_side', needs_ink=True)
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=300)
        page.draw_rect((105, 181, 130, 188), fill=(0, 0, 0), color=None)
        rest, art = skeleton._absorb_figure_content(
            [(block, block.lines) for block in raw.blocks], [main, peer],
            SimpleNamespace(body_size=11), raw=raw,
            pixel_probe=extract.ScanPixelProbe(doc, 0))
        regions = [main, peer] + art
        if retain_prose_mask:
            regions += [skeleton.Region('body', lines=group, bbox=block.bbox)
                        for block, group in rest]
        skel = skeleton.PageSkeleton(0, 400, 300, regions=regions)
        book = assemble.assemble([skel], skeleton.book_style([raw]), [raw])
        target = tmp_path / 'source-preservation.epub'
        build_epub.build(book, target, doc=doc)
        expected = extract.crop_jpeg(doc, 0, main.bbox)
    assert main.bbox[3] == 220 and main.needs_ink is False
    assert build_epub.validate(str(target)) == []
    with zipfile.ZipFile(target) as z:
        primary = z.read('OEBPS/images/fig_p0000_0.jpg')
        assert primary == expected
        chapter = z.read('OEBPS/ch001.xhtml').decode()
        assert 'images/fig_p0000_0.jpg' in chapter
        assert 'Unverified scan transcription' in chapter
        assert min(Image.open(io.BytesIO(primary)).convert('L').getdata()) < 100


@pytest.mark.parametrize('mark,color', [
    ((110, 202, 135, 216), (0, 0, 0)),     # below the neighbor
    ((80, 182, 89, 188), (0, 0, 0)),       # left strip
    ((160, 182, 190, 190), (0, 0, 0)),     # right strip
    ((150, 178, 180, 180), (0, 0, 0)),     # cut boundary
    ((110, 202, 135, 216), (.5, .5, .5)), # gray source mark
])
def test_removed_area_ink_outside_independent_source_owner_blocks_trim(mark, color):
    title = _large_line('A complete title', (80, 100, 250, 124))
    main = skeleton.Region('figure', bbox=(100, 40, 300, 220),
                           reason='scan_figure_side')
    neighbor = skeleton.Region('figure', bbox=(90, 180, 140, 189),
                               reason='ocr_uncertain_region')
    raw = scan([title], width=400, height=300)
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=300)
        page.draw_rect(mark, fill=color, color=None)
        probe = extract.ScanPixelProbe(doc, 0)
        assert probe.source_has_ink(mark)
        skeleton._absorb_figure_content(
            [(raw.blocks[0], [title])], [main, neighbor],
            SimpleNamespace(body_size=11), raw=raw, pixel_probe=probe)
    assert main.bbox[3] >= 220
    assert main.reason == 'unverified_scan_layout'


def test_neighbor_source_image_does_not_authorize_trimming_main_crop():
    title = _large_line('A complete title', (80, 100, 250, 124))
    main = skeleton.Region('figure', bbox=(100, 40, 300, 220),
                           reason='scan_figure_side')
    neighbor = skeleton.Region('figure', bbox=(90, 180, 140, 189),
                               reason='ocr_uncertain_region')
    raw = scan([title], width=400, height=300)
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=300)
        page.draw_rect((105, 181, 130, 188), fill=(0, 0, 0), color=None)
        probe = extract.ScanPixelProbe(doc, 0)
        skeleton._absorb_figure_content(
            [(raw.blocks[0], [title])], [main, neighbor],
            SimpleNamespace(body_size=11), raw=raw, pixel_probe=probe)
    assert main.bbox[3] == 220
    assert neighbor.bbox[0] <= 105 and neighbor.bbox[3] >= 188
    assert main.reason == 'unverified_scan_layout'
    assert main.needs_ink is False


@pytest.mark.parametrize('probe', [None, object()])
def test_missing_removed_area_probe_keeps_complete_source_crop(probe):
    title = _large_line('A complete title', (80, 100, 250, 124))
    following = line('Separate quotation', 40, 180, 100)
    main = skeleton.Region('figure', bbox=(100, 40, 300, 220),
                           reason='scan_figure_side')
    raw = scan([title, following], width=400, height=300)
    skeleton._absorb_figure_content(
        [(block, block.lines) for block in raw.blocks], [main],
        SimpleNamespace(body_size=11), raw=raw, pixel_probe=probe)
    assert main.bbox[3] == 220 and main.reason == 'unverified_scan_layout'


@pytest.mark.parametrize('error', [RuntimeError, extract.RasterTooLarge])
def test_removed_area_probe_error_keeps_complete_source_crop(error):
    class BrokenProbe:
        def source_has_ink(self, rect): raise error('render unavailable')
    title = _large_line('A complete title', (80, 100, 250, 124))
    following = line('Separate quotation', 40, 180, 100)
    main = skeleton.Region('figure', bbox=(100, 40, 300, 220),
                           reason='scan_figure_side')
    raw = scan([title, following], width=400, height=300)
    skeleton._absorb_figure_content(
        [(block, block.lines) for block in raw.blocks], [main],
        SimpleNamespace(body_size=11), raw=raw, pixel_probe=BrokenProbe())
    assert main.bbox[3] == 220 and main.reason == 'unverified_scan_layout'


def test_removed_area_proof_uses_rotated_reading_coordinates():
    from cps.services.reflow import ocr
    from cps.services.reflow.source_display import SourceDisplay
    title = _large_line('A complete title', (80, 100, 250, 124))
    following = line('Separate quotation', 40, 180, 100)
    main = skeleton.Region('figure', bbox=(100, 40, 300, 220),
                           reason='scan_figure_side')
    raw = scan([title, following], width=400, height=300)
    raw.source_geometry['orientation'] = 90
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=400)
        source_box = ocr._source_box((110, 202, 135, 216), 300, 400, 90)
        page.draw_rect(source_box, fill=(0, 0, 0), color=None)
        display = SourceDisplay(doc, 0, {'layer': 'ocr', 'orientation': 90,
                                         'source_rotation': 0,
                                         'page_rect': (0, 0, 300, 400)})
        with display.query_document(isolate=True) as reading:
            probe = extract.ScanPixelProbe(reading, 0)
            assert probe.source_has_ink((110, 202, 135, 216))
            skeleton._absorb_figure_content(
                [(block, block.lines) for block in raw.blocks], [main],
                SimpleNamespace(body_size=11), raw=raw, pixel_probe=probe)
    assert main.bbox[3] == 220 and main.reason == 'unverified_scan_layout'


@pytest.mark.parametrize('reverse', [False, True])
def test_competing_source_image_owners_never_justify_removing_unowned_ink(reverse):
    title = _large_line('A complete title', (80, 100, 250, 124))
    main = skeleton.Region('figure', bbox=(100, 40, 300, 220),
                           reason='scan_figure_side')
    neighbors = [skeleton.Region('figure', bbox=(90, 180, 135, 189)),
                 skeleton.Region('figure', bbox=(92, 180, 140, 190))]
    if reverse:
        neighbors.reverse()
    raw = scan([title], width=400, height=300)
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=300)
        page.draw_rect((160, 202, 190, 216), fill=(0, 0, 0), color=None)
        skeleton._absorb_figure_content(
            [(raw.blocks[0], [title])], [main] + neighbors,
            SimpleNamespace(body_size=11), raw=raw,
            pixel_probe=extract.ScanPixelProbe(doc, 0))
    assert main.bbox[3] == 220 and main.reason == 'unverified_scan_layout'


def test_fully_contained_lettering_needs_no_crop_growth():
    title = _large_line('Contained title',(560,100,700,124))
    candidate = skeleton.Region('figure',bbox=(546,44,796,194))
    skeleton._absorb_figure_content([(extract.Block(0,title.bbox,[title]),[title])],
                                    [candidate],SimpleNamespace(body_size=11))
    assert candidate.bbox==(546,44,796,194)


def test_competing_figure_ownership_uses_geometry_and_rejects_a_tie():
    title=_large_line('Shared printed label',(100,100,200,120))
    partial=skeleton.Region('figure',bbox=(115,90,210,140))
    whole=skeleton.Region('figure',bbox=(90,90,210,140))
    block=extract.Block(0,title.bbox,[title])
    rest,art=skeleton._absorb_figure_content([(block,[title])],[partial,whole],
                                              SimpleNamespace(body_size=11))
    assert not rest and len(art)==1 and _contains(whole.bbox,title.bbox)
    assert partial.bbox==(115,90,210,140)
    first=skeleton.Region('figure',bbox=(90,90,210,140))
    second=skeleton.Region('figure',bbox=(90,90,210,140))
    rest,art=skeleton._absorb_figure_content([(block,[title])],[first,second],
                                              SimpleNamespace(body_size=11),raw=scan([title]))
    assert title in [ln for _,lines in rest for ln in lines] and not art
    assert title.transcription_uncertain


def test_expanded_crop_stays_within_upright_page_bounds():
    title=_large_line('Edge title',(795,40,845,60))
    candidate=skeleton.Region('figure',bbox=(800,30,840,100))
    raw=scan([title],width=840,height=595)
    skeleton._absorb_figure_content([(raw.blocks[0],[title])],[candidate],
                                    SimpleNamespace(body_size=11),raw=raw)
    assert candidate.bbox[2]==840 and candidate.bbox[0]<=title.bbox[0]


def line(text, x, y, width=95):
    box = (x, y, x + width, y + 9)
    return extract.Line([extract.Span(text, 9, 'ocr', 0, box, y + 9)], box)


def scan(lines, width=840, height=595):
    blocks = [extract.Block(i, item.bbox, [item]) for i, item in enumerate(lines)]
    return extract.RawPage(0, width, height, blocks=blocks,
        images=[extract.Image((0, 0, width, height), 1)],
        source_geometry={'space': 'reading'})


def test_damaged_aligned_list_owns_entire_measured_row_object():
    rows = [line(f'{i}th: item', 185, 120 + i * 12, 75) for i in range(1, 13)]
    rows[0].spans[0].text = 'ist: item'
    rows[4].spans[0].text = 'sth: item'
    body = line('A reliable prose sentence with many words and ordinary syntax.',
                460, 150, 300)
    raw = scan(rows + [body])
    got = skeleton._uncertain_aligned_scan_list(raw,
        [(block, block.lines) for block in raw.blocks])
    assert got and len(got[1]) == 12 and body not in got[1]
    assert got[0][1] < rows[0].bbox[1] and got[0][3] > rows[-1].bbox[3]


def test_accurate_mixed_list_fallback_does_not_claim_source_disagreement():
    labels = [f'{i}th: item' for i in range(1, 11)] + ['A: alternate', 'B: reserve']
    raw = scan([line(text, 185, 120 + i * 12, 100)
                for i, text in enumerate(labels)])
    style = skeleton.book_style([raw])
    book = assemble.assemble([skeleton.page_skeleton(raw, style)], style, [raw])
    html = build_epub.page_fragment(book, 0)
    assert 'Complete printed list' in html
    assert 'Some OCR labels disagree with the source' not in html
    assert 'label and value rows are preserved together as pixels' in html


def test_nested_scan_list_and_heading_have_one_primary_source_figure(monkeypatch, tmp_path):
    heading = line('An introduction to the printed list:', 59, 108, 238)
    rows = [line(f'{i}th: item', 185, 126 + i * 12, 75) for i in range(12)]
    rows[0].spans[0].text = 'ist: item'
    rows[4].spans[0].text = 'sth: item'
    prose = line('Ordinary prose continues below this complete source list.', 48, 290, 336)
    raw = scan([heading] + rows + [prose])
    broad_box = (44, 105, 447, 274)
    monkeypatch.setattr(skeleton, '_scan_figures', lambda *args:
        [skeleton.Region('figure', bbox=broad_box, reason='scan_figure_band')])
    style = skeleton.book_style([raw])
    skel = skeleton.page_skeleton(raw, style)
    figures = [region for region in skel.regions if region.kind == 'figure']
    assert len(figures) == 1
    assert figures[0].reason == 'uncertain_aligned_scan_list'
    assert figures[0].bbox[0] == broad_box[0]
    assert figures[0].bbox[1] <= heading.bbox[1]
    assert figures[0].bbox[3] >= rows[-1].bbox[3]
    assert len([line for region in skel.regions if region.kind == 'artwork'
                for line in region.lines if line in rows]) == 12
    book = assemble.assemble([skel], style, [raw])
    assert book.conservation.ok
    assert len(book.figures) == 1
    with pymupdf.open() as printed:
        page = printed.new_page(width=raw.width, height=raw.height)
        page.insert_text((59, 117), heading.text, fontsize=9)
        for i, row in enumerate(rows):
            page.insert_text((185, 135+i*12), row.text, fontsize=9)
        raster = page.get_pixmap().tobytes('png')
    with pymupdf.open() as doc:
        page = doc.new_page(width=raw.width, height=raw.height)
        page.insert_image(page.rect, stream=raster)
        target = tmp_path/'one-list.epub'
        build_epub.build(book, target, doc=doc)
        assert build_epub.validate(target) == []
        with zipfile.ZipFile(target) as package:
            html = package.read('OEBPS/ch001.xhtml').decode()
            assert html.count('Complete printed list.') == 1
            assert 'images/fig_p0000_0.jpg' in html
            assert 'images/fig_p0000_1.jpg' not in html
            assert package.read('OEBPS/images/fig_p0000_0.jpg') == extract.crop_jpeg(
                doc, 0, book.figures[0]['bbox'])


@pytest.mark.parametrize('carrier', [
    (0, 0, 840, 595),        # an intentional full-page view may coexist with detail
    (30, 20, 120, 100),      # an adjacent source object owns different ink
    (180, 120, 265, 280),   # no substantially wider heading-and-list object
    (44, 105, 220, 274),    # partial overlap may carry different source marks
    (300, 105, 447, 274),   # a separate column is a separate source object
])
def test_scan_list_crop_remains_when_other_figure_does_not_own_it(carrier):
    rows = [line(f'{i}th: item', 185, 120+i*12, 75) for i in range(12)]
    raw = scan(rows)
    inner = skeleton.Region('figure', bbox=(178, 116, 267, 262),
                            reason='uncertain_aligned_scan_list')
    other = skeleton.Region('figure', bbox=carrier, reason='ocr_uncertain_region')
    skel = skeleton.PageSkeleton(0, raw.width, raw.height, [inner, other], is_scan=True)
    skeleton._coalesce_nested_scan_list_figures(raw, skel)
    assert [region for region in skel.regions if region.kind == 'figure'] == [inner, other]


def test_two_measured_lists_keep_separate_source_owners():
    raw = scan([line(f'{i}th: item', 185, 120+i*12, 75) for i in range(12)])
    first = skeleton.Region('figure', bbox=(178, 116, 267, 262),
                            reason='uncertain_aligned_scan_list')
    second = skeleton.Region('figure', bbox=(480, 116, 569, 262),
                             reason='uncertain_aligned_scan_list')
    heading = skeleton.Region('figure', bbox=(44, 105, 447, 274),
                              reason='ocr_uncertain_region')
    skel = skeleton.PageSkeleton(0, raw.width, raw.height,
                                 [first, second, heading], is_scan=True)
    skeleton._coalesce_nested_scan_list_figures(raw, skel)
    assert first not in skel.regions
    assert second in skel.regions
    assert heading.bbox == (44, 105, 447, 274)
    assert heading.reason == 'uncertain_aligned_scan_list'


@pytest.mark.parametrize('change', ['reliable', 'letters', 'separate', 'prose'])
def test_aligned_list_does_not_claim_unproved_objects(change):
    rows = [line(f'{i}th: item', 185, 120 + i * 12, 75) for i in range(1, 13)]
    if change == 'letters':
        for i, row in enumerate(rows): row.spans[0].text = f'{chr(65+i)}: item'
    elif change == 'separate':
        for row in rows[6:]: row.bbox = (260, row.bbox[1], 335, row.bbox[3])
    elif change == 'prose':
        for i, row in enumerate(rows): row.spans[0].text = f'{i}th chapter has reliable prose words'
    else:
        pass
    raw = scan(rows)
    assert skeleton._uncertain_aligned_scan_list(raw,
        [(block, block.lines) for block in raw.blocks]) is None


def test_sparse_spread_keeps_two_whole_panels_in_left_to_right_order():
    left = [line('Imprint line', 70, 340 + i * 29, 210) for i in range(8)]
    right = [line('Chapter title', 450, 90 + i * 12, 260) for i in range(21)]
    raw = scan(left + right)
    panels = skeleton._sparse_scan_spread_panels(raw)
    assert panels and len(panels) == 2
    assert panels[0][0][0] == 0 and panels[1][0][2] == raw.width
    skel = skeleton.page_skeleton(raw, skeleton.book_style([raw]))
    figs = [r for r in skel.regions if r.kind == 'figure']
    assert len(figs) == 2 and figs[0].bbox[0] == 0 and figs[1].bbox[2] == raw.width


@pytest.mark.parametrize('change', ['prose', 'table', 'heading', 'adjacent', 'rotated'])
def test_sparse_spread_rejects_competing_layouts(change):
    left = [line('Long ordinary paragraph words continue across this column',
                 70, 100 + i * 16, 260) for i in range(12)]
    right = [line('Long ordinary paragraph words continue across this column',
                  450, 100 + i * 16, 260) for i in range(12)]
    if change == 'table':
        for row in left + right: row.spans[0].text = 'Cell'
    elif change == 'heading':
        left.append(line('Full width title', 100, 75, 650))
    elif change == 'adjacent':
        right = [line('Second panel', 310, 100 + i * 16, 260) for i in range(12)]
    elif change == 'rotated':
        raw = scan(left + right); raw.source_geometry = {'space': 'pdf'}
        assert skeleton._sparse_scan_spread_panels(raw) is None
        return
    raw = scan(left + right)
    assert skeleton._sparse_scan_spread_panels(raw) is None


def test_uncertain_caption_stays_with_primary_chart_and_disclosure():
    figure = assemble.Element('fig', pno=0, bbox=(20, 40, 400, 300))
    caption = assemble.Element('caption', pno=0, bbox=(100, 305, 250, 320),
        runs=[['t', 'Unverified caption']], caption_uncertain=True)
    book = assemble.Book(elements=[figure, caption], pages={0: [figure, caption]},
        figures=[{'pno': 0, 'bbox': figure.bbox, 'found': 'ocr_uncertain_region'}])
    html = build_epub.page_fragment(book, 0)
    assert html.count('original_p0000_caption_0.jpg') == 1
    assert html.index('fig_p0000_0') < html.index('original_p0000_caption_0.jpg')
    assert 'OCR uncertain' in html and 'Inspect original printed caption' in html


def test_complete_figure_crop_does_not_repeat_its_printed_caption():
    figure = assemble.Element('fig', pno=0, bbox=(20, 40, 400, 320))
    caption = assemble.Element('caption', pno=0, bbox=(100, 305, 250, 315),
        runs=[['t', 'Unverified caption']], caption_uncertain=True)
    book = assemble.Book(elements=[figure, caption], pages={0: [figure, caption]},
        figures=[{'pno': 0, 'bbox': figure.bbox, 'found': 'sparse_scan_spread_panel'}])
    html = build_epub.page_fragment(book, 0)
    assert 'fig_p0000_0' in html and 'Complete printed panel' in html
    assert 'original_p0000_caption_0.jpg' not in html


def test_ordinary_and_orphan_captions_keep_their_existing_routes():
    figure = assemble.Element('fig', pno=0, bbox=(20, 40, 400, 300))
    ordinary = assemble.Element('caption', pno=0, bbox=(100, 305, 250, 315),
        runs=[['t', 'Verified caption']])
    book = assemble.Book(elements=[figure, ordinary], pages={0: [figure, ordinary]},
        figures=[{'pno': 0, 'bbox': figure.bbox, 'found': ''}])
    assert 'Verified caption' in build_epub.page_fragment(book, 0)
    orphan = assemble.Element('caption', pno=0, bbox=(100, 405, 250, 415),
        runs=[['t', 'Unverified orphan']], caption_uncertain=True)
    book.pages[0] = [orphan, figure]
    html = build_epub.page_fragment(book, 0)
    assert 'original_p0000_caption_orphan.jpg' in html


def test_chart_top_grows_only_to_measured_ink_below_prior_figure():
    caption = line('Chart 1: Source title', 100, 305, 180)
    raw = scan([caption], 500, 600)
    prior = skeleton.Region('figure', bbox=(20, 15, 160, 25))
    chart = skeleton.Region('figure', bbox=(20, 45, 400, 302))
    skel = skeleton.PageSkeleton(0, 500, 600, regions=[prior, chart])
    class Probe:
        def ink_bounds(self, box):
            assert box[1] > prior.bbox[3]
            return (60, 36, 350, 47)
    skeleton._complete_captioned_scan_top(raw, skel, Probe())
    assert chart.bbox[1] == 34 and prior.bbox[1] == 15
    ordinary = skeleton.Region('figure', bbox=(20, 45, 400, 302))
    skel.regions = [ordinary]
    raw.blocks = []
    skeleton._complete_captioned_scan_top(raw, skel, Probe())
    assert ordinary.bbox[1] == 45
