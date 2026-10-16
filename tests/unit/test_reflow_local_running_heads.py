"""Short, alternating folio-bearing margin rows need local recurrence proof."""
from cps.services.reflow import extract, skeleton


def _line(text, size, box):
    return extract.Line([extract.Span(text, size, 'Times-Roman', 0, box)], box)


def _page(pno, head=None, box=(43, 36.5, 250, 47.6), body='Ordinary body text continues here.'):
    lines = []
    if head is not None:
        lines.append(extract.Block(0, box, [_line(head, 10, box)]))
    body_box = (43, 82, 365, 94)
    lines.append(extract.Block(1, body_box, [_line(body, 11, body_box)]))
    return extract.RawPage(pno, 410, 648, blocks=lines)


def _style(pages):
    style = skeleton.book_style(pages)
    style.page_count = 255  # the whole-book threshold must still reject three hits
    return style


def _top(raw, style):
    page = skeleton.page_skeleton(raw, style)
    return [(region.kind, region.reason, region.text) for region in page.regions
            if region.lines and region.bbox[1] < 48]


def test_alternating_short_title_case_heads_on_either_folio_edge():
    pages = [_page(i) for i in range(7)]
    for pno in (0, 2, 4):
        pages[pno] = _page(pno, f'{50+pno} Example Theme')
    for pno in (1, 3, 5):
        pages[pno] = _page(pno, f'Chapter Notes {80+pno}', box=(235, 36.5, 367, 47.6))
    style = _style(pages)
    assert style.boiler_threshold == 51
    for raw in pages[:6]:
        assert _top(raw, style) == [('furniture', 'local_running_folio',
                                     raw.text_blocks[0].lines[0].stripped)]
    assert not _top(pages[6], style)


def test_two_hits_bad_progression_and_geometry_do_not_discard_body():
    two = [_page(i) for i in range(5)]
    for pno in (0, 2):
        two[pno] = _page(pno, f'{30+pno} Sparse Header')
    style = _style(two)
    assert all(_top(two[pno], style)[0][0] == 'body' for pno in (0, 2))

    wrong = [_page(i) for i in range(7)]
    for pno, number in ((0, 40), (2, 42), (4, 99)):
        wrong[pno] = _page(pno, f'{number} Inconsistent Header')
    style = _style(wrong)
    assert all(_top(wrong[pno], style)[0][0] == 'body' for pno in (0, 2, 4))

    shifted = [_page(i) for i in range(7)]
    for pno in (0, 2, 4):
        box = (43, 36.5, 250, 47.6) if pno != 4 else (43, 24, 250, 35)
        shifted[pno] = _page(pno, f'{40+pno} Shifted Header', box=box)
    style = _style(shifted)
    assert all(_top(shifted[pno], style)[0][0] == 'body' for pno in (0, 2, 4))


def test_numbered_body_heading_and_matching_words_outside_margin_survive():
    pages = [_page(i) for i in range(7)]
    for pno in (0, 2, 4):
        pages[pno] = _page(pno, f'{50+pno} Example Theme')
    style = _style(pages)
    body = _page(6, '56 Example Theme', box=(43, 88, 250, 101),
                 body='A different page has a genuine numbered body heading.')
    assert any(region.text == '56 Example Theme' and region.kind != 'furniture'
               for region in skeleton.page_skeleton(body, style).regions)
