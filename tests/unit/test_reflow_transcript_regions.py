"""Unmatched recognition ink may remain an image, never become source text."""
import copy
from dataclasses import asdict
from types import SimpleNamespace as NS
import pytest
from cps.services.reflow import extract, skeleton, transcript, source

pytestmark = pytest.mark.unit


@pytest.fixture
def region_source(tmp_path, monkeypatch):
    import pymupdf
    from cps.services.reflow import transcript_regions as regions
    doc = pymupdf.open(); page = doc.new_page(width=400, height=600)
    page.insert_text((20, 40), 'Original native words.')
    page.draw_rect((100, 100, 200, 200), fill=(0, 0, 0))
    path = tmp_path/'source.pdf'; doc.save(path); doc.close(); doc = pymupdf.open(path)
    raw = extract.read_page(doc, 0, keep_char_boxes=True)
    raw.text_layer_invisible = True
    raw.images = [extract.Image((0, 0, 400, 600), 1)]
    words = [NS(text=s.text.strip(), pdf_bbox=s.bbox, confidence=99)
             for b in raw.text_blocks for l in b.lines for s in l.spans]
    words.append(NS(text='NEVER ADOPT THIS OCR', pdf_bbox=(120, 120, 150, 150), confidence=40))
    raw, evidence = transcript.corroborate(raw, NS(words=words))
    evidence.update(recognition_orientation=0, recognition_sha256='a'*64,
                    source_pdf_sha256=extract.document_fingerprint(doc), page=0)
    prov = asdict(source.PageRecovery(pno=0, layer='native', reason='verify_scan_transcript'))
    prov.update(verification=evidence, flags=['native_transcript_corroborated'])
    def measured(candidate, *args, **kwargs):
        # Exercise a real potential speculative mutation, even on refusal.
        candidate.blocks[0].lines[0].transcription_uncertain = True
        return skeleton.PageSkeleton(0, 400, 600, regions=[
            skeleton.Region('body', lines=candidate.blocks[0].lines, bbox=candidate.blocks[0].bbox),
            skeleton.Region('figure', bbox=(100, 100, 200, 200), reason='scan_figure_band', needs_ink=True)])
    monkeypatch.setattr(skeleton, 'page_skeleton', measured)
    yield regions, raw, prov, doc
    doc.close()


def test_complete_bounded_ink_keeps_raw_words_and_original_uncertainty(region_source):
    regions, raw, prov, doc = region_source
    before = copy.deepcopy(raw)
    result = regions.candidate(raw, prov, doc, skeleton.BookStyle(body_size=11), pixel_probe=None, layer_trusted=True)
    assert result is not None
    measured, proof = result
    assert raw == before and raw.transcript_unverified
    assert prov['verification']['unmatched_words'] == 1
    assert 'NEVER ADOPT' not in repr(prov)
    assert proof['unmatched_pdf_boxes'] == [[120, 120, 150, 150]]
    assert any(r.kind == 'body' for r in measured.regions)


@pytest.mark.parametrize('obstruction', [None, 'body', 'caption', 'third_figure'])
def test_overlapping_uncertain_heading_has_one_complete_pixel_owner(region_source, monkeypatch, obstruction):
    regions, raw, prov, doc = region_source
    def measured(candidate, *args, **kwargs):
        title = skeleton.Region('figure', bbox=(110, 90, 190, 110), reason='native_spacing_uncertain')
        chart = skeleton.Region('figure', bbox=(100, 100, 200, 200), reason='scan_figure_band')
        entries = [title, chart]
        if obstruction == 'body':
            entries.append(skeleton.Region('body', bbox=(100, 90, 110, 95), lines=raw.blocks[0].lines))
        elif obstruction == 'caption':
            title.caption_lines = raw.blocks[0].lines
        elif obstruction == 'third_figure':
            entries.append(skeleton.Region('figure', bbox=(100, 85, 120, 95)))
        return skeleton.PageSkeleton(0, 400, 600, regions=entries)
    monkeypatch.setattr(skeleton, 'page_skeleton', measured)
    result = regions.candidate(raw, prov, doc, skeleton.BookStyle(body_size=11), pixel_probe=None, layer_trusted=True)
    if obstruction:
        assert result is None
    else:
        measured, proof = result
        assert len([r for r in measured.regions if r.kind == 'figure']) == 1
        assert measured.regions[0].bbox == (100, 90, 200, 200)
        assert proof['owners'][0]['bbox'] == [100, 90, 200, 200]
        assert raw.transcript_unverified


@pytest.mark.parametrize('change', ['partial', 'overlap', 'count', 'missing', 'nan', 'boolean',
                                  'wrong_page', 'wrong_pdf', 'stale', 'rotation', 'frame', 'null_verification'])
def test_incomplete_or_wrong_frame_keeps_full_page_fallback(region_source, change):
    regions, raw, prov, doc = region_source
    v = prov['verification']
    if change == 'partial': v['unmatched_pdf_boxes'] = [[190, 120, 210, 150]]
    elif change == 'overlap': v['unmatched_pdf_boxes'] = [list(raw.blocks[0].lines[0].bbox)]
    elif change == 'count': v['unmatched_words'] = 2
    elif change == 'missing': v.pop('unmatched_pdf_boxes')
    elif change == 'nan': v['unmatched_pdf_boxes'][0][0] = float('nan')
    elif change == 'boolean': v['unmatched_pdf_boxes'][0][0] = True
    elif change == 'wrong_page': v['page'] = 1
    elif change == 'wrong_pdf': v['source_pdf_sha256'] = 'b'*64
    elif change == 'stale': v['version'] = 'old'
    elif change == 'rotation': doc[0].set_rotation(90)
    elif change == 'frame': raw.source_geometry = {'space': 'reading'}
    elif change == 'null_verification': prov['verification'] = None
    before = copy.deepcopy(raw)
    assert regions.candidate(raw, prov, doc, skeleton.BookStyle(body_size=11), pixel_probe=None, layer_trusted=True) is None
    assert raw == before


def test_required_source_crop_survives_optional_blank_classifier(region_source, tmp_path, monkeypatch):
    import zipfile
    from tests.unit.reflow_image_assertions import figure_name
    from cps.services.reflow import assemble, build_epub
    regions, raw, prov, doc = region_source
    measured, proof = regions.candidate(raw, prov, doc, skeleton.BookStyle(body_size=11), pixel_probe=None, layer_trusted=True)
    book = assemble.assemble([measured], skeleton.BookStyle(body_size=11), [raw])
    book.source_fingerprint = extract.document_fingerprint(doc)
    regions.bind(book, 0, proof)
    monkeypatch.setattr(extract, 'region_has_ink', lambda *a, **k: False)
    path = tmp_path/'mandatory.epub'
    build_epub.build(book, str(path), doc=doc)
    assert build_epub.validate(path)==[]
    with zipfile.ZipFile(path) as archive:
        assert archive.getinfo(figure_name(archive, 'fig_p0000_0')).file_size>0


def _book(region_source):
    from cps.services.reflow import assemble
    regions, raw, prov, doc = region_source
    measured, proof = regions.candidate(raw, prov, doc, skeleton.BookStyle(body_size=11), pixel_probe=None, layer_trusted=True)
    book = assemble.assemble([measured], skeleton.BookStyle(body_size=11), [raw])
    book.source_fingerprint = extract.document_fingerprint(doc)
    regions.bind(book, 0, proof)
    prov['verification']['region_disposition'] = proof
    return book


def test_source_crop_survives_real_epub_and_native_codec(region_source, tmp_path):
    import io, zipfile
    from tests.unit.reflow_image_assertions import figure_name
    from PIL import Image
    from cps.services.reflow import build_epub, enriched_source, native_codec
    regions, raw, prov, doc = region_source
    book = native_codec.loads(native_codec.dumps(_book(region_source)))
    canonical = enriched_source.prepare_source_page(book, 0, prov)
    assert 'NEVER ADOPT' not in canonical.html
    path = tmp_path/'preserved.epub'
    build_epub.build(book, str(path), doc=doc, source_pages={0:canonical})
    assert build_epub.validate(path) == []
    with zipfile.ZipFile(path) as z:
        name = figure_name(z, 'fig_p0000_0').removeprefix('OEBPS/')
        body = ''.join(z.read(n).decode() for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml'))
        assert name in body
        image = Image.open(io.BytesIO(z.read('OEBPS/'+name))).convert('L')
        assert image.getpixel((image.width//2, image.height//2)) < 20
    assert raw.transcript_unverified and prov['verification']['unmatched_words'] == 1


@pytest.mark.parametrize('damage', ['remove_owner', 'shrink_owner', 'crop_failure', 'change_transform', 'remove_reference', 'dirty_pixels'])
def test_no_plan_publication_cannot_lose_or_shrink_required_source(region_source, tmp_path, monkeypatch, damage):
    from cps.services.reflow import build_epub
    from cps.services.reflow.structural_ops import ContractError
    regions, raw, prov, doc = region_source
    book = _book(region_source)
    options = {}
    if damage == 'remove_owner': book.figures.clear()
    elif damage == 'shrink_owner': book.figures[0]['bbox'] = [100, 100, 110, 110]
    elif damage == 'crop_failure':
        crop = extract.crop_jpeg
        def failed(doc, pno, bbox, *args, **kwargs):
            if tuple(bbox) == (100, 100, 200, 200): raise ValueError('source crop failed')
            return crop(doc, pno, bbox, *args, **kwargs)
        monkeypatch.setattr(extract, 'crop_jpeg', failed)
    elif damage == 'change_transform': options['figure_transform'] = lambda pno, box: (100, 100, 110, 110)
    elif damage == 'dirty_pixels': doc[0].draw_rect((100, 100, 200, 200), fill=(1, 1, 1), color=(1, 1, 1))
    elif damage == 'remove_reference':
        original = build_epub._chapters
        def missing(*args, **kwargs):
            chapters = original(*args, **kwargs)
            for chapter in chapters: chapter.blocks = [b.replace('images/fig_p0000_0.jpg', 'images/other.jpg') for b in chapter.blocks]
            return chapters
        monkeypatch.setattr(build_epub, '_chapters', missing)
    path = tmp_path/(damage+'.epub')
    with pytest.raises(ContractError, match='source region'):
        build_epub.build(book, str(path), doc=doc, **options)
    assert not path.exists()


def test_source_factory_requires_complete_witness_disposition(region_source):
    from cps.services.reflow import enriched_source
    from cps.services.reflow.structural_ops import ContractError
    book = _book(region_source)
    prov = region_source[2]
    prov['verification']['unmatched_pdf_boxes'] = []
    with pytest.raises(ContractError, match='source region'):
        enriched_source.prepare_source_page(book, 0, prov)


def test_unchanged_clone_occurrences_rebind_without_matching_equal_words(region_source, monkeypatch):
    regions, raw, prov, doc = region_source
    raw.blocks.append(copy.deepcopy(raw.blocks[0]))
    assert raw.blocks[0].lines[0] == raw.blocks[1].lines[0]
    assert raw.blocks[0].lines[0] is not raw.blocks[1].lines[0]
    def measured(clone, *args, **kwargs):
        return skeleton.PageSkeleton(0, 400, 600, regions=[
            skeleton.Region('body', lines=[clone.blocks[0].lines[0]]),
            skeleton.Region('furniture', lines=[clone.blocks[1].lines[0]]),
            skeleton.Region('figure', bbox=(100,100,200,200), reason='scan_figure_band')])
    monkeypatch.setattr(skeleton, 'page_skeleton', measured)
    skel, _ = regions.candidate(raw, prov, doc, skeleton.BookStyle(body_size=11), pixel_probe=None, layer_trusted=True)
    assert skel.regions[0].lines[0] is raw.blocks[0].lines[0]
    assert skel.regions[1].lines[0] is raw.blocks[1].lines[0]
