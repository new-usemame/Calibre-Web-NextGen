"""Synthetic scans exercise reserve/dispatch crash boundaries in a real worker."""
import os
import base64
import json
import signal
import shutil
from pathlib import Path
import pytest
import pymupdf
import requests
from cps.services.reflow import native_ipc, vision_bridge, vision_reading, model
from cps.services.reflow.ledger import Ledger
from cps.services.reflow.shared_budget import SharedLedger, Store

pytestmark = [pytest.mark.unit, pytest.mark.skipif(not shutil.which('tesseract'), reason='Tesseract is required for this scan')]


def scan(path):
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=500)
        page.insert_textbox(pymupdf.Rect(30,40,370,460),
            'A clear river runs beside the garden.\nThe window opens toward the road.\n'
            'Each printed sentence stays on the page.\nThe reader can inspect this simple record.\n', fontsize=13)
        png=page.get_pixmap(matrix=pymupdf.Matrix(2,2)).tobytes('png')
    with pymupdf.open() as doc:
        doc.new_page(width=400,height=500).insert_image(pymupdf.Rect(0,0,400,500),stream=png)
        doc.save(path)


@pytest.mark.parametrize('boundary', ['after_reserve', 'store_only', 'after_pending', 'after_dispatch'])
def test_real_child_crash_releases_only_absence_of_dispatch(tmp_path, monkeypatch, boundary):
    source=tmp_path/'source.pdf';scan(source)
    root=tmp_path/'state';store=Store(root,lambda:5)
    led=SharedLedger(root/'jobs'/'5'/'job.jsonl',1,job_id='job',store=store,book_id=5,user_id=7)
    reader=vision_reading.Reader('inert-key',root/'cache',led,confidence_protocol='tokens')
    calls=[]
    observed_images=[]
    def post(*a,**k):
        calls.append(True)
        raise requests.Timeout('Synthetic ambiguous outcome')
    monkeypatch.setattr(requests,'post',post)
    real_handle=vision_bridge.ParentBroker.handle
    real_record=led.record
    doc=native_ipc.NativeDocument(source,scratch_root=root/'native-scratch',cache_root=root)
    def kill():
        os.kill(doc.process.pid,signal.SIGKILL)
        doc.process.wait(timeout=10)
    def handle(self, request):
        if request.get('operation')=='post':
            encoded=request['arguments']['payload']['messages'][0]['content'][1]['image_url']['url']
            observed_images.append(base64.b64decode(encoded.split(',',1)[1]))
        if boundary=='after_pending' and request.get('operation')=='post':
            assert list(reader.cache.glob('*.pending'))
            kill()
            raise native_ipc.ChildExited('Synthetic child exit before post')
        reply=real_handle(self,request)
        if boundary=='after_reserve' and request.get('operation')=='ledger' and request['arguments'].get('method')=='reserve_attempt':
            assert reply['ok'] and led.pending_usd()>0
            kill()
        return reply
    def record(entry, *a, **kw):
        if boundary=='store_only' and entry.get('kind')=='reservation' and entry.get('event')=='pending':
            kill()
            # A BaseException models termination between the store commit and
            # the job mirror; handled I/O errors release in reserve_attempt.
            raise KeyboardInterrupt('Synthetic reserve/mirror interruption')
        result=real_record(entry,*a,**kw)
        if boundary=='after_dispatch' and entry.get('kind')=='vision_dispatch':kill()
        return result
    monkeypatch.setattr(vision_bridge.ParentBroker,'handle',handle)
    monkeypatch.setattr(led,'record',record)
    quote=doc.vision_quote()
    assert quote['eligible_pages']==1
    try:
        with pytest.raises((native_ipc.ChildExited, BrokenPipeError, KeyboardInterrupt, requests.Timeout)):
            doc.convert_vision(tmp_path/'out.epub',reader,quote=quote,job_id='job')
    finally:
        monkeypatch.setattr(led,'record',real_record)
        doc.close()
    assert doc.process.poll() is not None
    assert doc.shutdown_evidence['direct_child_reaped']
    current=Ledger(led.path,1)
    markers=[e for e in current.entries() if e.get('kind')=='vision_dispatch']
    if boundary=='after_dispatch':
        assert len(markers)==1 and current.pending_usd()>0
        assert calls==[True]
        with pytest.raises(model.UncertainBilling):
            reader.read(observed_images[0])
        assert calls==[True]  # no repeat transport for the marked attempt
    else:
        assert markers==[] and current.pending_usd()==0 and current.spent()==0
        assert calls==[] and store.status()['remaining_usd']==5
        assert list(reader.cache.glob('*.pending'))==[]
        assert reader.financial_dispatch_allowed()
        assert [e['event'] for e in current.entries('reservation')]==['pending','released']

    if boundary=='after_pending':
        # A new authorized attempt can read the exact previously-unsent image;
        # reusing its new cache afterwards must not spend a second time.
        from tests.unit.test_reflow_vision_bridge import reading, response
        answer=reading('A synthetic reading of the clear printed page.')
        block=answer['blocks'][0]
        block['tokens']=[dict(text=word,low_confidence=False) for word in block.pop('text').split()]
        block.pop('low_confidence')
        def success(*a,**kw):
            calls.append(True)
            result=requests.Response();result.status_code=200
            result._content=json.dumps(response(answer).json()).encode()
            return result
        monkeypatch.setattr(requests,'post',success)
        retry_ledger=SharedLedger(root/'jobs'/'5'/'retry.jsonl',1,job_id='retry',store=store,book_id=5,user_id=7)
        retry=vision_reading.Reader('inert-key',reader.cache,retry_ledger,confidence_protocol='tokens')
        assert retry.read(observed_images[0])['new_cost_usd']==.0004
        assert retry.read(observed_images[0])['new_cost_usd']==0
        assert calls==[True]
        old_attempt=current.entries('reservation')[0]['attempt']
        new_attempt=retry_ledger.entries('reservation')[0]['attempt']
        assert old_attempt!=new_attempt
        assert retry_ledger.spent()==.0004 and retry_ledger.pending_usd()==0
        assert Ledger(led.path,1).spent()==Ledger(led.path,1).pending_usd()==0
