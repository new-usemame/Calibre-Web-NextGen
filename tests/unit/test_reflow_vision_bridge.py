"""Synthetic native-worker and parent-finance boundary regressions."""
import base64
from contextlib import contextmanager
import hashlib
import io
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace
import zipfile

from PIL import Image
import pymupdf
import pytest
import requests

from cps.services.reflow import native_ipc, unit_recovery, vision_bridge, vision_reading
from cps.services.reflow.ledger import CapExceeded, Ledger

pytestmark = pytest.mark.unit
SOURCE = 'a' * 64


def _synthetic_report(source_sha):
    return dict(source_document_sha256=source_sha, pages={0: dict(source='native',
        words=[dict(text='token' + str(index), bbox=[1., 2., 3., 4.],
            uncertain=True, source=dict(page=0, rect=[1., 2., 3., 4.]))
            for index in range(5000)], page_geometry=(1., 2., 3., 4.))}, native_pages=[0], vision_pages=[],
        new_cost_usd=0., recovery_financial_state=dict(cumulative_spend_usd=0., held_usd=0.))


def test_real_native_worker_returns_complete_geometry_report_inside_original_bound(tmp_path, monkeypatch):
    """Scale down the unchanged codec ceiling, rather than allocating huge inputs."""
    from cps.services.reflow import native_codec, extract
    source = tmp_path / 'source.pdf'
    with pymupdf.open() as document:
        document.new_page().insert_text((30, 40), 'A synthetic source page.')
        document.save(source)
    bound = 640 * 1024
    expected = _synthetic_report(extract.document_fingerprint(source))
    with monkeypatch.context() as context:
        context.setattr(native_codec, 'MAX_BYTES', bound)
        with pytest.raises(ValueError, match='payload exceeds size bound'):
            native_codec.dumps(expected)
        compact = vision_bridge.compact_report(expected)
        assert len(native_codec.dumps(compact)) < bound
        assert native_codec.loads(native_codec.dumps(compact)) == expected
    worker = tmp_path / 'worker.py'
    worker.write_text('''import importlib,importlib.util,os,sys,zipfile
from pathlib import Path
root=Path(ROOT_LITERAL)
name='_reflow_native_runtime'
spec=importlib.util.spec_from_file_location(name,root/'__init__.py',submodule_search_locations=[str(root)])
module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
runtime=importlib.import_module(name+'.native_worker_runtime')
codec=importlib.import_module(name+'.native_codec');codec.MAX_BYTES=640*1024
pipeline=importlib.import_module(name+'.vision_pipeline')
extract=importlib.import_module(name+'.extract')
REPORT_FACTORY
def convert(doc,path,reader,**kwargs):
 with zipfile.ZipFile(path,'w') as archive: archive.writestr('mimetype','application/epub+zip')
 return _synthetic_report(extract.document_fingerprint(doc))
pipeline.convert=convert
with os.fdopen(int(sys.argv[3]),'w',buffering=1) as control:
 raise SystemExit(runtime.serve(int(sys.argv[1]),sys.argv[2],control))
'''.replace('ROOT_LITERAL', repr(str(native_ipc.WORKER.parent)))
        .replace('REPORT_FACTORY', inspect.getsource(_synthetic_report)))
    monkeypatch.setattr(native_ipc, 'WORKER', worker)
    state = tmp_path / 'state'
    reader = vision_reading.Reader(None, state / 'cache', Ledger(state / 'job.jsonl', 0))
    with native_ipc.NativeDocument(source, scratch_root=state / 'scratch', cache_root=state) as native:
        child = native.process
        actual = native.convert_vision(tmp_path / 'output.epub', reader)
        assert child.pid != os.getpid()
        assert actual == expected
        assert len(actual['pages'][0]['words']) == 5000
        assert actual['pages'][0]['words'][-1]['bbox'] == [1., 2., 3., 4.]
        assert actual['pages'][0]['page_geometry'] == (1., 2., 3., 4.)
        assert actual['recovery_financial_state'] == expected['recovery_financial_state']
    assert child.returncode == 0
    assert list((state / 'scratch').iterdir()) == []
    assert reader.ledger.spent() == reader.ledger.pending_usd() == 0


def test_compact_report_preserves_types_keys_order_signed_zero_and_every_value():
    import copy
    import math
    from cps.services.reflow import native_codec
    original = dict(pages={0: dict(words=[dict(text='Synthetic token', bbox=[1., 2., 3., 4.]),
        dict(text='Synthetic token', bbox=[1., 2., 3., 4.])], geometry=(1., 2., 3., 4.))},
        numbers=[True, 1, 1., -0., 0.], keys={0: 'integer', '0': 'string'}, empty=[[], ()])
    saved = copy.deepcopy(original)
    compact = vision_bridge.compact_report(original)
    assert original == saved
    assert original['pages'][0]['words'][0] is not original['pages'][0]['words'][1]
    assert compact['pages'][0]['words'][0] is compact['pages'][0]['words'][1]
    actual = native_codec.loads(native_codec.dumps(compact))
    assert actual == saved
    assert list(actual['keys']) == [0, '0']
    assert [type(value) for value in actual['numbers']] == [bool, int, float, float, float]
    assert math.copysign(1, actual['numbers'][3]) == -1
    assert math.copysign(1, actual['numbers'][4]) == 1
    assert type(actual['pages'][0]['geometry']) is tuple
    assert [type(value) for value in actual['empty']] == [list, tuple]


@pytest.mark.parametrize('value', [dict(number=float('inf')), dict(number=float('nan')),
    dict(number={1, 2}), dict(keys={True: 'boolean'}), ['not a report']])
def test_compact_report_refuses_unsupported_values(value):
    with pytest.raises(ValueError): vision_bridge.compact_report(value)


def test_compact_report_refuses_cycles_and_excessive_depth(monkeypatch):
    from cps.services.reflow import native_codec
    cyclic = {}; cyclic['self'] = cyclic
    with pytest.raises(ValueError, match='Cyclic'): vision_bridge.compact_report(cyclic)
    monkeypatch.setattr(native_codec, 'MAX_DEPTH', 3)
    with pytest.raises(ValueError, match='nesting'): vision_bridge.compact_report(dict(a=[dict(b=[1])]))


def test_compact_report_keeps_the_original_wire_ceiling(monkeypatch):
    from cps.services.reflow import native_codec
    monkeypatch.setattr(native_codec, 'MAX_BYTES', 1024)
    compact = vision_bridge.compact_report(dict(text='x' * 2048))
    with pytest.raises(ValueError, match='payload exceeds size bound'):
        native_codec.dumps(compact)


def image():
    output = io.BytesIO()
    Image.new('RGB', (90, 120), 'white').save(output, 'JPEG')
    return output.getvalue()


def reading(text='A synthetic sentence written for this test.'):
    return {'blocks': [dict(id='b1', role='body', text=text, marker='',
        bbox=[50, 50, 950, 800], low_confidence=[],
        continues_previous=False, continues_next=False)]}


def response(answer=None, *, cost=.0004, actual_model=None):
    return SimpleNamespace(status_code=200, json=lambda: dict(id='synthetic-request',
        model=actual_model or vision_reading.MODEL, usage={'cost': cost},
        choices=[dict(finish_reason='stop', message={'content': json.dumps(answer or reading())})]))


def rig(tmp_path, monkeypatch, *, cap=.1, authorize=None, reply=None):
    calls = []
    ledger = Ledger(tmp_path / 'parent.jsonl', cap, job_id='synthetic-job')
    parent = vision_reading.Reader('inert-key', tmp_path / 'cache', ledger)
    class Authorizer:
        def authorize(self, payload, context):
            if authorize:
                authorize(payload, context)
    broker = vision_bridge.ParentBroker(parent, SOURCE, [0], authorizer=Authorizer())
    def post(*args, **kwargs):
        assert Ledger(ledger.path, cap).pending_usd() > 0
        calls.append((os.getpid(), kwargs))
        return reply or response()
    monkeypatch.setattr(requests, 'post', post)
    def rpc(operation, arguments):
        return vision_bridge.checked_reply(broker.handle(dict(operation=operation,
            arguments=arguments, context=dict(source_sha256=SOURCE, page_index0=0))))
    child = vision_bridge.ChildReader(rpc, parent.cache, tmp_path, cap, enabled=True)
    return SimpleNamespace(parent=parent, child=child, ledger=ledger, calls=calls,
                           broker=broker, rpc=rpc)


def test_parent_owns_dispatch_and_settles_exact_charge_before_reply(tmp_path, monkeypatch):
    state = rig(tmp_path, monkeypatch)
    actual = state.child.read(image())
    assert actual['answer'] == reading()
    assert actual['cached'] is False
    assert state.child.key == 'parent-owned-reader'
    assert state.calls[0][0] == os.getpid()
    assert state.calls[0][1]['headers']['Authorization'] == 'Bearer inert-key'
    assert state.ledger.spent() == pytest.approx(.0004)
    assert state.ledger.pending_usd() == 0
    assert state.child.read(image())['cached'] is True
    assert len(state.calls) == 1
    assert [row['event'] for row in state.ledger.entries('reservation')] == ['pending', 'reconciled']


def test_bridge_preserves_native_source_repair_without_reread(tmp_path, monkeypatch):
    state = rig(tmp_path, monkeypatch, reply=response(reading('A synthetic <n:bad> sentence.')))
    repaired = []
    def source_repair(answer):
        repaired.append(answer)
        return reading(), {'kind': 'synthetic_source_identity'}
    result = state.child.read_unit(image(), source_repair=source_repair, page=0)
    assert result['answer'] == reading()
    assert result['source_repairs'] == [{'kind': 'synthetic_source_identity'}]
    assert len(repaired) == 1 and len(state.calls) == 1
    assert state.ledger.spent() == pytest.approx(.0004)
    assert state.ledger.pending_usd() == 0
    assert not list(state.parent.cache.glob('unit-reread-*'))


def test_retry_keeps_parent_bridge_and_confidence_contract(tmp_path, monkeypatch):
    state = rig(tmp_path, monkeypatch)
    retry = state.child._retry_reader()
    assert isinstance(retry, vision_bridge.ChildReader)
    assert retry.rpc is state.child.rpc
    assert retry.key == state.child.key
    assert retry.prompt == state.parent._retry_reader().prompt
    assert retry.api_schema == state.parent._retry_reader().api_schema
    assert state.child._retry_reader(offline=True).key is None
    assert retry.read(image())['answer'] == reading()
    assert len(state.calls) == 1


def test_own_cap_is_checked_in_parent_before_http(tmp_path, monkeypatch):
    state = rig(tmp_path, monkeypatch, cap=0)
    with pytest.raises(CapExceeded):
        state.child.read(image())
    assert state.calls == []
    assert state.ledger.spent() == state.ledger.pending_usd() == 0


def test_out_of_quote_request_releases_unsent_reservation_durably(tmp_path, monkeypatch):
    def refuse(payload, context):
        raise unit_recovery.SourceIntegrityError('Synthetic quote mismatch')
    state = rig(tmp_path, monkeypatch, authorize=refuse)
    with pytest.raises(unit_recovery.SourceIntegrityError, match='quote mismatch'):
        state.child.read(image())
    reopened = Ledger(state.ledger.path, .1)
    assert reopened.spent() == reopened.pending_usd() == 0
    assert state.calls == []
    rejected = list(state.parent.cache.glob('*.rejected'))
    assert len(rejected) == 1
    assert json.loads(rejected[0].read_text())['reason'] == 'local_pre_dispatch'
    assert not list(state.parent.cache.glob('*.pending'))


def test_known_charge_survives_child_loss_before_its_own_settlement(tmp_path, monkeypatch):
    state = rig(tmp_path, monkeypatch)
    frame = image(); identity = hashlib.sha256(frame).hexdigest()
    bound = state.parent._bounded_image_request(frame, state.parent.prompt,
        state.parent.api_schema, state.parent.confidence_protocol)
    attempt = state.rpc('ledger', dict(method='reserve_attempt',
        args=[identity, bound, vision_reading.MODEL, vision_reading.VERSION], kwargs={}))
    pending = identity + '-' + state.parent._configuration() + '.pending'
    payload = dict(messages=[dict(content=[dict(type='text', text=state.parent.prompt),
        dict(type='image_url', image_url={'url':'data:image/jpeg;base64,'+base64.b64encode(frame).decode()})])])
    result = state.rpc('post', dict(payload=payload, pending=pending,
        reservation=attempt, bound_usd=bound, title='ignored', timeout=[1, 1]))
    # Do not let the simulated child receive, parse or settle its response.
    assert result['status'] == 200
    reopened = Ledger(state.ledger.path, .1)
    assert reopened.spent() == pytest.approx(.0004) and reopened.pending_usd() == 0
    saved = json.loads((state.parent.cache / pending).read_text())
    assert saved['response']['usage']['cost'] == .0004
    assert saved['reservation'] == attempt
    assert state.calls[0][1]['timeout'] == (30, 240)


def test_substituted_model_is_billed_but_never_adopted(tmp_path, monkeypatch):
    state = rig(tmp_path, monkeypatch, reply=response(actual_model='synthetic-other-model'))
    with pytest.raises(vision_reading.model.UnusableAnswer):
        state.child.read(image())
    assert state.ledger.spent() == pytest.approx(.0004)
    assert state.ledger.pending_usd() == 0
    assert list(state.parent.cache.glob('*.pending'))
    assert not list(state.parent.cache.glob('*.json'))


def test_unknown_ledger_method_and_foreign_source_fail_closed(tmp_path, monkeypatch):
    state = rig(tmp_path, monkeypatch)
    request = dict(operation='ledger', arguments={'method':'__getattribute__',
        'args':['key'], 'kwargs':{}}, context={'source_sha256':SOURCE, 'page_index0':0})
    with pytest.raises(unit_recovery.SourceIntegrityError):
        vision_bridge.checked_reply(state.broker.handle(request))
    request['arguments'] = {'method':'spent','args':[],'kwargs':{}}
    request['context']['source_sha256'] = 'b' * 64
    with pytest.raises(unit_recovery.SourceIntegrityError):
        vision_bridge.checked_reply(state.broker.handle(request))
    assert state.calls == []


def test_native_guards_restore_page_scope_even_when_recovery_raises(tmp_path):
    controller = unit_recovery.Controller(SimpleNamespace(), [0, 1], tmp_path)
    with unit_recovery.activated(controller):
        assert controller._active_page is None
        with unit_recovery.guard(0, 'synthetic_outer'):
            assert controller._active_page == 0
            with unit_recovery.guard([0, 1], 'synthetic_shared'):
                assert controller._active_page is None
            assert controller._active_page == 0
            with pytest.raises(unit_recovery.UnitFailure):
                with unit_recovery.guard(1, 'synthetic_inner'):
                    assert controller._active_page == 1
                    raise ValueError('Synthetic local rejection')
            assert controller._active_page == 0
        assert controller._active_page is None


def test_actual_native_worker_builds_vision_candidate_without_provider(tmp_path):
    source = tmp_path / 'source.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(40, 40, 550, 650),
            'The garden path follows the river. Each reader can open the source page.\n' * 6,
            fontsize=12)
        doc.save(source)
    state = tmp_path / 'state'
    ledger = Ledger(state / 'job.jsonl', 0, job_id='synthetic-job')
    reader = vision_reading.Reader(None, state / 'reading-cache', ledger)
    output = tmp_path / 'candidate.epub'
    events = []
    with native_ipc.NativeDocument(source, scratch_root=state / 'native-scratch',
                                   cache_root=state, progress=events.append) as doc:
        report = doc.convert_vision(output, reader, metadata={'title':'Synthetic garden guide'})
        assert report['native_pages'] == [0]
        assert report['vision_pages'] == []
        assert report['new_cost_usd'] == 0
        assert report['source_document_sha256'] == doc.fingerprint
        assert doc.reader_broker is None
    assert events[0].stage == 'native_preparation'
    assert events[0].page == 0 and events[0].pages == 1
    assert events[0].message == 'Preparing native source pages'
    assert next(e for e in events if e.stage == 'vision').page == 1
    assert ledger.spent() == ledger.pending_usd() == 0
    with zipfile.ZipFile(output) as epub:
        assert json.loads(epub.read('META-INF/reflow.json')) == json.loads(json.dumps(report))
        assert 'Synthetic garden guide' in epub.read('OEBPS/content.opf').decode()
    assert list((state / 'native-scratch').iterdir()) == []


def test_actual_scanned_worker_dispatches_only_through_current_parent_quote(tmp_path, monkeypatch):
    text = ('Every clear sentence follows the line above.\n'
            'The river runs beyond the garden wall.\n'
            'A reader can inspect the original page.\n'
            'This simple record keeps the printed words.\n'
            'The window opens toward the quiet road.\n')
    source = tmp_path / 'scan.pdf'
    with pymupdf.open() as original:
        page = original.new_page(width=400, height=500)
        page.insert_textbox(pymupdf.Rect(40, 60, 360, 420), text, fontsize=13)
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes('png')
    with pymupdf.open() as scanned:
        scanned.new_page(width=400, height=500).insert_image(pymupdf.Rect(0, 0, 400, 500), stream=pixels)
        scanned.save(source)
    state = tmp_path / 'state'
    from cps.services.reflow.shared_budget import SharedLedger, Store
    store = Store(state, lambda: 1)
    ledger = SharedLedger(state / 'job.jsonl', .1, job_id='synthetic-job',
                          store=store, book_id=5, user_id=7)
    parent = vision_reading.Reader('inert-key', state / 'reading-cache', ledger,
                                   confidence_protocol='tokens', reuse_legacy=True)
    calls = []
    answer = reading(text)
    item = answer['blocks'][0]
    item['tokens'] = [dict(text=word, low_confidence=False) for word in item.pop('text').split()]
    item.pop('low_confidence')
    def post(*args, **kwargs):
        calls.append(os.getpid())
        assert Ledger(ledger.path, .1).pending_usd() > 0
        return response(answer)
    monkeypatch.setattr(requests, 'post', post)
    target = state / 'conversion-scratch' / 'synthetic-job' / 'candidate.epub'
    with native_ipc.NativeDocument(source, scratch_root=state / 'native-scratch',
                                   cache_root=state) as doc:
        quote = doc.vision_quote()
        assert quote['eligible_pages'] == 1
        report = doc.convert_vision(target, parent, quote=quote, job_id='synthetic-job')
    assert calls == [os.getpid()]
    assert ledger.spent() == pytest.approx(.0004)
    assert ledger.pending_usd() == 0
    assert 1 - store.status()['remaining_usd'] == pytest.approx(.0004)
    assert report['new_cost_usd'] == pytest.approx(.0004)
    assert report['native_pages'] == [] and report['vision_pages'] == [0]
    assert not any(case.get('block') is None for case in report['degraded_units'])
    assert target.is_file()
    assert list(target.parent.iterdir()) == [target]


def test_child_cannot_replace_parent_metered_charge_or_release_ambiguous_dispatch(tmp_path, monkeypatch):
    state = rig(tmp_path, monkeypatch)
    state.child.read(image())
    attempt = state.ledger.entries('reservation')[0]['attempt']
    with pytest.raises(unit_recovery.SourceIntegrityError, match='metered response'):
        state.rpc('ledger', dict(method='reconcile_attempt', args=[attempt, 0], kwargs={}))
    assert state.ledger.spent() == pytest.approx(.0004)
    def timeout(*args, **kwargs):
        raise requests.Timeout('Synthetic ambiguous outcome')
    monkeypatch.setattr(requests, 'post', timeout)
    other = io.BytesIO()
    Image.new('RGB', (91, 120), 'white').save(other, 'JPEG')
    with pytest.raises(requests.RequestException):
        state.child.read(other.getvalue())
    held = state.ledger.pending_usd()
    attempt = state.ledger.entries('reservation')[-1]['attempt']
    assert held > 0
    with pytest.raises(unit_recovery.SourceIntegrityError, match='cannot be released'):
        state.rpc('ledger', dict(method='release_attempt',
            args=[attempt, 'local_pre_dispatch'], kwargs={}))
    assert state.ledger.pending_usd() == held


def test_native_deadline_excludes_only_parent_bridge_wait(tmp_path, monkeypatch):
    import time
    doc = native_ipc.NativeDocument.__new__(native_ipc.NativeDocument)
    doc.root = tmp_path; doc.closed = False; doc.seq = 0
    doc.resource_lease = None; doc.should_stop = None
    doc.deadline_exceeded = False; doc.operation_deadline = None
    doc.process = SimpleNamespace(stdin=io.BytesIO())
    class SlowBroker:
        def handle(self, request):
            time.sleep(.08)
            return {'ok':True, 'value':0}
    doc.reader_broker = SlowBroker()
    replies = iter([{'seq':1, 'reader_request':1}, {'seq':1, 'ready':True}])
    doc._line = lambda: next(replies)
    native_ipc.write_owned(tmp_path, 'reader-request.json', {'synthetic':True})
    native_ipc.write_owned(tmp_path, 'response.json', {'seq':1,'ok':True,'value':'complete'})
    monkeypatch.setattr(native_ipc, 'operation_timeout', lambda *args: .04)
    assert doc._call('vision_convert', {}) == 'complete'
    assert doc.deadline_exceeded is False
    assert doc.process.stdin.getvalue() == b'1\ncontinue\n'


def test_actual_worker_sample_origin_is_native_and_validated(tmp_path):
    source = tmp_path / 'sample.pdf'
    from tests.fixtures import reflow_pdfs as fixtures
    with pymupdf.open() as doc:
        fixtures.title_page(doc)
        fixtures.prose_page(doc)
        doc.save(source)
    with native_ipc.NativeDocument(source, scratch_root=tmp_path / 'scratch') as native:
        start = native.vision_sample_start(1)
        assert start == 1
        with pytest.raises(ValueError, match='sample size'):
            native.vision_sample_start(True)
    assert list((tmp_path / 'scratch').iterdir()) == []


def test_dispatch_journal_failure_releases_the_proven_unsent_request(tmp_path, monkeypatch):
    state = rig(tmp_path, monkeypatch)
    record = state.ledger.record
    def fail_dispatch(entry, durable=False):
        if entry.get('kind') == 'vision_dispatch':
            raise OSError('Synthetic dispatch journal failure')
        return record(entry, durable=durable)
    monkeypatch.setattr(state.ledger, 'record', fail_dispatch)
    with pytest.raises(OSError, match='Synthetic dispatch journal failure'):
        state.child.read(image())
    assert state.calls == []
    assert Ledger(state.ledger.path, .1).spent() == 0
    assert Ledger(state.ledger.path, .1).pending_usd() == 0
    assert list(state.parent.cache.glob('*.rejected'))


def test_scanned_preparation_reports_before_second_reader_and_propagates_cancel(tmp_path, monkeypatch):
    from cps.services.reflow import model, vision_pipeline
    from tests.unit.test_reflow_app_vision_route import _source
    from tests.unit.test_reflow_task import _PixelSession
    source = tmp_path / 'source.pdf'
    _source(source, scanned=True, pages=2)
    ledger = Ledger(tmp_path / 'job.jsonl', .1)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', ledger,
                                  confidence_protocol='tokens', reuse_legacy=True)
    provider = _PixelSession()
    monkeypatch.setattr(requests, 'post', provider.post)
    calls = []; events = []; completed = []
    original = reader.read_unit
    def read_unit(*args, **kwargs):
        calls.append(len(calls))
        if len(calls) == 2:
            assert ('source_reading', 1, 2) in events
        return original(*args, **kwargs)
    monkeypatch.setattr(reader, 'read_unit', read_unit)
    def preparation(event):
        events.append((event.stage, event.page, event.pages))
        if event.stage == 'source_reading' and event.page == 1:
            assert len(calls) == 1
            assert completed == [], 'A prepared reading is not yet a compiled page'
            raise model.AttemptCancelled('Synthetic stop after one prepared source')
    output = tmp_path / 'candidate.epub'
    with pymupdf.open(source) as document:
        with pytest.raises(model.AttemptCancelled, match='one prepared source'):
            vision_pipeline.convert(document, str(output), reader,
                preparation_progress=preparation, progress=lambda *args: completed.append(args))
    assert events == [('source_reading', 0, 2), ('source_reading', 1, 2)]
    assert len(calls) == len(provider.calls) == 1
    assert ledger.spent() == pytest.approx(provider.cost) and ledger.pending_usd() == 0
    assert not output.exists()


def test_real_worker_preparation_cancel_keeps_second_scan_unsent(tmp_path, monkeypatch):
    from cps.services.reflow import model
    from tests.unit.test_reflow_app_vision_route import _source
    from tests.unit.test_reflow_task import _PixelSession
    source = tmp_path / 'source.pdf'
    _source(source, scanned=True, pages=2)
    state = tmp_path / 'state'
    ledger = Ledger(state / 'job.jsonl', .1)
    reader = vision_reading.Reader('inert-key', state / 'cache', ledger,
                                  confidence_protocol='tokens', reuse_legacy=True)
    provider = _PixelSession()
    monkeypatch.setattr(requests, 'post', provider.post)
    stopped = []; events = []
    def preparation(event):
        events.append(event)
        if event.stage == 'source_reading' and event.page == 1:
            assert len(provider.calls) == 1
            assert not any(e.stage == 'vision' for e in events)
            stopped.append(True)
    output = tmp_path / 'candidate.epub'
    with native_ipc.NativeDocument(source, scratch_root=state / 'native-scratch',
            cache_root=state, ledger=ledger, progress=preparation,
            should_stop=lambda: bool(stopped)) as document:
        quote = document.vision_quote()
        process = document.process
        with pytest.raises(model.AttemptCancelled):
            document.convert_vision(output, reader, quote=quote)
    reading_events = [e for e in events if e.stage == 'source_reading']
    assert [(e.page, e.pages) for e in reading_events] == [(0, 2), (1, 2)]
    assert reading_events[-1].message == 'Reading scanned source pages'
    assert len(provider.calls) == 1
    assert ledger.spent() == pytest.approx(provider.cost) and ledger.pending_usd() == 0
    assert process.poll() is not None
    assert not output.exists()
    assert list((state / 'native-scratch').iterdir()) == []
