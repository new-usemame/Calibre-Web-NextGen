"""Product conversion uses the consented pixel reader with source-only fallbacks."""
import os
import zipfile

import pytest

from tests.unit.test_reflow_task import rig, _run, _run_pixel_response, _PixelSession
from tests.unit.test_reflow_app_vision_route import _source
from cps.services.reflow import build_epub, ledger, vision_reading
from cps.services.worker import STAT_FINISH_SUCCESS

pytestmark = pytest.mark.unit


def test_product_client_uses_the_pixel_reader_and_token_confidence_contract(rig):
    client = rig.mod.make_client('source_verified')
    assert isinstance(client, vision_reading.Reader)
    assert client.confidence_protocol == 'tokens' and client.reuse_legacy is True
    fields = client.api_schema['properties']['blocks']['items']['properties']
    assert 'tokens' in fields and 'text' not in fields
    assert fields['tokens']['items']['properties']['low_confidence']['type'] == 'boolean'


def test_product_sample_adopts_complete_pixel_reading_without_layout_generation(rig, monkeypatch):
    session = _PixelSession()
    task = _run_pixel_response(rig, monkeypatch, session, mode='sample', sample_pages=1)
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert len(session.calls) == 1
    assert task.results['report']['fidelity']['pages_model'] == 1
    assert task.results['report']['vision']['pages'] == [0]
    assert rig.local_db.session.commits == 0
    with zipfile.ZipFile(task.results['path']) as archive:
        chapters = [archive.read(name).decode() for name in archive.namelist()
                    if name.startswith('OEBPS/ch') and name.endswith('.xhtml')]
    import xml.etree.ElementTree as ET
    from tests.unit.test_round4_defects import plain
    ns = {'x': 'http://www.w3.org/1999/xhtml'}
    readings = [plain(node) for text in chapters for node in
                ET.fromstring(text).findall('.//x:span[@data-source-block]', ns)]
    assert readings == ['The clear sample remains readable. Another simple line follows the first.']
    payload = session.calls[0]
    assert payload['model'] == vision_reading.MODEL
    assert payload['messages'][0]['content'][1]['type'] == 'image_url'
    schema = payload['response_format']['json_schema']['schema']
    assert 'blocks' in schema['properties'] and 'select' not in schema['properties']


def test_clear_prose_product_conversion_never_dispatches_or_reserves(rig, monkeypatch):
    import requests
    path = rig.folder / 'Book - Author.pdf'
    _source(path, pages=3)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError('Clean native prose must not dispatch a pixel request.')
    monkeypatch.setattr(requests, 'post', forbidden)
    monkeypatch.setattr(rig.mod.config, 'resolved_openrouter_key', lambda: 'inert-configured-key')
    # A source-bound quote permits the paid route; all three clean native pages
    # still complete deterministically and reserve no money.
    from cps.services.reflow import vision_quote
    import pymupdf
    with pymupdf.open(path) as doc:
        quote = vision_quote.measure(doc)
    options = rig.mod.ReflowOptions(dict(mode='full', review_mode='source_verified', cost_cap_usd=1))
    options.source_sha256 = quote['source_sha256']; options.consent_quote = quote
    task = rig.mod.TaskReflowPdf(5, 7, options); task.run(None)
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert calls == []
    assert task.results['report']['fidelity']['pages_model'] == 0
    assert task.results['report']['fidelity']['pages_deterministic'] == 3
    assert task.results['report']['cost']['spend_usd'] == 0
    audit = ledger.Ledger(os.path.join(rig.root, 'jobs', '5', task.job_id + '.jsonl'), 1)
    assert audit.entries('reservation') == []
    assert audit.spent() == audit.pending_usd() == 0


def test_product_consent_binds_actual_pixel_requests(rig, monkeypatch):
    session = _PixelSession()
    task = _run_pixel_response(rig, monkeypatch, session)
    assert task.stat == STAT_FINISH_SUCCESS, task.error
    assert len(session.calls) == 1
    assert task.options.consent_quote['source_sha256'] == task.options.source_sha256
    assert task.results['report']['source']['pdf_sha256'] == task.options.source_sha256
    assert task.results['report']['fidelity']['pages_model'] == 2
    assert task.results['report']['vision']['pages'] == [0, 1]
    sidecar = build_epub.read_sidecar(task.results['path'])
    assert sidecar['source_document_sha256'] == task.options.source_sha256
    assert set(sidecar['vision_pages']) == {0, 1}
