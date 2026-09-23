"""Local preparation is bounded, owner-scoped, cancellable and never a paid retry."""
import threading
import pytest
from cps.services.reflow import model

pytestmark=pytest.mark.unit


def test_preparation_returns_without_waiting_and_cancel_is_owner_scoped(tmp_path):
    from cps.services.reflow.quote_preparation import PreparationStore
    source=tmp_path/'source.pdf';source.write_bytes(b'source')
    entered=threading.Event();release=threading.Event();stopped=threading.Event()
    def work(path,options,progress,stop):
        entered.set();release.wait(3)
        if stop():stopped.set();raise model.AttemptCancelled('stopped')
        raise AssertionError('cancel was not observed')
    store=PreparationStore(tmp_path/'cache',workers=1)
    try:
        job=store.start(7,5,str(source),{'mode':'off'},work)
        assert job['status'] in ('waiting','preparing') and entered.wait(1)
        with pytest.raises(KeyError):store.cancel(8,5,job['preparation_id'])
        assert store.cancel(7,5,job['preparation_id'])['status']=='cancelling'
        release.set();assert stopped.wait(1)
    finally:release.set();store.close()
    assert store.get(7,5,job['preparation_id'])['status']=='cancelled'
    assert not list((tmp_path/'cache').glob('*.json'))


def test_parallel_preparations_are_bounded_and_same_owner_request_reuses_work(tmp_path):
    from cps.services.reflow.quote_preparation import PreparationStore,PreparationBusy
    source=tmp_path/'source.pdf';source.write_bytes(b'source');release=threading.Event()
    def work(path,options,progress,stop):
        release.wait(3);raise model.AttemptCancelled('end test')
    store=PreparationStore(tmp_path/'cache',workers=1)
    try:
        first=store.start(7,5,str(source),{'mode':'off'},work)
        assert store.start(7,5,str(source),{'mode':'off'},work)['preparation_id']==first['preparation_id']
        with pytest.raises(PreparationBusy):store.start(8,5,str(source),{'mode':'off'},work)
    finally:release.set();store.close()


def test_actual_isolated_source_preparation_produces_reusable_owner_scoped_quote(tmp_path):
    import time
    from tests.fixtures import reflow_pdfs as F
    from cps.services.reflow.quote_preparation import PreparationStore,measure_isolated
    source=tmp_path/'source.pdf';doc=F.new_doc();F.chapter_opening_page(doc,'A complete source title');F.prose_page(doc)
    doc.save(source);doc.close();options={'source_recovery':'off','ocr_language':'eng'}
    cache=tmp_path/'cache';store=PreparationStore(cache)
    events=[]
    def work(path,opts,progress,stop):
        def observed(event):events.append(event.stage);progress(event)
        return measure_isolated(path,opts,observed,stop,tmp_path)
    try:
        job=store.start(7,5,str(source),options,work)
        # This is a bounded test wait, not a production operation deadline.
        deadline=time.monotonic()+20
        while job['status'] not in ('ready','failed') and time.monotonic()<deadline:
            time.sleep(.02);job=store.get(7,5,job['preparation_id'])
        assert job['status']=='ready',job
        quote=store.ready(7,5,job['preparation_id'],str(source),options)
        assert quote['source_context_pages']==2 and quote['confirmed_usd']==quote['held_usd']==0
        with pytest.raises(KeyError):store.ready(8,5,job['preparation_id'],str(source),options)
        with pytest.raises(KeyError):store.ready(7,5,job['preparation_id'],str(source),dict(options,source_recovery='auto'))
    finally:store.close()
    # A new application lifetime uses completed data, not another paid request or
    # another owner's job handle. Source modification invalidates the handle.
    other=PreparationStore(cache)
    try:
        def forbidden(*args):raise AssertionError('completed source quote must reuse cache')
        reused=other.start(8,5,str(source),options,forbidden)
        assert reused['status']=='ready' and reused['quote']==quote
        assert reused['preparation_id']!=job['preparation_id']
        source.write_bytes(source.read_bytes()+b'\n% changed source')
        with pytest.raises(KeyError):other.ready(8,5,reused['preparation_id'],str(source),options)
    finally:other.close()


def test_actual_isolated_preparation_cancels_before_publishing_quote(tmp_path):
    from tests.fixtures import reflow_pdfs as F
    from cps.services.reflow.quote_preparation import measure_isolated
    source=tmp_path/'source.pdf';doc=F.new_doc()
    for _ in range(12):F.prose_page(doc)
    doc.save(source);doc.close()
    with pytest.raises(model.AttemptCancelled):
        measure_isolated(source,{'source_recovery':'off','ocr_language':'eng'},None,lambda:True,tmp_path)
    assert list((tmp_path/'quote-scratch').iterdir())==[]


def test_recovery_runtime_change_invalidates_ready_quote_without_reusing_old_context(tmp_path,monkeypatch):
    import json,hashlib,time
    from cps.services.reflow import extract,ocr,structural_quote
    from cps.services.reflow.quote_preparation import PreparationStore
    source=tmp_path/'source.pdf';source.write_bytes(b'source');version=['engine-1']
    monkeypatch.setattr(ocr,'_engine',lambda language:('/inert/tesseract',version[0],'language-data-1'))
    options={'source_recovery':'auto','ocr_language':'eng'}
    def work(*args):
        quote={'version':structural_quote.VERSION,'source_sha256':extract.document_fingerprint(str(source))}
        quote['identity']=hashlib.sha256(json.dumps(quote,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        return quote
    store=PreparationStore(tmp_path/'cache')
    try:
        job=store.start(7,5,str(source),options,work)
        deadline=time.monotonic()+2
        while job['status']!='ready' and time.monotonic()<deadline:
            time.sleep(.01);job=store.get(7,5,job['preparation_id'])
        assert job['status']=='ready'
        version[0]='engine-2'
        with pytest.raises(KeyError):store.ready(7,5,job['preparation_id'],str(source),options)
    finally:store.close()


def test_the_preparation_process_inherits_no_secrets_and_no_injected_imports(tmp_path,monkeypatch):
    """The preparation child reads an untrusted PDF. It needs no provider key or
    database secret, and it must not import code from the web process's
    environment: the service is started with ``-P`` and ``PYTHONNOUSERSITE=1``
    precisely so /config and PYTHONPATH cannot place modules ahead of the app.
    OCR already scrubs its environment (ocr._env); this child inherited
    everything. Breaks if the child is started with the parent environment again,
    or with a stripped one that drops the isolation flags (the sitecustomize
    marker below is then imported)."""
    import os,subprocess
    from tests.fixtures import reflow_pdfs as F
    from cps.services.reflow import quote_preparation
    source=tmp_path/'source.pdf';doc=F.new_doc();F.chapter_opening_page(doc,'A complete source title');F.prose_page(doc)
    doc.save(source);doc.close()
    injected=tmp_path/'injected';injected.mkdir();marker=tmp_path/'imported-from-the-web-environment'
    (injected/'sitecustomize.py').write_text('open(%r,"w").write("x")\n'%str(marker))
    monkeypatch.setenv('PYTHONPATH',str(injected))
    monkeypatch.setenv('OPENROUTER_API_KEY','sk-or-v1-NOT-FOR-THE-CHILD')
    monkeypatch.setenv('CWNG_TEST_DB_PASSWORD','not-for-the-child')
    started=[];real=subprocess.Popen
    def spy(*args,**kwargs):
        started.append(dict(os.environ) if kwargs.get('env') is None else dict(kwargs['env']))
        return real(*args,**kwargs)
    monkeypatch.setattr(quote_preparation.subprocess,'Popen',spy)
    quote=quote_preparation.measure_isolated(source,{'source_recovery':'off','ocr_language':'eng'},None,None,tmp_path)
    assert quote['source_context_pages']==2          # the stripped child still does the work
    assert started,'no preparation process was started'
    environment=started[0]
    assert 'OPENROUTER_API_KEY' not in environment and 'CWNG_TEST_DB_PASSWORD' not in environment
    assert not any('sk-or-' in value for value in environment.values())
    assert not marker.exists(),'the child imported a module from the inherited PYTHONPATH'
