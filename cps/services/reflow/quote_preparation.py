"""Bounded local preparation jobs; no provider sessions or billing reservations."""
import copy
import hashlib
import itertools
import json
import os
import signal
import threading
import time
import uuid
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import extract,model,structural_quote,typed_model,prompts,ocr,build_epub,enriched_source,heading_units


class PreparationBusy(Exception):
    """No room for this preparation now. ``reason`` is ``owner_busy`` (this person
    already has one in flight) or ``queue_full`` (the line is full)."""
    def __init__(self,reason='queue_full'):
        super().__init__(reason);self.reason=reason


class PreparationTimeout(Exception):
    """A preparation ran past its wall-clock limit and was stopped."""


#: The longest one preparation may hold the preparation slot (N2(b) of the
#: 7daffa5 retest: the process had no wall-clock limit, so one huge PDF held the
#: only slot for as long as it took). Recognised pages are cached as they finish,
#: so a preparation stopped here continues from where it stopped when it is
#: started again: a turn, not a loss.
PREPARATION_TIMEOUT=30*60
#: How long a stopped preparation gets to stop on its own -- OCR ends its own
#: Tesseract -- before its whole process group is killed.
STOP_GRACE=10.0
#: How many preparations may wait for the slot behind the one that is running.
MAX_WAITING=4
ACTIVE=('waiting','preparing','cancelling')

WORKER=Path(__file__).with_name('quote_worker.py')


class PreparationStore:
    """Preparations, one person at a time per slot, in the order they were asked for.

    ``workers`` preparations run at once (one: each is a CPU-heavy process). The
    rest wait in line, first come first served, and each is told how many are
    ahead of it. One person has at most one preparation in flight, so nobody can
    fill the line alone, and the line holds ``max_waiting``; past that a request is
    refused as busy rather than queued behind hours of other people's work.
    """
    def __init__(self,directory,workers=1,max_waiting=MAX_WAITING):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.workers=workers;self.pool=ThreadPoolExecutor(max_workers=workers,thread_name_prefix='reflow-source-quote')
        self.lock=threading.Lock();self.jobs={};self.max_waiting=max_waiting;self._order=itertools.count()

    def _key(self,source,options):
        fingerprint=extract.document_fingerprint(source)
        recovery_identity=None
        if options.get('source_recovery','auto')!='off':
            try:
                _executable,version,data=ocr._engine(options.get('ocr_language','eng'))
                recovery_identity=[ocr.ADAPTER_VERSION,version,data]
            except ocr.OCRUnavailable:recovery_identity=[ocr.ADAPTER_VERSION,'unavailable']
        context=[fingerprint,options,structural_quote.VERSION,typed_model.SOURCE_REVISION,
                 build_epub.CONVERTER_VERSION,enriched_source.VERSION,heading_units.VERSION,recovery_identity,
                 typed_model.ROUTE_VERSION,prompts.OPERATION_PROMPT_VERSION,prompts.VERIFICATION_PROMPT_VERSION,
                 [(s,m.model_id,m.prompt_usd_per_mtok,m.completion_usd_per_mtok) for s,m in typed_model.STAGES.items()]]
        return hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest(),fingerprint

    @staticmethod
    def _valid_quote(quote,fingerprint):
        if not isinstance(quote,dict) or quote.get('source_sha256')!=fingerprint or quote.get('version')!=structural_quote.VERSION:
            raise ValueError('prepared quote does not match current source')
        data=dict(quote);identity=data.pop('identity',None)
        actual=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        if identity!=actual:raise ValueError('prepared quote integrity changed')

    def _ahead(self,job):
        """How many preparations will run before this waiting one (lock held)."""
        if job['status']!='waiting':return 0
        return sum(1 for other in self.jobs.values()
                   if other is not job and other['status'] in ACTIVE and other['order']<job['order'])

    def _public(self,job):
        result={k:copy.deepcopy(job[k]) for k in ('preparation_id','status','progress')}
        result['ahead']=self._ahead(job);result['timeout_minutes']=PREPARATION_TIMEOUT//60
        if job['status']=='ready':result['quote']=copy.deepcopy(job['quote'])
        if job.get('error'):result['error']=job['error']
        return result

    def start(self,owner,book_id,source,options,work):
        key,fingerprint=self._key(source,options);now=time.monotonic()
        # Read a completed quote before taking the lock: the lock guards the job
        # table only, and a request thread waiting on it must never wait on disk.
        try:
            cached=json.loads((self.directory/(key+'.json')).read_text())
            self._valid_quote(cached,fingerprint)
        except (OSError,ValueError,TypeError):cached=None
        with self.lock:
            self.jobs={i:j for i,j in self.jobs.items() if j['status'] in ('waiting','preparing','cancelling') or now-j['touched']<600}
            for job in self.jobs.values():
                if job['owner']==owner and job['book_id']==book_id and job['key']==key and job['status'] in ('waiting','preparing','ready'):
                    job['touched']=now;return self._public(job)
            # Bound retained opaque handles independently of worker count.
            terminal=sorted((j for j in self.jobs.values() if j['status'] not in ACTIVE),key=lambda j:j['touched'])
            for old in terminal[:max(0,len(self.jobs)-255)]:self.jobs.pop(old['preparation_id'],None)
            if cached is None:
                # A completed quote costs nothing and is never refused. New work
                # waits its turn: one in flight per person, and a bounded line.
                if any(j['owner']==owner and j['status'] in ACTIVE for j in self.jobs.values()):
                    raise PreparationBusy('owner_busy')
                active=sum(j['status'] in ACTIVE for j in self.jobs.values())
                if active-self.workers>=self.max_waiting:raise PreparationBusy('queue_full')
            job=dict(preparation_id=uuid.uuid4().hex,owner=owner,book_id=book_id,key=key,source=source,
                     fingerprint=fingerprint,options=copy.deepcopy(options),status='waiting',progress={},
                     stop=threading.Event(),touched=now,order=next(self._order))
            if cached is not None:job.update(status='ready',quote=cached)
            self.jobs[job['preparation_id']]=job
            if job['status']!='ready':job['future']=self.pool.submit(self._run,job,work)
            return self._public(job)

    def _run(self,job,work):
        def progress(event):
            with self.lock:
                job['progress']={'stage':str(event.stage),'page':int(event.page),'pages':int(event.pages)}
        try:
            with self.lock:job['status']='preparing'
            if job['stop'].is_set():raise model.AttemptCancelled('local preparation cancelled')
            quote=work(job['source'],job['options'],progress,job['stop'].is_set)
            if job['stop'].is_set():raise model.AttemptCancelled('local preparation cancelled')
            self._valid_quote(quote,job['fingerprint'])
            if extract.document_fingerprint(job['source'])!=job['fingerprint']:
                raise ValueError('source changed during preparation')
            temporary=self.directory/(job['key']+'.'+job['preparation_id']+'.tmp')
            try:
                temporary.write_text(json.dumps(quote,ensure_ascii=False,allow_nan=False))
                os.replace(temporary,self.directory/(job['key']+'.json'))
            finally:
                if temporary.exists():temporary.unlink()
            with self.lock:job.update(status='ready',quote=quote,touched=time.monotonic())
        except model.AttemptCancelled:
            with self.lock:job.update(status='cancelled',touched=time.monotonic())
        except PreparationTimeout:
            with self.lock:job.update(status='failed',error='source_preparation_timeout',touched=time.monotonic())
        except Exception:
            # No source paths, raw source text or another owner's identity in API errors.
            with self.lock:job.update(status='failed',error='source_preparation_failed',touched=time.monotonic())

    def get(self,owner,book_id,identifier):
        with self.lock:
            job=self.jobs.get(identifier)
            if job is None or job['owner']!=owner or job['book_id']!=book_id:raise KeyError(identifier)
            job['touched']=time.monotonic();return self._public(job)

    def cancel(self,owner,book_id,identifier):
        with self.lock:
            job=self.jobs.get(identifier)
            if job is None or job['owner']!=owner or job['book_id']!=book_id:raise KeyError(identifier)
            if job['status'] in ('waiting','preparing','cancelling'):
                job['stop'].set();job['status']='cancelling'
                if job['future'].cancel():job['status']='cancelled'
            return self._public(job)

    def ready(self,owner,book_id,identifier,source,options):
        key,fingerprint=self._key(source,options)
        with self.lock:
            job=self.jobs.get(identifier)
            if job is None or job['owner']!=owner or job['book_id']!=book_id or job['key']!=key or job['status']!='ready':
                raise KeyError(identifier)
            self._valid_quote(job['quote'],fingerprint)
            return copy.deepcopy(job['quote'])

    def close(self):
        with self.lock:
            for job in self.jobs.values():job['stop'].set()
        self.pool.shutdown(wait=True,cancel_futures=True)


def _child_environment(scratch):
    """What the preparation child may see of the web process: nothing secret.

    The child parses an untrusted PDF and runs OCR. It needs a PATH (to find
    Tesseract), the locale and the OCR data location -- the allowlist the OCR
    adapter already uses (``ocr._env``) -- and nothing else: no provider key, no
    database or mail secret. Its temporary files stay in the per-preparation
    scratch directory, which is removed with it. ``PYTHONNOUSERSITE`` restates what
    ``-I`` below already enforces; the service sets it for the parent too.
    """
    environment=ocr._env()
    environment['TMPDIR']=str(scratch)
    environment['PYTHONNOUSERSITE']='1'
    return environment


def _reap(process,control,grace):
    """Stop the preparation process and everything it started; every wait is bounded.

    The process leads its own session and process group (``start_new_session``),
    so one ``killpg`` also reaches the Tesseract it runs. A running process is
    first asked to stop through its control file and given ``grace`` seconds --
    OCR then ends its own child and removes its scratch -- and the group is
    signalled even after a normal exit, because a helper can outlive its parent.
    """
    if process.poll() is None:
        try:control.touch(exist_ok=True)
        except OSError:pass
        try:process.wait(timeout=grace)
        except subprocess.TimeoutExpired:pass
    try:os.killpg(process.pid,signal.SIGKILL)
    except (ProcessLookupError,PermissionError):pass
    try:process.wait(timeout=grace)
    except subprocess.TimeoutExpired:pass       # unkillable (in-kernel I/O): nothing more to do


def measure_isolated(source,options,progress,should_stop,cache_root,timeout=None,grace=None):
    """One owned renderer process; only bounded progress and quote JSON cross back.

    The process has no client/session/key arguments, and no secrets in its
    environment (``_child_environment``). It runs in Python's isolated mode (``-I``:
    no PYTHONPATH, no user site-packages, no script directory on sys.path), the
    same import boundary the service itself starts with (``-P`` and
    ``PYTHONNOUSERSITE=1`` in its s6 run script). A cancellation file is polled
    by the existing extraction, Recovery and candidate loops, including OCR's
    child-process cancellation. No partial quote is made ready or billed.

    It is bounded in time: past ``timeout`` seconds (``PREPARATION_TIMEOUT``) it
    raises :class:`PreparationTimeout`, and a cancelled preparation that has not
    stopped on its own within ``grace`` seconds is stopped. Either way the process
    and everything it started are killed (``_reap``) and its scratch removed.
    """
    timeout=PREPARATION_TIMEOUT if timeout is None else float(timeout)
    grace=STOP_GRACE if grace is None else float(grace)
    scratch=Path(cache_root)/'quote-scratch';scratch.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='prepare-',dir=scratch) as directory:
        root=Path(directory);control=root/'cancel';status=root/'progress.json';output=root/'quote.json'
        request=dict(source=str(source),options=options,cache_root=str(cache_root),
                     control=str(control),progress=str(status),output=str(output),parent=os.getpid())
        with open(root/'stderr.log','wb') as errors:
            process=subprocess.Popen([sys.executable,'-I',str(WORKER)],
                stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=errors,
                env=_child_environment(root),cwd=str(root),start_new_session=True)
            deadline=time.monotonic()+timeout;stopping=None
            try:
                process.stdin.write(json.dumps(request).encode());process.stdin.close()
                last=None
                while process.poll() is None:
                    now=time.monotonic()
                    if should_stop and should_stop():
                        control.touch(exist_ok=True)
                        stopping=stopping or now+grace
                        if now>=stopping:break
                    if now>=deadline:
                        raise PreparationTimeout('source preparation exceeded its time limit')
                    try:
                        event=json.loads(status.read_text())
                        if event!=last and progress:
                            progress(SimpleNamespace(**event));last=event
                    except (OSError,ValueError,TypeError):pass
                    time.sleep(.1)
                if control.exists() or (should_stop and should_stop()):
                    raise model.AttemptCancelled('local source preparation cancelled')
                if process.returncode!=0:raise ValueError('source preparation process failed')
                return json.loads(output.read_text())
            finally:
                _reap(process,control,grace)
