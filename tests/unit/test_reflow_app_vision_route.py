"""Synthetic app jobs bind pixel conversion to the source and consented requests."""
import base64
import hashlib
import inspect
import io
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest
from PIL import Image

from cps.services.reflow import build_epub, extract, ledger, vision_reading
from cps.services.worker import STAT_FAIL, STAT_FINISH_SUCCESS
from tests.unit.test_reflow_task import rig
from tests.unit.test_reflow_api import (
    _ctx, _json, _start, _status, _user, _wire, pdf_on_disk,
)


def _source(path, *, scanned=False, pages=2):
    document = pymupdf.open()
    for index in range(pages):
        ink = pymupdf.open()
        page = ink.new_page(width=240, height=300)
        page.insert_text((20, 40), 'The clear sample remains readable.', fontsize=10)
        page.insert_text((20, 60), 'Another simple line follows the first.', fontsize=10)
        target = document.new_page(width=240, height=300)
        if scanned:
            target.insert_image(target.rect, stream=page.get_pixmap().tobytes('png'))
        else:
            target.show_pdf_page(target.rect, ink, 0)
        ink.close()
    document.save(path)
    document.close()


def _run(rig, **options):
    values = {'mode': 'full', 'review_mode': 'deterministic',
              'source_recovery': 'off', 'cost_cap_usd': 0}
    values.update(options)
    task = rig.mod.TaskReflowPdf(5, 7, values)
    task.run(None)
    return task


def test_actual_task_calls_vision_reader_without_a_structural_fallback(rig, monkeypatch):
    from cps.services.reflow.native_ipc import NativeDocument
    calls = []

    def vision(self, out_path, reader, **kwargs):
        calls.append((Path(out_path), reader, kwargs))
        raise RuntimeError('Synthetic vision-route stop.')

    def forbidden(*args, **kwargs):
        raise AssertionError('Task entered the obsolete structural conversion route.')

    monkeypatch.setattr(NativeDocument, 'convert_vision', vision, raising=False)
    structural = getattr(rig.mod, 'structural_pipeline', None)
    if structural is not None:
        monkeypatch.setattr(structural, 'run_structural', forbidden)
    _source(rig.folder / 'Book - Author.pdf')
    task = _run(rig)
    assert len(calls) == 1, 'The app task must call the pixel converter.'
    path, reader, kwargs = calls[0]
    assert path.is_relative_to(Path(rig.root))
    assert isinstance(reader, vision_reading.Reader) and reader.key is None
    assert reader.confidence_protocol == 'tokens' and reader.reuse_legacy is True
    assert kwargs.get('quote') is None
    assert task.stat == STAT_FAIL and 'Synthetic vision-route stop.' in task.error
    assert rig.local_db.session.commits == 0


@pytest.mark.parametrize('scanned', [False, True])
def test_real_no_key_task_publishes_vision_native_text_or_disclosed_page(rig, monkeypatch, scanned):
    import requests

    def forbidden(*args, **kwargs):
        raise AssertionError('No-key conversion attempted a provider request.')

    monkeypatch.setattr(requests.sessions.Session, 'request', forbidden)
    source = rig.folder / 'Book - Author.pdf'
    _source(source, scanned=scanned)
    task = _run(rig)
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert rig.local_db.session.commits == 1
    path = Path(task.results['path'])
    assert path.is_file() and build_epub.validate(str(path)) == []
    sidecar = build_epub.read_sidecar(str(path))
    assert sidecar['version'] == vision_reading.VERSION
    assert sidecar['source_document_sha256'] == extract.document_fingerprint(source)
    assert set(map(int, sidecar['pages'])) == {0, 1}
    if scanned:
        assert sidecar['native_pages'] == []
        assert sidecar['degraded_unit_count'] == 2
        assert {u['page'] for u in sidecar['degraded_units'] if u['block'] is None} == {0, 1}
    else:
        assert sidecar['native_pages'] == [0, 1]
        assert sidecar['degraded_unit_count'] == 0
    audit = ledger.Ledger(Path(rig.root) / 'jobs' / '5' / (task.job_id + '.jsonl'),
                          cap_usd=0, job_id=task.job_id)
    assert audit.spent() == audit.pending_usd() == 0
    assert audit.entries('reservation') == []
    artifact = audit.entries('artifact')[-1]
    assert artifact['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert task.results['sha256'] == artifact['sha256']
    assert audit.job()['status'] == 'done'


def test_real_vision_sample_limits_source_scope_and_does_not_file(rig):
    source = rig.folder / 'Book - Author.pdf'
    _source(source, pages=3)
    task = _run(rig, mode='sample', sample_pages=1)
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert rig.local_db.session.commits == 0
    assert not (rig.folder / 'Book - Author.epub').exists()
    sidecar = build_epub.read_sidecar(task.results['path'])
    assert sidecar['version'] == vision_reading.VERSION
    assert set(map(int, sidecar['pages'])) == {0}
    assert task.results['sha256'] == hashlib.sha256(Path(task.results['path']).read_bytes()).hexdigest()


@pytest.fixture
def api(tmp_path, monkeypatch):
    from cps.api import reflow as module
    monkeypatch.setattr(module, 'REFLOW_DIR', str(tmp_path / 'reflow'))
    monkeypatch.setattr(module.tasks_reflow, 'REFLOW_DIR', str(tmp_path / 'reflow'))
    return module


def test_api_rejects_obsolete_markup_consent_before_enqueue(api, monkeypatch, pdf_on_disk):
    _wire(api, monkeypatch, pdf_on_disk)
    response, added = _start(api, {'consent': True, 'consent_contract': 'source-layout-1'})
    assert _status(response) == 409
    assert _json(response)['error']['code'] == 'current_consent_required'
    added.assert_not_called()


def test_fast_assessment_identifies_the_pixel_reader_and_consent_contract(api, monkeypatch, pdf_on_disk):
    _wire(api, monkeypatch, pdf_on_disk)
    with _ctx('/api/v1/books/5/reflow/estimate'), patch.object(api, 'current_user', _user()):
        result = _json(inspect.unwrap(api.reflow_estimate)(5))
    assert result['consent_contract'] == 'vision-page-1'
    assert result['review']['model'] == vision_reading.MODEL
    assert result['review']['scope'] == 'printed-page pixel transcription'
    assert result['review']['max_output_tokens'] == 20000


def _quote_and_image(tmp_path):
    from cps.services.reflow import vision_quote
    from cps.services.reflow.source_display import SourceDisplay
    path = tmp_path / 'synthetic-source.pdf'
    _source(path, scanned=True, pages=1)
    with pymupdf.open(path) as document:
        quote = vision_quote.measure(document)
        image = SourceDisplay(document, 0).pixmap(
            scale=2, max_pixels=2_500_000).tobytes('jpg', jpg_quality=90)
    return path, quote, image


def _actual_payload(tmp_path, image, operation='primary'):
    reader = vision_reading.Reader('inert-test-key', tmp_path / 'readings',
        ledger.Ledger(tmp_path / 'ledger.jsonl', cap_usd=5),
        confidence_protocol='tokens', reuse_legacy=True)
    captured = []

    class Captured(Exception):
        pass

    def capture(payload, *args, **kwargs):
        captured.append(payload)
        raise Captured()

    reader._direct_post = capture
    if operation == 'retry':
        reader = reader._retry_reader()
        reader._direct_post = capture
    with pytest.raises(Captured):
        reader.read_marker(image) if operation == 'marker' else reader.read(image)
    assert len(captured) == 1
    return captured[0]


def _context(quote, image, payload, operation='primary'):
    from cps.services.reflow import vision_quote
    with Image.open(io.BytesIO(image)) as frame:
        width, height = frame.size
    schema = payload['response_format']['json_schema']['schema']
    return {'page_index0': 0, 'source_sha256': quote['source_sha256'],
            'image_sha256': hashlib.sha256(image).hexdigest(),
            'operation': operation, 'bound_usd': vision_quote.bound(
                width, height, payload['messages'][0]['content'][0]['text'],
                schema, payload['max_tokens'])}


@pytest.mark.parametrize('operation', ['primary', 'retry', 'marker'])
def test_consent_authorizes_the_actual_reader_payload_once_per_allowance(tmp_path, operation):
    from cps.services.reflow import vision_quote
    from cps.services.reflow.unit_recovery import SourceIntegrityError
    source, quote, image = _quote_and_image(tmp_path)
    payload = _actual_payload(tmp_path, image, operation)
    context = _context(quote, image, payload, operation)
    authorizer = vision_quote.Authorizer(quote, extract.document_fingerprint(source), [0])
    maximum = vision_quote.MARKERS_PER_PAGE if operation == 'marker' else 1
    for _ in range(maximum):
        assert authorizer.authorize(payload, context) == operation
    with pytest.raises(SourceIntegrityError, match='consented'):
        authorizer.authorize(payload, context)


@pytest.mark.parametrize('mutation', ['source', 'pixels', 'schema', 'prompt', 'model', 'bound', 'page',
    'role', 'strict', 'usage', 'extra', 'content_type'])
def test_consent_rejects_a_request_outside_its_exact_source_and_wire_contract(tmp_path, mutation):
    from cps.services.reflow import vision_quote
    from cps.services.reflow.unit_recovery import SourceIntegrityError
    source, quote, image = _quote_and_image(tmp_path)
    payload = _actual_payload(tmp_path, image)
    context = _context(quote, image, payload)
    if mutation == 'source':
        context['source_sha256'] = '0' * 64
    elif mutation == 'pixels':
        with Image.open(io.BytesIO(image)) as frame:
            frame.putpixel((0, 0), (0, 0, 0))
            stream = io.BytesIO()
            frame.save(stream, format='JPEG')
        payload['messages'][0]['content'][1]['image_url']['url'] = (
            'data:image/jpeg;base64,' + base64.b64encode(stream.getvalue()).decode())
        context['image_sha256'] = hashlib.sha256(stream.getvalue()).hexdigest()
    elif mutation == 'schema':
        payload['response_format']['json_schema']['schema']['additionalProperties'] = True
    elif mutation == 'prompt':
        payload['messages'][0]['content'][0]['text'] += '\nSynthetic unconsented instruction.'
    elif mutation == 'model':
        payload['model'] = 'synthetic-other-model'
    elif mutation == 'bound':
        context['bound_usd'] += .01
    elif mutation == 'role':
        payload['messages'][0]['role'] = 'system'
    elif mutation == 'strict':
        payload['response_format']['json_schema']['strict'] = False
    elif mutation == 'usage':
        payload['usage']['include'] = False
    elif mutation == 'extra':
        payload['seed'] = 7
    elif mutation == 'content_type':
        payload['messages'][0]['content'][0]['type'] = 'synthetic_other_type'
    else:
        context['page_index0'] = 1
    authorizer = vision_quote.Authorizer(quote, extract.document_fingerprint(source), [0])
    with pytest.raises(SourceIntegrityError, match='consented'):
        authorizer.authorize(payload, context)


def test_disabling_the_report_removes_its_document_and_all_navigation_references(rig):
    import zipfile
    from xml.etree import ElementTree as ET
    _source(rig.folder / 'Book - Author.pdf')
    task = _run(rig, include_report_page=False)
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert build_epub.validate(task.results['path']) == []
    with zipfile.ZipFile(task.results['path']) as archive:
        assert 'OEBPS/vision-about.xhtml' not in archive.namelist()
        for name in ('OEBPS/content.opf', 'OEBPS/nav.xhtml', 'OEBPS/toc.ncx'):
            root = ET.fromstring(archive.read(name))
            assert not any(node.get('href') == 'vision-about.xhtml' or
                           node.get('src') == 'vision-about.xhtml' or
                           node.get('id') == 'about' or node.get('idref') == 'about'
                           for node in root.iter()), name
        # Dropping the generated report must leave the converted book navigable.
        opf = ET.fromstring(archive.read('OEBPS/content.opf'))
        ns = '{http://www.idpf.org/2007/opf}'
        manifest = {item.get('id'): item.get('href') for item in opf.find(ns + 'manifest')}
        spine = [manifest[item.get('idref')] for item in opf.find(ns + 'spine')]
        assert any(name.startswith('ch') and name.endswith('.xhtml') for name in spine)
        assert build_epub.read_sidecar(task.results['path'])['app_report']['route'] == 'vision'
