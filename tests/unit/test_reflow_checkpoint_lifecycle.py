"""Synthetic page edits and job folders exercise preparation ownership."""
import copy
import os
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from cps.services.reflow import retention, unit_recovery as ur, vision_pipeline as vp
from tests.unit.test_reflow_task import rig


def _prepare(tmp_path, monkeypatch):
    doc = pymupdf.open()
    page = doc.new_page(width=240, height=300)
    page.insert_text((20, 40), 'The clear sample remains readable.')
    answer = {'blocks': [{'id': 'body', 'role': 'body',
                         'text': 'The clear sample remains readable.',
                         'bbox': [80, 80, 950, 160], 'marker': '',
                         'low_confidence': [], 'continues_previous': False,
                         'continues_next': False}]}

    class Reader:
        cache = tmp_path / 'cache' / 'readings'
        calls = 0

        def read(self, image):
            self.calls += 1
            return {'answer': copy.deepcopy(answer)}

    from cps.services.reflow import vision_pixels
    monkeypatch.setattr(vision_pixels, 'words', lambda *args: [])
    monkeypatch.setattr(vp, 'bind_geometry', lambda value, *args: (value, []))
    monkeypatch.setattr(vp, '_source_note_boundaries', lambda value, *args: (value, []))
    monkeypatch.setattr(vp, 'layer_words', lambda *args: [])
    reader = Reader()
    controller = ur.Controller(reader, [0], tmp_path / 'prepare')
    vp._prepare_scanned_page(doc, 0, reader, controller, None, None)
    return doc, reader, controller


@pytest.mark.parametrize('edit', ['text', 'vector', 'resource'])
def test_prepared_checkpoint_rejects_current_page_mutation(tmp_path, monkeypatch, edit):
    doc, reader, controller = _prepare(tmp_path, monkeypatch)
    try:
        before = doc[0].get_pixmap().samples
        changed_object = False
        if edit == 'text':
            doc[0].insert_text((20, 80), 'A different synthetic line.')
        elif edit == 'vector':
            doc[0].draw_rect(pymupdf.Rect(20, 100, 180, 160), fill=(1, 0, 0))
        else:
            resource = doc[0].get_fonts()[0][0]
            before_object = doc.xref_object(resource)
            doc.update_object(resource, before_object.replace('/Helvetica', '/Courier'))
            changed_object = doc.xref_object(resource) != before_object
        # MuPDF can retain an already-loaded font; a changed resource still
        # invalidates the authoritative preparation before a later renderer.
        assert changed_object or doc[0].get_pixmap().samples != before
        with pytest.raises(ur.SourceIntegrityError, match='source.*changed'):
            vp._prepare_scanned_page(doc, 0, reader, controller, None, None)
        assert reader.calls == 1, 'Source mutation cannot authorize another reading.'
    finally:
        doc.close()


def test_unchanged_source_still_reuses_independent_preparation(tmp_path, monkeypatch):
    doc, reader, controller = _prepare(tmp_path, monkeypatch)
    try:
        prepared, _ = vp._prepare_scanned_page(doc, 0, reader, controller, None, None)
        prepared['answer']['blocks'][0]['text'] = 'An abandoned mutable answer.'
        again, _ = vp._prepare_scanned_page(doc, 0, reader, controller, None, None)
        assert again['answer']['blocks'][0]['text'] == 'The clear sample remains readable.'
        assert reader.calls == 1
    finally:
        doc.close()


def test_checkpoints_use_job_owned_scratch_and_normal_exit_cleans_it(tmp_path, monkeypatch):
    doc = pymupdf.open()
    doc.new_page(width=100, height=100)
    scratch = tmp_path / 'reflow' / 'conversion-scratch'
    output = tmp_path / 'library' / 'candidate.epub'
    visited = []

    def attempt(*args, **kwargs):
        controller = ur.current()
        visited.append(controller.staging)
        assert controller.staging.parent == scratch / 'c7d291a024e758af'
        (controller.staging / 'prepared-0.pickle').write_bytes(b'synthetic checkpoint')
        return {'passed': True}

    monkeypatch.setattr(vp, '_convert_attempt', attempt)
    try:
        result = vp.convert(doc, output, SimpleNamespace(),
                            scratch_dir=scratch, job_id='c7d291a024e758af')
        assert result == {'passed': True}
        assert len(visited) == 1 and not visited[0].exists()
        assert not (scratch / 'c7d291a024e758af').exists()
        assert not list(output.parent.glob('.unit-recovery-*'))
    finally:
        doc.close()


def _job_scratch(root, ident):
    directory = Path(root) / 'conversion-scratch' / ident / 'prepare-abcd'
    directory.mkdir(parents=True)
    (directory / 'prepared-0.pickle').write_bytes(b'synthetic checkpoint')
    return directory.parent


def test_interrupted_recovery_removes_job_checkpoint_but_preserves_live_job(rig):
    from cps.services.reflow.ledger import Ledger
    from cps.services.worker import STAT_STARTED
    stale, live = 'e6d930ac541f827b', 'a821e1b4f7650c93'
    for ident in (stale, live):
        ledger = Ledger(Path(rig.root) / 'jobs' / '5' / (ident + '.jsonl'),
                        cap_usd=0, job_id=ident)
        ledger.record({'kind': 'job', 'event': 'start', 'user_id': 1})
    old_path, live_path = _job_scratch(rig.root, stale), _job_scratch(rig.root, live)
    worker = SimpleNamespace(tasks=[(None, None, None, SimpleNamespace(
        is_reflow=True, stat=STAT_STARTED, job_id=live), None)])
    assert rig.mod.recover_interrupted_jobs(worker) == [stale]
    assert not old_path.exists() and live_path.exists()
    assert rig.mod.recover_interrupted_jobs(worker) == []


def test_retention_removes_abandoned_job_scratch_only(tmp_path):
    abandoned = _job_scratch(tmp_path, 'd391c2e4a560b78f')
    active = _job_scratch(tmp_path, 'e761f2c830b94a5d')
    unrelated = _job_scratch(tmp_path, 'not.a.job')
    for directory in (abandoned, active, unrelated):
        os.utime(directory, (1, 1))
    removed = retention.sweep(str(tmp_path), active_jobs=[active.name], now=3 * retention.DAY)
    assert removed['conversion-scratch'] == 1
    assert not abandoned.exists() and active.exists() and unrelated.exists()


def test_job_scratch_cleanup_does_not_follow_symlink(tmp_path):
    outside = tmp_path / 'other'
    outside.mkdir()
    marker = outside / 'preserve'
    marker.write_text('Synthetic unrelated data.', encoding='utf-8')
    scratch = tmp_path / 'conversion-scratch'
    scratch.mkdir()
    (scratch / 'd391c2e4a560b78f').symlink_to(outside, target_is_directory=True)
    retention.sweep(str(tmp_path), now=3 * retention.DAY)
    assert marker.read_text(encoding='utf-8') == 'Synthetic unrelated data.'


def test_hard_exit_checkpoint_is_removed_by_interrupted_recovery(rig, tmp_path):
    import subprocess
    import sys
    from cps.services.reflow.ledger import Ledger
    ident = 'a4c170bd896e235f'
    ledger = Ledger(Path(rig.root) / 'jobs' / '5' / (ident + '.jsonl'),
                    cap_usd=0, job_id=ident)
    ledger.record({'kind': 'job', 'event': 'start', 'user_id': 1})
    script = '''
import os, sys
from pathlib import Path
from types import SimpleNamespace
import pymupdf
from cps.services.reflow import unit_recovery as ur, vision_pipeline as vp

def attempt(*args, **kwargs):
    controller = ur.current()
    controller.readings[0] = {'answer': {'blocks': []}}
    controller.store_prepared(0, {'answer': {'blocks': []}})
    os._exit(29)

vp._convert_attempt = attempt
doc = pymupdf.open()
doc.new_page(width=100, height=100)
vp.convert(doc, Path(sys.argv[1]), SimpleNamespace(),
           scratch_dir=Path(sys.argv[2]), job_id=sys.argv[3])
'''
    output = tmp_path / 'publication' / 'candidate.epub'
    scratch = Path(rig.root) / 'conversion-scratch'
    result = subprocess.run([sys.executable, '-P', '-c', script,
                             str(output), str(scratch), ident],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 29, result.stderr
    assert len(list((scratch / ident).glob('prepare-*/prepared-0.pickle'))) == 1
    assert not list(output.parent.glob('.unit-recovery-*'))
    assert rig.mod.recover_interrupted_jobs(SimpleNamespace(tasks=[])) == [ident]
    assert not (scratch / ident).exists()


def test_source_graph_fingerprint_tracks_inherited_resources_and_visibility():
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=240, height=300)
        page.insert_text((20, 40), 'The synthetic sample stays clear.')
        parent = int(doc.xref_get_key(page.xref, 'Parent')[1].split()[0])
        resource = doc.xref_get_key(page.xref, 'Resources')[1]
        doc.xref_set_key(parent, 'Resources', resource)
        doc.xref_set_key(page.xref, 'Resources', 'null')
        before = ur.page_fingerprint(doc, 0)
        resource_xref = int(resource.split()[0])
        doc.xref_set_key(resource_xref, 'SyntheticFlag', 'true')
        inherited_changed = ur.page_fingerprint(doc, 0)
        assert inherited_changed != before
        catalog = doc.pdf_catalog()
        doc.xref_set_key(catalog, 'OCProperties', '<</D<</BaseState /OFF>>>>')
        assert ur.page_fingerprint(doc, 0) != inherited_changed
    finally:
        doc.close()


def test_pdf_reference_scanner_ignores_literal_reference_shapes():
    assert list(ur._pdf_references(
        '<< /Contents [12 0 R 14 0 R] /Name (99 0 R) /Other <343220302052> '
        '/Nested (outer (88 0 R) end) /Escaped (text \\(77 0 R\\)) >>')) == [12, 14]


def test_source_graph_fingerprint_tracks_runtime_layer_visibility():
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=240, height=300)
        layer = doc.add_ocg('Synthetic layer')
        page.insert_text((20, 40), 'The synthetic sample stays clear.', oc=layer)
        before_pixels = page.get_pixmap().samples
        before = ur.page_fingerprint(doc, 0)
        doc.set_layer_ui_config(0, 1)
        assert page.get_pixmap().samples != before_pixels
        assert ur.page_fingerprint(doc, 0) != before
    finally:
        doc.close()
