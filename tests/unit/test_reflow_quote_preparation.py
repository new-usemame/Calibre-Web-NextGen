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


def _blocking_work(release, entered=None):
    """Work that runs until the test releases it or the store stops it."""
    def work(path,options,progress,stop):
        import time
        if entered is not None:entered.set()
        deadline=time.monotonic()+5
        while not release.is_set() and not stop() and time.monotonic()<deadline:time.sleep(.01)
        raise model.AttemptCancelled('stopped' if stop() else 'end test')
    return work


def test_parallel_preparations_are_bounded_and_same_owner_request_reuses_work(tmp_path):
    """One preparation runs at a time and the same request rejoins it. Nobody else
    is turned away while it runs: they wait in line (below)."""
    from cps.services.reflow.quote_preparation import PreparationStore
    source=tmp_path/'source.pdf';source.write_bytes(b'source');release=threading.Event()
    entered=threading.Event()
    store=PreparationStore(tmp_path/'cache',workers=1)
    try:
        first=store.start(7,5,str(source),{'mode':'off'},_blocking_work(release,entered))
        assert entered.wait(2)
        assert store.start(7,5,str(source),{'mode':'off'},_blocking_work(release))['preparation_id']==first['preparation_id']
        assert store.get(7,5,first['preparation_id'])['status']=='preparing'
    finally:release.set();store.close()


def test_a_second_person_waits_in_line_and_one_person_cannot_fill_it(tmp_path):
    """N2(b) of the 7daffa5 retest: one edit user preparing a huge PDF turned every
    other user away with 503s for as long as it ran. Now another person's request
    waits in line (and is told how many are ahead), a person has one preparation
    in flight at a time, and the line itself is bounded. Breaks if a second person
    is refused while one preparation runs, or if one person can queue two."""
    from cps.services.reflow.quote_preparation import PreparationStore,PreparationBusy
    source=tmp_path/'source.pdf';source.write_bytes(b'source');other=tmp_path/'other.pdf';other.write_bytes(b'other')
    release=threading.Event();entered=threading.Event()
    store=PreparationStore(tmp_path/'cache',workers=1,max_waiting=2)
    try:
        first=store.start(7,5,str(source),{'mode':'off'},_blocking_work(release,entered))
        assert entered.wait(2)
        second=store.start(8,5,str(source),{'mode':'off'},_blocking_work(release))
        assert second['status']=='waiting' and second['ahead']==1
        third=store.start(9,6,str(other),{'mode':'off'},_blocking_work(release))
        assert third['status']=='waiting' and third['ahead']==2
        with pytest.raises(PreparationBusy) as mine:
            store.start(7,6,str(other),{'mode':'off'},_blocking_work(release))
        assert mine.value.reason=='owner_busy'
        with pytest.raises(PreparationBusy) as full:
            store.start(10,6,str(other),{'mode':'off'},_blocking_work(release))
        assert full.value.reason=='queue_full'
        # The second person moves up when the first one stops theirs.
        store.cancel(7,5,first['preparation_id'])
        deadline=__import__('time').monotonic()+3
        while store.get(8,5,second['preparation_id'])['status']=='waiting' and __import__('time').monotonic()<deadline:
            __import__('time').sleep(.02)
        assert store.get(8,5,second['preparation_id'])['status']=='preparing'
        assert store.get(9,6,third['preparation_id'])['ahead']==1
    finally:release.set();store.close()


def test_a_preparation_that_runs_out_of_time_says_so(tmp_path):
    """A preparation that hits its wall-clock limit is failed with its own reason
    (the reader is told it can continue, since recognised pages are kept), not
    the generic failure."""
    import time
    from cps.services.reflow.quote_preparation import PreparationStore,PreparationTimeout
    source=tmp_path/'source.pdf';source.write_bytes(b'source')
    def work(path,options,progress,stop):raise PreparationTimeout('out of time')
    store=PreparationStore(tmp_path/'cache',workers=1)
    try:
        job=store.start(7,5,str(source),{'mode':'off'},work)
        deadline=time.monotonic()+2
        while job['status'] not in ('failed','ready') and time.monotonic()<deadline:
            time.sleep(.01);job=store.get(7,5,job['preparation_id'])
    finally:store.close()
    assert job['status']=='failed' and job['error']=='source_preparation_timeout'
    assert job['timeout_minutes']>=1


_STUCK_WORKER = """
import os, subprocess, sys, time
request = sys.stdin.read()
grandchild = subprocess.Popen(['sleep', '300'])
with open(os.environ['STUCK_PIDS'], 'w') as handle:
    handle.write('%d %d' % (os.getpid(), grandchild.pid))
while True:
    time.sleep(0.05)       # ignores the cancel file: a wedged renderer
"""


def _alive(pid):
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_preparation_past_its_time_limit_is_stopped_with_everything_it_started(tmp_path,monkeypatch):
    """N2(b): the preparation process had no wall-clock limit, and cleanup reached
    the child but not what the child started (Tesseract). A wedged preparation
    now stops at its limit -- cooperative stop first, then the whole process
    group is killed -- every wait is bounded, and its scratch space is removed.
    Breaks if the call outlives its limit or leaves any process behind."""
    import os,signal,time
    from cps.services.reflow import quote_preparation as qp
    worker=tmp_path/'stuck_worker.py';worker.write_text(_STUCK_WORKER)
    pids=tmp_path/'pids'
    monkeypatch.setattr(qp,'WORKER',worker)
    real_environment=qp._child_environment
    monkeypatch.setattr(qp,'_child_environment',lambda scratch:dict(real_environment(scratch),STUCK_PIDS=str(pids)))
    source=tmp_path/'source.pdf';source.write_bytes(b'%PDF-1.4')
    outcome={}
    def call():
        try:
            qp.measure_isolated(source,{'source_recovery':'off','ocr_language':'eng'},None,None,tmp_path,
                                timeout=1.0,grace=0.5)
        except Exception as exc:          # noqa: BLE001 - the test inspects it
            outcome['error']=exc
    runner=threading.Thread(target=call,daemon=True);started=time.monotonic();runner.start()
    runner.join(15)
    try:
        assert not runner.is_alive(),'the preparation outlived its limit'
        assert isinstance(outcome.get('error'),qp.PreparationTimeout),outcome
        assert time.monotonic()-started<10
        child,grandchild=(int(x) for x in pids.read_text().split())
        deadline=time.monotonic()+5
        while (_alive(child) or _alive(grandchild)) and time.monotonic()<deadline:time.sleep(.05)
        assert not _alive(child) and not _alive(grandchild)
        assert list((tmp_path/'quote-scratch').iterdir())==[]
    finally:
        if pids.exists():
            for pid in (int(x) for x in pids.read_text().split()):
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass


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


def test_stopping_a_wedged_preparation_does_not_wait_for_its_time_limit(tmp_path,monkeypatch):
    """Stop asks the process to stop, then gives it a grace period -- not the rest
    of its 30 minutes. A renderer wedged in one page is killed with everything it
    started once the grace runs out."""
    import os,signal,time
    from cps.services.reflow import model as model_mod
    from cps.services.reflow import quote_preparation as qp
    worker=tmp_path/'stuck_worker.py';worker.write_text(_STUCK_WORKER)
    pids=tmp_path/'pids'
    monkeypatch.setattr(qp,'WORKER',worker)
    real_environment=qp._child_environment
    monkeypatch.setattr(qp,'_child_environment',lambda scratch:dict(real_environment(scratch),STUCK_PIDS=str(pids)))
    source=tmp_path/'source.pdf';source.write_bytes(b'%PDF-1.4')
    outcome={}
    def call():
        try:
            qp.measure_isolated(source,{'source_recovery':'off','ocr_language':'eng'},None,
                                lambda:pids.exists(),tmp_path,timeout=600,grace=0.5)
        except Exception as exc:          # noqa: BLE001 - the test inspects it
            outcome['error']=exc
    runner=threading.Thread(target=call,daemon=True);runner.start();runner.join(15)
    try:
        assert not runner.is_alive(),'stop waited for the time limit'
        assert isinstance(outcome.get('error'),model_mod.AttemptCancelled),outcome
        child,grandchild=(int(x) for x in pids.read_text().split())
        deadline=time.monotonic()+5
        while (_alive(child) or _alive(grandchild)) and time.monotonic()<deadline:time.sleep(.05)
        assert not _alive(child) and not _alive(grandchild)
    finally:
        if pids.exists():
            for pid in (int(x) for x in pids.read_text().split()):
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass


def test_a_preparation_whose_web_process_is_gone_stops_itself(tmp_path):
    """The web process enforces the time limit and reads the result. A preparation
    orphaned by a crashed web process would otherwise run to the end of a
    2,000-page book for nobody; it now stops at its next check and publishes
    nothing."""
    import json,subprocess,sys,time
    from tests.fixtures import reflow_pdfs as F
    from cps.services.reflow import quote_preparation as qp
    source=tmp_path/'source.pdf';doc=F.new_doc()
    for _ in range(40):F.prose_page(doc)
    doc.save(source);doc.close()
    root=tmp_path/'run';root.mkdir()
    request=dict(source=str(source),options={'source_recovery':'off','ocr_language':'eng'},
                 cache_root=str(tmp_path),control=str(root/'cancel'),progress=str(root/'progress.json'),
                 output=str(root/'quote.json'))
    # Stands in for the web process: it names itself in the request, as
    # measure_isolated does, starts the preparation and dies at once.
    launcher=("import json,os,subprocess,sys\n"
              "request=json.loads(sys.stdin.read());request['parent']=os.getpid()\n"
              "p=subprocess.Popen([sys.executable,'-I',%r],stdin=subprocess.PIPE,"
              "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)\n"
              "p.stdin.write(json.dumps(request).encode());p.stdin.close();print(p.pid)\n") % str(qp.WORKER)
    started=subprocess.run([sys.executable,'-c',launcher],input=json.dumps(request).encode(),
                           capture_output=True,check=True,timeout=30)
    worker=int(started.stdout.decode().strip())
    deadline=time.monotonic()+30
    while _alive(worker) and time.monotonic()<deadline:time.sleep(.1)
    try:
        assert not _alive(worker),'the orphaned preparation kept running'
        assert not (root/'quote.json').exists()
    finally:
        if _alive(worker):
            import os,signal
            os.kill(worker,signal.SIGKILL)
