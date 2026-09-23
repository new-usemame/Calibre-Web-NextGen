# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Stage 1's raster boundary.

The page image is rendered before it is measured, so an oversized page used to be
ALLOCATED before anyone asked how big it was, and the byte ceiling was consulted
only after the pixmap existed -- with the last, still-oversized attempt handed to
the caller anyway. The geometry tests here use fake pages whose pixmap calls are
recorded, so oversized geometry is exercised without allocating dangerous memory
on the host.
"""

import pymupdf
import pytest

from cps.services.reflow import extract, model
from tests.fixtures import reflow_pdfs as F

pytestmark = pytest.mark.unit


class _Pixmap(object):
    def __init__(self, data):
        self._data = data

    def tobytes(self, *_args, **_kwargs):
        return self._data


class _Page(object):
    """A page whose "pixmap" costs nothing, so a hostile size can be tested."""

    def __init__(self, width, height, size_for_scale=None):
        self.rect = pymupdf.Rect(0, 0, width, height)
        self.calls = []
        self._size_for_scale = size_for_scale or (lambda _scale: 64)

    def get_pixmap(self, matrix=None, clip=None, **_kwargs):
        scale = matrix.a if matrix is not None else 1.0
        self.calls.append({"scale": scale, "clip": clip})
        return _Pixmap(b"\xff\xd8" + b"x" * self._size_for_scale(scale))


class _Doc(object):
    page_count = 1

    def __init__(self, page):
        self._page = page

    def __getitem__(self, _pno):
        return self._page


def _rendered_pixels(page, call):
    """The pixels a get_pixmap call would have allocated, clip included."""
    rect = call["clip"] if call["clip"] is not None else page.rect
    return rect.width * call["scale"] * rect.height * call["scale"]


def test_pixmap_geometry_is_bounded_before_any_pixel_is_allocated():
    """A 20000 x 20000 pt page at 1.5x is a 900-megapixel, ~2.7 GB allocation.
    The bound has to land on the matrix BEFORE get_pixmap, not on the bytes after."""
    page = _Page(20000, 20000)
    data = extract.render_page_jpeg(_Doc(page), 0, scale=1.5, quality=80)

    assert page.calls, "nothing was rendered at all"
    for call in page.calls:
        assert _rendered_pixels(page, call) <= extract.MAX_RASTER_PIXELS, \
            "scale %.2f would allocate %d pixels" % (call["scale"],
                                                     _rendered_pixels(page, call))
    assert data.startswith(b"\xff\xd8")


def test_a_still_oversized_encoding_is_a_failure_not_a_return_value():
    """The finding's shape: every ladder step over the byte ceiling used to end
    with the oversized bytes returned to the caller. That is a clear failure now."""
    page = _Page(612, 792, size_for_scale=lambda _scale: 900 * 1024)

    with pytest.raises(extract.RasterTooLarge):
        extract.render_page_jpeg(_Doc(page), 0, scale=1.5, quality=80,
                                 max_bytes=900 * 1024)


def test_an_oversized_encoding_still_steps_down_until_it_fits():
    """The preserved behavior: byte pressure walks the scale down, smallest first
    attempt wins -- resolution is kept before gradients are."""
    page = _Page(612, 792, size_for_scale=lambda s: int(612 * 792 * s * s * 0.05))

    data = extract.render_page_jpeg(_Doc(page), 0, scale=1.5, quality=80,
                                    max_bytes=20 * 1024)

    assert [call["scale"] for call in page.calls] == [1.5, 1.25, 1.0, 0.75]
    assert len(data) <= 20 * 1024


def test_the_clip_is_part_of_the_bounded_geometry():
    """The pipeline crops furniture out of the picture; a clip must shrink the
    budget, and a hostile clip must be bounded by it."""
    page = _Page(20000, 20000)
    clip = pymupdf.Rect(0, 0, 1000, 1000)
    extract.render_page_jpeg(_Doc(page), 0, scale=1.5, quality=80, clip=clip)
    assert page.calls[0]["scale"] == 1.5, "a small clip on a huge page fits whole"
    assert page.calls[0]["clip"] == clip

    page2 = _Page(612, 792)
    big_clip = pymupdf.Rect(0, 0, 20000, 20000)
    extract.render_page_jpeg(_Doc(page2), 0, scale=1.5, quality=80, clip=big_clip)
    assert _rendered_pixels(page2, page2.calls[0]) <= extract.MAX_RASTER_PIXELS


def test_crop_jpeg_bounds_its_allocation_too():
    """Figure crops take the same bound: a hostile figure box is not a back door."""
    page = _Page(612, 792)
    data = extract.crop_jpeg(_Doc(page), 0, (0, 0, 50000, 40000), scale=2.0)

    assert _rendered_pixels(page, page.calls[0]) <= extract.MAX_RASTER_PIXELS
    assert data.startswith(b"\xff\xd8")


class _InkPixmap(object):
    width = height = 8
    n = 1
    samples = b"\xff" * 64


class _InkPage(_Page):
    """The blank-versus-artwork question needs samples, not bytes."""

    def __init__(self):
        super().__init__(612, 792)

    def get_pixmap(self, matrix=None, clip=None, **_kwargs):
        scale = matrix.a if matrix is not None else 1.0
        self.calls.append({"scale": scale, "clip": clip})
        return _InkPixmap()


def test_the_ink_check_bounds_its_allocation_before_any_pixel():
    """The ink check rendered its clip at a fixed 0.35 with no budget, so a
    hostile figure rectangle could ask for ~1.2 billion pixels before any cap
    applied (independent repro). The bound lands on the matrix first here too."""
    page = _InkPage()
    extract.region_has_ink(_Doc(page), 0, (0, 0, 100000, 100000))

    assert page.calls, "nothing was rendered at all"
    for call in page.calls:
        assert _rendered_pixels(page, call) <= extract.MAX_RASTER_PIXELS, \
            "scale %.4f would allocate %d pixels" % (
                call["scale"], _rendered_pixels(page, call))


def _text_doc(text, size=12, box=(0, 0, 1000, 1000), insert=True):
    doc = pymupdf.open()
    page = doc.new_page(width=box[2] - box[0], height=box[3] - box[1])
    if insert:
        page.insert_text((40, 60), text, fontsize=size)
    return doc


def test_sparse_but_meaningful_print_is_not_diluted_into_blank():
    """The confirmed ink-boundary block: 12pt 'Figure 7: Mars' alone inside a
    1000x1000pt territory reads 0.044% dark globally and was silently dropped
    exactly like a blank page. Sparse content must be read by whether marks
    exist at all, never by how much of the territory they cover."""
    doc = _text_doc("Figure 7: Mars")
    try:
        assert extract.region_has_ink(doc, 0, (0, 0, 1000, 1000)) is True
    finally:
        doc.close()


def test_a_truly_blank_territory_still_drops():
    """The control the dilution defect shares its shape with: blank paper is
    blank, and keeping it must not be the price of keeping sparse print."""
    doc = _text_doc("", insert=False)
    try:
        assert extract.region_has_ink(doc, 0, (0, 0, 1000, 1000)) is False
    finally:
        doc.close()


def _blobs_doc(boxes, size=(600, 400)):
    doc = pymupdf.open()
    page = doc.new_page(width=size[0], height=size[1])
    for box in boxes:
        page.draw_rect(box, fill=(0, 0, 0), color=None)
    return doc


def test_two_charts_have_one_empty_channel_between_them():
    """The 355 shape: two diagrams side by side split at the one column of
    paper that is empty from the territory's top to its bottom."""
    doc = _blobs_doc([(40, 40, 200, 360), (360, 40, 560, 360)])
    try:
        channels = extract.ink_channel(doc, 0, (20, 20, 580, 380))
        assert len(channels) == 1, channels
        assert 200 < channels[0] < 360
    finally:
        doc.close()


def test_one_chart_has_no_empty_channel_and_blank_paper_has_none():
    """The wheel control: one solid diagram never has a truly empty column, and
    blank paper has no channel because it has no marks to be between."""
    doc = _blobs_doc([(40, 40, 560, 360)])
    try:
        assert extract.ink_channel(doc, 0, (20, 20, 580, 380)) == []
    finally:
        doc.close()
    doc = _blobs_doc([])
    try:
        assert extract.ink_channel(doc, 0, (20, 20, 580, 380)) == []
    finally:
        doc.close()


def _rules_doc(rule_ys, vertical_ys=None):
    """A page carrying ruling and nothing else: horizontal rules at the given
    y positions, optionally one vertical divider."""
    doc = pymupdf.open()
    page = doc.new_page(width=504.0, height=720.0)
    for y in rule_ys:
        page.draw_line((48.0, y), (460.0, y), color=(0.3, 0.3, 0.32), width=0.7)
    if vertical_ys is not None:
        page.draw_line((264.0, vertical_ys[0]), (264.0, vertical_ys[1]),
                       color=(0.3, 0.3, 0.32), width=0.7)
    return doc


def test_rule_rows_reports_the_strokes_a_scan_carries():
    """A ruled grid's strokes are a point tall at most: the ink proof's 10x10
    cells cannot see them, so the row question is asked of the raster directly --
    which device rows stay dark across the territory's width."""
    doc = _rules_doc([134.0, 168.0, 250.0])
    try:
        rows = extract.rule_rows(doc, 0, (40.0, 90.0, 470.0, 300.0))
    finally:
        doc.close()

    assert len(rows) == 3, rows
    for got, want in zip(rows, (134.0, 168.0, 250.0)):
        assert abs(got - want) <= 2.0, (rows, want)


def test_rule_rows_sees_neither_blank_paper_nor_a_vertical_divider():
    """The controls: blank paper holds no rules, and a two-column page's
    vertical divider is not a row separator -- ruling that pairs nothing must
    not veto anything."""
    doc = _rules_doc([])
    try:
        assert extract.rule_rows(doc, 0, (40.0, 90.0, 470.0, 300.0)) == []
    finally:
        doc.close()
    doc = _rules_doc([], vertical_ys=(100.0, 500.0))
    try:
        assert extract.rule_rows(doc, 0, (40.0, 90.0, 470.0, 550.0)) == []
    finally:
        doc.close()


def test_the_rule_query_bounds_its_allocation_before_any_pixel():
    """The rule query renders at a stroke-resolving scale, far above the ink
    proof's 0.35: the pixel budget lands on the matrix first here as well."""
    page = _InkPage()
    extract.rule_rows(_Doc(page), 0, (0, 0, 100000, 100000))

    assert page.calls, "nothing was rendered at all"
    for call in page.calls:
        assert _rendered_pixels(page, call) <= extract.MAX_RASTER_PIXELS, \
            "scale %.4f would allocate %d pixels" % (
                call["scale"], _rendered_pixels(page, call))


def test_an_ordinary_page_is_rendered_exactly_as_before():
    """A real PDF at the pipeline's own settings: full scale, real JPEG, the size
    the vision model has always been sent."""
    doc = F.new_doc()
    F.prose_page(doc)
    try:
        width, height = doc[0].rect.width, doc[0].rect.height
        data = extract.render_page_jpeg(doc, 0, scale=1.5, quality=80,
                                        max_bytes=900 * 1024)
    finally:
        doc.close()

    assert data[:2] == b"\xff\xd8"
    assert len(data) <= 900 * 1024
    assert model._jpeg_dimensions(data) == (round(width * 1.5), round(height * 1.5))


def test_rotation_is_part_of_the_rendered_geometry():
    """The bound reads the page as it will be rendered; a rotated page's rendered
    size swaps its axes."""
    doc = F.new_doc()
    F.prose_page(doc)
    try:
        width, height = doc[0].rect.width, doc[0].rect.height
        doc[0].set_rotation(90)
        data = extract.render_page_jpeg(doc, 0, scale=1.5, quality=80)
    finally:
        doc.close()

    assert model._jpeg_dimensions(data) == (round(height * 1.5), round(width * 1.5))


def test_ink_extent_rejects_invalid_geometry_before_render_and_caps_large_clips():
    class EmptyPixmap:
        samples=b''
        n=1
        width=height=0
    class InkPage(_Page):
        def get_pixmap(self, **kwargs):
            super().get_pixmap(**kwargs)
            return EmptyPixmap()
    page=InkPage(20000,20000);probe=extract.ScanPixelProbe(_Doc(page),0)
    for rect in [(0,0,0,100),(0,0,100,float('inf'))]:
        with pytest.raises(ValueError):probe.ink_bounds(rect)
    assert not page.calls
    assert probe.ink_bounds((0,0,20000,20000)) is None
    assert page.calls and all(_rendered_pixels(page,c)<=extract.MAX_RASTER_PIXELS for c in page.calls)


def test_ink_extent_keeps_thin_pale_colored_source_annotations():
    """Low-resolution dark-only sampling loses pale relationship arcs outside
    the strong central wheel; the source extent must include those too."""
    doc=pymupdf.open();page=doc.new_page(width=500,height=720)
    page.draw_circle((350,220),70,color=(0,0,0),width=1)
    page.draw_circle((350,220),82,color=(0.70,0.85,0.85),width=0.5)
    try:box=extract.ScanPixelProbe(doc,0).ink_bounds((230,120,470,330))
    finally:doc.close()
    assert box and box[0]<=268 and box[2]>=432 and box[1]<=138 and box[3]>=302,box


def test_colored_ink_extent_excludes_masked_prose_in_every_channel():
    """A color-aware extent must not rediscover text through unmasked G/B."""
    doc=pymupdf.open();page=doc.new_page(width=500,height=720)
    page.insert_text((220,150),'Surrounding prose',fontsize=12)
    page.draw_circle((350,220),70,color=(0,0,0),width=1)
    masks=[line['bbox'] for block in page.get_text('dict')['blocks']
           for line in block.get('lines',[])]
    try:box=extract.ScanPixelProbe(doc,0,mask=masks).ink_bounds((200,120,470,330))
    finally:doc.close()
    assert box and box[0]>275 and box[1]>145,box
