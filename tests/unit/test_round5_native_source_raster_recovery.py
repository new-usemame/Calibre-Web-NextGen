"""SOURCE-known native raster carriers survive absent assets without BODY loss."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET
import zipfile
import pytest
import pymupdf
from cps.services.reflow import assemble, build_epub, vision_pipeline

_p = Path(__file__).with_name('test_round5_figure_recovery_calibrated.py')
if not _p.exists():
    _p = Path(vision_pipeline.__file__).parents[3] / 'tests/unit/test_round5_figure_recovery.py'
_s = importlib.util.spec_from_file_location('round5_native_carrier_ink', _p)
r = importlib.util.module_from_spec(_s); _s.loader.exec_module(r)
h = r.h
pytestmark = pytest.mark.unit


@pytest.mark.parametrize('prose_pixels', [False, True])
def test_source_known_raster_presentation_recovers_asset_and_preserves_source_atom(tmp_path, monkeypatch, prose_pixels):
    doc = pymupdf.open(); pages = {}; figures = []
    texts = ['A clear native paragraph precedes its existing source image.', 'A later native paragraph and valid image both survive.']
    for n, text in enumerate(texts):
        page = doc.new_page(width=h.W, height=h.H); page.insert_text((24, 50), text, fontsize=9)
        page.draw_rect([40, 100, 170, 180], color=(0, 0, 1), fill=(.85, .85, 1))
        if prose_pixels and not n:
            page.insert_text((48, 130), 'Scarlet source text', fontsize=8, color=(1, 0, 0))
            page.insert_text((48, 145), 'remains in the body image.', fontsize=8, color=(1, 0, 0))
        elif n:
            page.draw_line((40, 100), (170, 180), color=(0, 0, 0), width=2)
        prose = assemble.Element('p', runs=[['t', text]], pno=n, bbox=(24, 35, 290, 55))
        fig = assemble.Element('fig', pno=n, bbox=(40, 100, 170, 180)); pages[n] = [prose, fig]
        figures.append(dict(pno=n, bbox=(40, 100, 170, 180), found=('native_spacing_uncertain' if prose_pixels else 'ocr_uncertain_region') if not n else 'native'))
    book = assemble.Book(elements=[e for pg in pages.values() for e in pg], pages=pages, figures=figures)
    original = build_epub._figure_images
    def one_unavailable(*args, **kwargs):
        missing, blank = original(*args, **kwargs)
        package = args[3]; wanted = 'images/fig_p0000_0.jpg'
        package.images.pop(package.aliases.get(wanted, wanted), None)
        package.images.pop(wanted, None)
        return ['images/fig_p0000_0.jpg'] + missing, blank
    monkeypatch.setattr(build_epub, '_figure_images', one_unavailable)
    reader = SimpleNamespace(cache=tmp_path/'cache'/'readings', read=lambda image: pytest.fail('clean native input never calls model'))
    path = tmp_path/'native-raster.epub'
    try: result = vision_pipeline.convert(doc, str(path), reader, native_prepared=SimpleNamespace(book=book))
    finally: doc.close()
    assert build_epub.validate(str(path)) == []
    with zipfile.ZipFile(path) as z:
        docs = {name: ET.fromstring(z.read(name)) for name in z.namelist() if name.endswith('.xhtml') and 'nav.xhtml' not in name and 'vision-about' not in name}
        for case in result['degraded_figures']:
            assert 'OEBPS/' + case['source_image'] in z.namelist(), 'report source_image resolves to actual member after aliases'
    chapters = {name: root for name, root in docs.items() if name.startswith('OEBPS/ch')}
    live = [h.plain(n) for n in h.body_nodes(chapters) if n.tag.endswith('}p')
            and n.get('class') not in ('caption', 'source-evidence-notice') and h.plain(n)]
    assert live == texts, 'unaffected native live text survives exactly once'
    assert result['degraded_figure_count'] == 1 and result['degraded_figures'][0]['page'] == 0
    assert len(r.context_links(path, result, docs)) == 1
    if prose_pixels:
        body_images = [img for n in h.body_nodes(chapters) for img in n.findall('.//x:img', h.NS)
                       if any(c[0] > 130 and c[1] < 90 and c[2] < 90 for c in r.pixels(path, img.get('src')).getdata())]
        assert len(body_images) == 1, 'existing SOURCE-prose raster stays visible once, never moved solely into optional evidence'
        box = result['images'][body_images[0].get('src')]['bbox']
        assert box == pytest.approx([40, 100, 170, 180]), 'bounded original SOURCE region replaces its unavailable asset'
    assert any(record['page'] == 1 and record['kind'] == 'native' for record in result['images'].values())
