"""SOURCE-free native missing-asset and structurally proven degraded TABLE controls."""
import importlib.util
from pathlib import Path
from cps.services.reflow import vision_pipeline
_p = Path(__file__).with_name('test_round5_figure_recovery_calibrated.py')
if not _p.exists():
    _p = Path(vision_pipeline.__file__).parents[3] / 'tests/unit/test_round5_figure_recovery.py'
_s = importlib.util.spec_from_file_location('round5_recovery_controls', _p)
r = importlib.util.module_from_spec(_s)
_s.loader.exec_module(r)
h, art, paragraph, context_links = r.h, r.art, r.paragraph, r.context_links
pytest, pymupdf, assemble, build_epub = r.pytest, r.pymupdf, r.assemble, r.build_epub
SimpleNamespace, ET, zipfile = r.SimpleNamespace, r.ET, r.zipfile
pytestmark = pytest.mark.unit

def test_degraded_source_ruled_table_floats_after_complete_live_sentence(tmp_path, monkeypatch):
    p = h.InkPage()
    upper = 'The careful observer follows'
    beside = 'a narrow path through'
    first = upper + ' ' + beside
    last = 'the entire displayed material before reaching its natural endpoint.'
    paragraph(p, [upper, beside], [(24, 60), (195, 151)], ident='first', next=True)
    rect = [35, 100, 205, 200]; art(p, rect, 'table', [35, 100, 225, 200], role='table')
    p.page.draw_line((35, 140), (205, 140), color=(0, 0, 0), width=1)
    # The original table touches independently corroborated BODY ink, so clean
    # rectangle publication must degrade; its physical ruled structure remains.
    p.text('A synthetic ruled table.', 'caption', 'caption', y=215, size=7)
    p.text(last, 'last', y=242, size=7, previous=True)
    path, result, docs = h.convert_ink(tmp_path, monkeypatch, [p])
    body = h.body_texts(docs)
    assert first + ' ' + last in body, 'degraded original-context table must not split a proven continuous sentence'
    assert len(context_links(path, result, docs)) == 1
    assert result['degraded_figure_count'] == 1
    nodes = h.body_nodes(docs)
    assert not any(n.tag.endswith('}p') and n.findall('.//x:a[@class="source-disclosure-ref"]', h.NS) for n in nodes), 'table source control must float outside BODY sentence'


@pytest.mark.parametrize('missing_identity', ['known', 'unknown'])
def test_native_asset_failure_recovers_only_source_known_objects(tmp_path, monkeypatch, missing_identity):
    doc = pymupdf.open(); pages = {}; figures = []
    texts = ['A native source paragraph remains live before its uncertain asset.',
             'A later native paragraph keeps its complete valid illustration.']
    for n, text in enumerate(texts):
        page = doc.new_page(width=h.W, height=h.H)
        page.insert_text((24, 50), text, fontsize=9)
        assert ' '.join(w[4]for w in page.get_text('words'))==text, 'The entire authored sentence must be inside the printed page'
        page.draw_rect([40, 100, 120, 180], color=(0, 0, 1), fill=(0, 0, 1))
        if n: page.draw_line((40, 100), (120, 180), color=(0, 0, 0), width=2)
        prose = assemble.Element('p', runs=[['t', text]], pno=n, bbox=(24, 35, 290, 55))
        fig = assemble.Element('fig', pno=n, bbox=(40, 100, 120, 180))
        pages[n] = [prose, fig]
        figures.append(dict(pno=n, bbox=(40, 100, 120, 180), found='native'))
    book = assemble.Book(elements=[e for page in pages.values() for e in page], pages=pages, figures=figures)
    original = build_epub._figure_images
    def one_unavailable(*args, **kwargs):
        missing, blank = original(*args, **kwargs)
        package = args[3]; src = 'images/fig_p0000_0.jpg' if missing_identity == 'known' else 'images/fig_p0999_0.jpg'
        # A source-known asset failure is independent of whole source-page
        # evidence and must not erase the other source-bound object.
        package.images.pop(src, None)
        return [src] + missing, blank
    monkeypatch.setattr(build_epub, '_figure_images', one_unavailable)
    reader = SimpleNamespace(cache=tmp_path/'cache'/'readings', read=lambda image: pytest.fail('clean native recovery never calls the model'))
    path = tmp_path/'native.epub'
    try:
        if missing_identity == 'unknown':
            with pytest.raises(ValueError):
                vision_pipeline.convert(doc, str(path), reader, native_prepared=SimpleNamespace(book=book))
            assert not path.exists(), 'unknown source identity is a hard failure, not invented context'
            return
        result = vision_pipeline.convert(doc, str(path), reader, native_prepared=SimpleNamespace(book=book))
    finally:
        doc.close()
    assert build_epub.validate(str(path)) == []
    with zipfile.ZipFile(path) as z:
        docs = {name: ET.fromstring(z.read(name)) for name in z.namelist() if name.endswith('.xhtml') and 'nav.xhtml' not in name and 'vision-about' not in name}
    assert h.body_texts({name: root for name, root in docs.items() if name.startswith('OEBPS/ch')}) == texts
    assert result['degraded_figure_count'] == 1
    assert len(context_links(path, result, docs)) == 1
    valid = [(src, record) for src, record in result['images'].items()
             if record['page'] == 1 and record['kind'] == 'native'
             and record['bbox'] == (40, 100, 120, 180)]
    assert len(valid) == 1, 'the unrelated SOURCE-bound native object remains published'
    src, _ = valid[0]
    assert any(img.get('src') == src for root in docs.values() for img in root.findall('.//x:img', h.NS))
    r.blue_corners(r.pixels(path, src))
    with zipfile.ZipFile(path) as archive:
        for case in result['degraded_figures']:
            assert 'OEBPS/' + case['source_image'] in archive.namelist(), 'report uses actual packaged image identity after aliases' 
