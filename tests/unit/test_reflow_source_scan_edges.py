"""Source frame evidence removes binding artifacts without erasing reading ink."""
import io
import pytest
from PIL import Image, ImageDraw
from cps.services.reflow import crop_edges

pytestmark = pytest.mark.unit


def png(image):
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


def frame(kind='gutter'):
    image = Image.new('L', (1200, 800), 255)
    draw = ImageDraw.Draw(image)
    # Printed paragraphs/art have edges far from the physical scan ends.
    for y in range(90, 600, 24):
        draw.rectangle((80, y, 535, y+4), fill=0)
        draw.rectangle((690, y, 1100, y+4), fill=0)
    if kind == 'gutter':
        draw.polygon([(575, 0), (630, 0), (609, 70), (632, 310),
                      (620, 800), (588, 800), (584, 200)], fill=0)
    elif kind == 'printed_panel':
        draw.rectangle((575, 100, 635, 700), fill=0)
    elif kind == 'printed_rule':
        draw.rectangle((600, 0, 600, 799), fill=0)
    elif kind == 'table':
        draw.rectangle((575, 100, 635, 700), outline=0, width=6)
    elif kind == 'non_margin':
        draw.rectangle((250, 0, 300, 799), fill=0)
    elif kind == 'page_fill':
        draw.rectangle((575, 0, 850, 799), fill=0)
    return image


def test_standalone_binding_wedge_is_not_a_figure():
    source = frame()
    edges = crop_edges.source_scan_edges(png(source), (0, 0, 1200, 800))
    assert edges, 'Full-page gutter must be recognized before cropping'
    box = (560, 120, 650, 450)
    original = png(source.crop(box))
    data, audit, drop = crop_edges.trim_source_edges(original, box, edges)
    assert drop and data == original
    assert any(row['action'] == 'drop_scan_carrier' for row in audit)


def test_thin_crop_edge_can_be_removed_by_full_frame_proof():
    source = frame()
    box = (70, 120, 591, 450)
    edges = crop_edges.source_scan_edges(png(source), (0, 0, 1200, 800))
    original = source.crop(box)
    data, audit, drop = crop_edges.trim_source_edges(png(original), box, edges,
                                                    [(80, 138, 535, 150)])
    result = Image.open(io.BytesIO(data)).convert('L')
    assert not drop and result.width < original.width
    assert result.tobytes() == original.crop((0, 0, result.width, original.height)).tobytes()
    assert any(row['action'] == 'trim' for row in audit)


def test_source_glyph_overlap_refuses_trim():
    source = frame()
    box = (70, 120, 591, 450)
    edges = crop_edges.source_scan_edges(png(source), (0, 0, 1200, 800))
    original = png(source.crop(box))
    data, audit, drop = crop_edges.trim_source_edges(original, box, edges,
                                                    [(80, 138, 589, 150)])
    assert not drop
    result = Image.open(io.BytesIO(data)).convert('L')
    assert result.crop((10,18,519,30)).tobytes() == Image.open(io.BytesIO(original)).convert('L').crop((10,18,519,30)).tobytes()
    assert any(row['action'] == 'refused_glyph_overlap' for row in audit)


def test_black_art_beside_binding_is_retained():
    source = frame()
    ImageDraw.Draw(source).rectangle((645, 250, 650, 280), fill=0)
    edges = crop_edges.source_scan_edges(png(source), (0, 0, 1200, 800))
    box = (560, 120, 655, 450)
    data, audit, drop = crop_edges.trim_source_edges(png(source.crop(box)), box, edges)
    assert not drop
    assert Image.open(io.BytesIO(data)).convert('L').getpixel((87, 140)) == 0


@pytest.mark.parametrize('kind', ['printed_panel', 'printed_rule', 'table', 'non_margin', 'page_fill'])
def test_source_content_is_not_scan_edge(kind):
    assert crop_edges.source_scan_edges(png(frame(kind)), (0, 0, 1200, 800)) == []


def test_interior_gutter_clearing_preserves_both_printed_halves():
    source = frame()
    box = (70, 120, 1120, 450)
    original = source.crop(box)
    edges = crop_edges.source_scan_edges(png(source), (0, 0, 1200, 800))
    protected = [(80, 138, 535, 150), (690, 138, 1100, 150)]
    data, audit, drop = crop_edges.trim_source_edges(png(original), box, edges, protected)
    result = Image.open(io.BytesIO(data)).convert('L')
    assert not drop and result.size == original.size
    assert result.crop((0, 0, 505, original.height)).tobytes() == original.crop((0, 0, 505, original.height)).tobytes()
    assert result.crop((570, 0, original.width, original.height)).tobytes() == original.crop((570, 0, original.width, original.height)).tobytes()
    assert result.getpixel((530, 130)) == 255
    assert any(row['action'] == 'clear_scan_carrier' for row in audit)


def test_interior_protected_glyph_refuses_silhouette_clearing():
    source = frame()
    box = (70, 120, 1120, 450)
    original = png(source.crop(box))
    edges = crop_edges.source_scan_edges(png(source), (0, 0, 1200, 800))
    data, audit, drop = crop_edges.trim_source_edges(original, box, edges,
                                                    [(590, 250, 620, 275)])
    assert not drop
    result = Image.open(io.BytesIO(data)).convert('L')
    # Protected source glyph samples remain identical, even if safe rows clean.
    assert result.crop((520,130,550,155)).tobytes() == Image.open(io.BytesIO(original)).convert('L').crop((520,130,550,155)).tobytes()
    assert any(row['action'] == 'refused_glyph_overlap' for row in audit)
