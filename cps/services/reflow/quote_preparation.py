"""Bounded local preparation jobs; no provider sessions or billing reservations."""
import copy
import hashlib
import json
import os
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
    pass


class PreparationStore:
    def __init__(self,directory,workers=1):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.workers=workers;self.pool=ThreadPoolExecutor(max_workers=workers,thread_name_prefix='reflow-source-quote')
        self.lock=threading.Lock();self.jobs={}

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

    @staticmethod
    def _public(job):
        result={k:copy.deepcopy(job[k]) for k in ('preparation_id','status','progress')}
        if job['status']=='ready':result['quote']=copy.deepcopy(job['quote'])
        if job.get('error'):result['error']=job['error']
        return result

    def start(self,owner,book_id,source,options,work):
        key,fingerprint=self._key(source,options);now=time.monotonic()
        with self.lock:
            self.jobs={i:j for i,j in self.jobs.items() if j['status'] in ('waiting','preparing','cancelling') or now-j['touched']<600}
            for job in self.jobs.values():
                if job['owner']==owner and job['book_id']==book_id and job['key']==key and job['status'] in ('waiting','preparing','ready'):
                    job['touched']=now;return self._public(job)
            # Bound retained opaque handles independently of worker count.
            terminal=sorted((j for j in self.jobs.values() if j['status'] not in ('waiting','preparing','cancelling')),key=lambda j:j['touched'])
            for old in terminal[:max(0,len(self.jobs)-255)]:self.jobs.pop(old['preparation_id'],None)
            active=sum(j['status'] in ('waiting','preparing','cancelling') for j in self.jobs.values())
            if active>=self.workers:raise PreparationBusy('source preparation workers are busy')
            job=dict(preparation_id=uuid.uuid4().hex,owner=owner,book_id=book_id,key=key,source=source,
                     fingerprint=fingerprint,options=copy.deepcopy(options),status='waiting',progress={},
                     stop=threading.Event(),touched=now)
            try:
                cached=json.loads((self.directory/(key+'.json')).read_text())
                self._valid_quote(cached,fingerprint);job.update(status='ready',quote=cached)
            except (OSError,ValueError,TypeError):pass
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


def measure_isolated(source,options,progress,should_stop,cache_root):
    """One owned renderer process; only bounded progress and quote JSON cross back.

    The process has no client/session/key arguments, and no secrets in its
    environment (``_child_environment``). It runs in Python's isolated mode (``-I``:
    no PYTHONPATH, no user site-packages, no script directory on sys.path), the
    same import boundary the service itself starts with (``-P`` and
    ``PYTHONNOUSERSITE=1`` in its s6 run script). A cancellation file is polled
    by the existing extraction, Recovery and candidate loops, including OCR's
    child-process cancellation. No partial quote is made ready or billed.
    """
    scratch=Path(cache_root)/'quote-scratch';scratch.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='prepare-',dir=scratch) as directory:
        root=Path(directory);control=root/'cancel';status=root/'progress.json';output=root/'quote.json'
        request=dict(source=str(source),options=options,cache_root=str(cache_root),
                     control=str(control),progress=str(status),output=str(output))
        with open(root/'stderr.log','wb') as errors:
            process=subprocess.Popen([sys.executable,'-I',str(Path(__file__).with_name('quote_worker.py'))],
                stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=errors,
                env=_child_environment(root),cwd=str(root))
            try:
                process.stdin.write(json.dumps(request).encode());process.stdin.close()
                last=None
                while process.poll() is None:
                    if should_stop and should_stop():control.touch(exist_ok=True)
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
                if process.poll() is None:
                    control.touch(exist_ok=True)
                    # Exception cleanup owns this subprocess; normal cancellation
                    # above waits for cooperative source/Recovery cleanup.
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.terminate();process.wait()
