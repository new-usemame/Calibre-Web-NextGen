"""Source object ownership for damaged scan transcripts."""
import pytest

from cps.services.reflow import assemble, build_epub, extract, skeleton


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
    assert 'Original text region' in html and 'Inspect original printed caption' in html


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
