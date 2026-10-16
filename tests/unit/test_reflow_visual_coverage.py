"""Source artwork may grow only with exact raster and transcript ownership proof."""
import copy
import hashlib
import json
import zipfile
from dataclasses import asdict

import pymupdf
import pytest
from cps.services.reflow import assemble, enriched_source, extract, skeleton, source_inventory, visual_objects

pytestmark = pytest.mark.unit

@pytest.fixture(params=[0, 90])
def coverage(tmp_path, request):
    path = tmp_path / 'rotated.pdf'
    with pymupdf.open() as doc:
        angle = request.param
        page = doc.new_page(width=300 if angle else 400, height=400 if angle else 300)
        page.draw_rect((80, 160, 190, 330) if angle else (70, 80, 240, 190), fill=(0, 0, 0))
        page.set_rotation(angle)
        doc.save(path)
    with pymupdf.open(path) as doc:
        def line(text, box):
            return extract.Line([extract.Span(text, 10, 'OCR', 0, box)], box)
        header = line('Readable header', (60, 40, 250, 60))
        legend = line('damaged == legend', (180, 180, 220, 190))
        caption = line('Independent caption', (90, 230, 230, 245))
        raw = extract.RawPage(0, 400, 300, blocks=[extract.Block(i, l.bbox, [l])
                 for i, l in enumerate([header, legend, caption])],
                 source_geometry=dict(space='reading', orientation=0, source_rotation=angle,
                                      page_rect=[0., 0., 400., 300.]))
        skel = skeleton.PageSkeleton(0, 400, 300, regions=[
            skeleton.Region('body', lines=[header], bbox=header.bbox),
            skeleton.Region('figure', bbox=(60, 60, 190, 185), needs_ink=True, reason='scan_figure_side'),
            skeleton.Region('figure', bbox=(175, 175, 225, 195), reason='ocr_uncertain_region'),
            skeleton.Region('artwork', lines=[legend], bbox=legend.bbox, reason='ocr_uncertain_region'),
            skeleton.Region('caption', lines=[caption], bbox=caption.bbox)])
        yield doc, raw, skel


def record(doc, raw):
    from cps.services.reflow import visual_coverage as coverage
    raster, binding = coverage.render_input(doc, raw)
    return dict(binding, detections=[dict(label='image', confidence=.95, bbox=[150, 105, 360, 315]),
                                    dict(label='figure_title', confidence=.9, bbox=[135, 345, 345, 368])])


@pytest.mark.parametrize('native', [False, True])
def test_rotated_coverage_keeps_source_occurrences_pixels_and_disclosure(coverage, tmp_path, native):
    from cps.services.reflow import build_epub, source, source_display
    doc, raw, skel = coverage
    original = copy.deepcopy(skel)
    catalog = source_inventory.catalog(raw)
    old = assemble.assemble([skel], skeleton.book_style([raw]), [raw])
    before = enriched_source.prepare_source_page(old, 0, {'layer':'ocr', **raw.source_geometry})
    observation = visual_objects.prepare(doc, raw, record(doc, raw))
    visual_objects.append_regions(raw, skel, observation)
    inventory = source_inventory.capture(catalog, skel)
    source_inventory.validate(inventory, raw)
    assert [r['sha256'] for r in inventory['lines']] == [r['sha256'] for r in catalog.page['lines']]
    assert [r['representation'] for r in inventory['ownership']] == ['text', 'protected_image', 'text']
    assert not inventory['unresolved']
    figures = [r for r in skel.regions if r.kind == 'figure']
    assert len(figures) == 1 and figures[0].bbox == (60, 60, 240, 210)
    assert skel.regions[0] is original.regions[0] or asdict(skel.regions[0]) == asdict(original.regions[0])
    assert next(r for r in skel.regions if r.kind == 'caption').lines[0] is raw.blocks[2].lines[0]
    artwork = next(r for r in skel.regions if r.kind == 'artwork')
    assert artwork.lines == [raw.blocks[1].lines[0]] and artwork.bbox == figures[0].bbox
    skel.regions.sort(key=skeleton._region_order)
    after = assemble.assemble([skel], skeleton.book_style([raw]), [raw])
    assert after.needs_source_evidence(0)
    current = enriched_source.prepare_source_page(after, 0, {'layer':'ocr', **raw.source_geometry})
    assert current.html.count('Independent caption') == 1
    assert 'OCR uncertain' in current.html
    with pytest.raises(ValueError, match='stale'): before.validate(after)
    display = source_display.SourceDisplay(doc, 0, {'layer':'ocr', **raw.source_geometry})
    full = display.pixmap(scale=1)
    crop = display.pixmap(figures[0].bbox, scale=1)
    for y in range(crop.height):
        for x in range(crop.width):
            assert crop.pixel(x,y) == full.pixel(x+60,y+60)
    # Publishing maps reading -> PDF once through the existing recovery transform.
    recovery = source.Recovery()
    recovery.provenance = {0: source.PageRecovery(pno=0, layer='ocr',
        orientation=0, source_rotation=doc[0].rotation, page_rect=tuple(doc[0].rect),
        derotation=tuple(doc[0].derotation_matrix))}
    target = tmp_path / 'coverage.epub'
    if native:
        from cps.services.reflow.native_ipc import NativeDocument
        with NativeDocument(doc.name, scratch_root=tmp_path/'native') as worker:
            build_epub.build(after, str(target), doc=worker, source_pages={0:current},
                             figure_transform=recovery.figure_rect)
    else:
        build_epub.build(after, str(target), doc=doc, source_pages={0:current},
                         figure_transform=recovery.figure_rect)
    assert not build_epub.validate(target)
    with zipfile.ZipFile(target) as z:
        chapter = b''.join(z.read(n) for n in z.namelist() if n.startswith('OEBPS/ch') and n.endswith('.xhtml')).decode()
        assert chapter.count('Independent caption') == 1 and 'Readable header' in chapter
        assert 'OCR uncertain' in chapter
        assert 'damaged == legend' not in chapter
        assert 'original-p0000.xhtml' in chapter
        assert any(n.startswith('OEBPS/images/fig_') for n in z.namelist())


@pytest.mark.parametrize('damage', ['raw', 'pdf', 'raster', 'model', 'frame', 'rotation', 'orientation', 'bounds', 'nan', 'caption'])
def test_coverage_rejects_stale_frame_raster_and_cut_caption(coverage, damage):
    doc, raw, skel = coverage
    proposal = record(doc, raw)
    if damage == 'raw': proposal['raw_sha256'] = '0'*64
    elif damage == 'pdf': proposal['pdf_sha256'] = '0'*64
    elif damage == 'raster': proposal['image_sha256'] = '0'*64
    elif damage == 'model': proposal['model_revision'] = 'wrong'
    elif damage == 'frame': raw.source_geometry['page_rect'][2] -= 1
    elif damage == 'rotation': doc[0].set_rotation(180)
    elif damage == 'orientation': raw.source_geometry['orientation'] = 90
    elif damage == 'bounds': proposal['detections'][0]['bbox'][2] = 1000
    elif damage == 'nan': proposal['detections'][0]['bbox'][0] = float('nan')
    else: proposal['detections'][1]['bbox'] = [135, 270, 345, 368]
    with pytest.raises(ValueError):
        obs = visual_objects.prepare(doc, raw, proposal)
        visual_objects.append_regions(raw, skel, obs)


@pytest.mark.parametrize('damage', ['prose', 'duplicate', 'conflict', 'partial', 'unissued', 'mutated'])
def test_coverage_rejects_ownership_loss_without_mutating_skeleton(coverage, damage):
    doc, raw, skel = coverage
    obs = visual_objects.prepare(doc, raw, record(doc, raw))
    if damage == 'prose': skel.regions[3].kind = 'body'
    elif damage == 'duplicate': skel.regions[3].lines *= 2
    elif damage == 'conflict': skel.regions.append(skeleton.Region('body', lines=skel.regions[3].lines, bbox=(280,250,320,270)))
    elif damage == 'partial': skel.regions.append(skeleton.Region('figure', bbox=(65, 50, 95, 68)))
    elif damage == 'unissued': obs = copy.deepcopy(obs)
    else: object.__setattr__(obs, 'raw_digest', 'wrong')
    before = asdict(skel)
    with pytest.raises(ValueError): visual_objects.append_regions(raw, skel, obs)
    assert asdict(skel) == before


@pytest.mark.parametrize('field,value', [
    ('image', 'unsupported'), ('continued_from', (0, 1)), ('initial_join', {'pending':True}),
    ('display_group', {'group':'x'}), ('level', 1), ('number', 3),
    ('visual_evidence', {'prior':'observation'}), ('reason', 'unverified_scan_layout'),
])
def test_coverage_rejects_semantics_it_cannot_preserve(coverage, field, value):
    doc, raw, skel = coverage
    obs = visual_objects.prepare(doc, raw, record(doc, raw))
    setattr(skel.regions[3], field, value)
    before = asdict(skel)
    with pytest.raises(ValueError): visual_objects.append_regions(raw, skel, obs)
    assert asdict(skel) == before


def test_coverage_rejects_untested_pdf_crop_offset(coverage):
    from cps.services.reflow import visual_coverage
    doc, raw, skel = coverage
    doc[0].set_cropbox(pymupdf.Rect(10, 10, 280, 280))
    raw.source_geometry['page_rect'] = list(doc[0].rect)
    raw.width, raw.height = doc[0].rect.width, doc[0].rect.height
    with pytest.raises(ValueError, match='cropped'): visual_coverage.render_input(doc, raw)



def test_empty_observation_and_caller_mutation_preserve_source(coverage):
    doc, raw, skel = coverage
    proposal = record(doc, raw)
    proposal['detections'] = []
    obs = visual_objects.prepare(doc, raw, proposal)
    proposal['detections'].append(dict(label='image', confidence=1, bbox=[0,0,600,450]))
    before = asdict(skel)
    visual_objects.append_regions(raw, skel, obs)
    assert asdict(skel) == before
