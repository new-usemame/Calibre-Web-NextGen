"""Model-discovered artwork is additive, source-bound, and text-disjoint."""
import copy
import hashlib
import json
import zipfile

import pymupdf
import pytest

from cps.services.reflow import assemble, build_epub, enriched_source, extract, pipeline, skeleton


@pytest.fixture
def example(tmp_path):
    path = tmp_path / 'source.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=500)
        page.insert_text((40, 80), 'These source words remain in their paragraph.', fontsize=11)
        page.insert_text((40, 240), 'Later source words stay below the printed mark.', fontsize=11)
        page.draw_rect((300, 120, 340, 160), fill=(0, 0, 0))
        doc.save(path)
    with pymupdf.open(path) as doc:
        yield doc, extract.read_pages(doc)[0]


def proposal(doc, raw):
    from cps.services.reflow import visual_objects as visual
    from cps.services.reflow.model import _jpeg_dimensions
    raster = visual.render_input(doc, raw.pno)
    return dict(version=visual.VERSION, model_id=visual.MODEL_ID,
                model_revision=visual.MODEL_REVISION, raster_version=visual.RASTER_VERSION,
                pdf_sha256=extract.document_fingerprint(doc), page=raw.pno,
                image_sha256=hashlib.sha256(raster).hexdigest(),
                image_size=list(_jpeg_dimensions(raster)), page_rect=list(doc[raw.pno].rect),
                rotation=0, detections=[dict(label='image', confidence=.9,
                                            bbox=[450, 180, 510, 240])])


def book_for(raw, observation=None):
    style = skeleton.book_style([raw])
    skel = skeleton.page_skeleton(raw, style, visual_objects=observation)
    return assemble.assemble([skel], style, [raw])


def test_source_crop_preserves_words_and_seals_evidence(example, tmp_path):
    from cps.services.reflow import visual_objects as visual
    doc, raw = example
    before = book_for(raw)
    old = enriched_source.prepare_source_page(before, 0, {'layer': 'native'})
    record = proposal(doc, raw)
    observation = visual.prepare(doc, raw, record)
    record['detections'][0]['bbox'][0] = 0  # caller mutation cannot change the issued value
    after = book_for(raw, observation)
    assert len(after.figures) == len(before.figures) + 1
    assert [(e.kind, e.runs) for e in after.pages[0] if e.kind != 'fig'] == [
        (e.kind, e.runs) for e in before.pages[0] if e.kind != 'fig']
    assert [e.kind for e in after.pages[0]].index('fig') == 1
    figure = next(f for f in after.figures if f['found'] == 'local_visual_object')
    assert figure['bbox'] == pytest.approx([300, 120, 340, 160])
    assert figure['visual_evidence']['model_revision'] == visual.MODEL_REVISION
    current = enriched_source.prepare_source_page(after, 0, {'layer': 'native'})
    assert current.identity != old.identity
    with pytest.raises(ValueError, match='stale'):
        old.validate(after)
    target = tmp_path / 'with-object.epub'
    build_epub.build(after, str(target), doc=doc, source_pages={0: current})
    assert not build_epub.validate(target)
    with zipfile.ZipFile(target) as package:
        assert any(n.startswith('OEBPS/images/fig_p0000_') for n in package.namelist())
    figure['visual_evidence']['model_revision'] = 'changed'
    with pytest.raises(ValueError, match='stale'):
        current.validate(after)


@pytest.mark.parametrize('field,value', [
    ('pdf_sha256', '0' * 64), ('page', 1), ('image_sha256', '0' * 64),
    ('image_size', [600, 749]), ('page_rect', [0, 0, 399, 500]), ('rotation', 90),
    ('model_revision', 'unreviewed'), ('model_id', 'another-model'),
    ('raster_version', 'unknown'),
])
def test_mismatched_evidence_is_not_admitted(example, field, value):
    from cps.services.reflow import visual_objects as visual
    doc, raw = example
    record = proposal(doc, raw)
    record[field] = value
    with pytest.raises(ValueError):
        visual.prepare(doc, raw, record)


@pytest.mark.parametrize('box', [[-1, 180, 510, 240], [450, 180, 650, 240],
                                 [450, 240, 510, 180], [450, 180, float('nan'), 240]])
def test_invalid_coordinates_fail_closed(example, box):
    from cps.services.reflow import visual_objects as visual
    doc, raw = example
    record = proposal(doc, raw)
    record['detections'][0]['bbox'] = box
    with pytest.raises(ValueError):
        visual.prepare(doc, raw, record)


@pytest.mark.parametrize('change', ['text', 'blank', 'label', 'low_confidence'])
def test_unsupported_or_text_overlapping_objects_do_not_change_source(example, change):
    from cps.services.reflow import visual_objects as visual
    doc, raw = example
    record = proposal(doc, raw)
    detection = record['detections'][0]
    if change == 'text':
        detection['bbox'] = [v * 1.5 for v in raw.text_blocks[0].lines[0].bbox]
    elif change == 'blank':
        detection['bbox'] = [450, 450, 510, 510]
    elif change == 'label':
        detection['label'] = 'header'
    else:
        detection['confidence'] = .4
    after = book_for(raw, visual.prepare(doc, raw, record))
    before = book_for(raw)
    assert after.figures == before.figures
    assert after.pages == before.pages


def test_duplicate_region_and_stale_raw_source(example):
    from cps.services.reflow import visual_objects as visual
    doc, raw = example
    record = proposal(doc, raw)
    record['detections'].append(copy.deepcopy(record['detections'][0]))
    observation = visual.prepare(doc, raw, record)
    assert len(book_for(raw, observation).figures) == 1
    changed = copy.deepcopy(raw)
    changed.blocks[0].lines[0].spans[0].text = 'Changed source'
    with pytest.raises(ValueError, match='source'):
        book_for(changed, observation)


def test_rotated_frame_is_explicitly_unsupported(example):
    from cps.services.reflow import visual_objects as visual
    doc, raw = example
    record = proposal(doc, raw)
    doc[0].set_rotation(90)
    with pytest.raises(ValueError, match='rotation|frame'):
        visual.prepare(doc, raw, record)


def test_pipeline_threads_bound_visual_input_before_source_sealing(example):
    doc, raw = example
    result = pipeline.run(doc, recovery_opts={'mode': 'off'},
                          visual_results={0: proposal(doc, raw)})
    assert len(result.book.figures) == 1
    assert result.book.figures[0]['found'] == 'local_visual_object'
    assert result.book.figures[0]['visual_evidence']['pdf_sha256'] == result.fingerprint


def test_unissued_copy_and_subpixel_source_change_are_rejected(example):
    from cps.services.reflow import visual_objects as visual
    doc, raw = example
    observation = visual.prepare(doc, raw, proposal(doc, raw))
    with pytest.raises(ValueError, match='unissued'):
        book_for(raw, copy.copy(observation))
    changed = copy.deepcopy(raw)
    box = list(changed.blocks[0].lines[0].bbox)
    box[0] += .0001
    changed.blocks[0].lines[0].bbox = tuple(box)
    with pytest.raises(ValueError, match='stale'):
        book_for(changed, observation)


def test_stale_recovery_frame_is_rejected(example):
    from cps.services.reflow import visual_objects as visual
    doc, raw = example
    record = proposal(doc, raw)
    raw.source_geometry = dict(space='reading', orientation=0, source_rotation=0,
                               page_rect=[0, 0, 399, 500])
    with pytest.raises(ValueError, match='frame'):
        visual.prepare(doc, raw, record)


def test_visual_evidence_survives_actual_native_worker(example, tmp_path):
    from cps.services.reflow.native_ipc import NativeDocument
    doc, raw = example
    record = proposal(doc, raw)
    with NativeDocument(doc.name, scratch_root=tmp_path / 'native') as worker:
        result = pipeline.run(worker, recovery_opts={'mode': 'off'},
                              visual_results={0: record})
    assert len(result.book.figures) == 1
    proof = result.book.figures[0]['visual_evidence']
    assert proof['image_sha256'] == record['image_sha256']
    assert proof['source_bbox'] == pytest.approx([300, 120, 340, 160])
