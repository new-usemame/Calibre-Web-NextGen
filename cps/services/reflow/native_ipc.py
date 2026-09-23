"""Supervise a fresh native worker; review, money and publication stay here."""
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading

from . import native_codec as codec

WORKER = Path(__file__).with_name('native_worker.py')
MAX_CONTROL = 4096


class ChildExited(RuntimeError):
    pass


def read_owned(root, name):
    if name not in ('request.json', 'response.json', 'candidate.epub'):
        raise ValueError('unknown native artifact')
    path = Path(root) / name
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > codec.MAX_BYTES:
            raise ValueError('native artifact is not a bounded regular file')
        return handle.read(codec.MAX_BYTES + 1)


def write_owned(root, name, value):
    if name not in ('request.json', 'response.json'):
        raise ValueError('unknown native manifest')
    raw = codec.dumps(value)
    path = Path(root) / name
    temporary = Path(root) / (name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


class NativeDocument:
    """Named PDF handle whose parsing/rendering executes only in its child.

    The parent uses the original name only for SHA-256 checks. The child reads a
    private snapshot and accepts a fixed operation vocabulary, never provider RPC,
    credentials, a library destination, a Python callable, or an arbitrary path.
    """
    def __init__(self, source, *, scratch_root, cache_root=None, should_stop=None,
                 progress=None, ledger=None):
        from . import extract, ocr
        self.name = os.fspath(source)
        self.should_stop = should_stop
        self.progress = progress
        self.ledger = ledger
        self.process = None
        self.drain = None
        self.error_tail = b''
        self.control = None
        self.closed = False
        self.seq = 0
        self.last_phase = 'open'
        self.operation_book = None
        os.makedirs(scratch_root, mode=0o700, exist_ok=True)
        self.root = tempfile.mkdtemp(prefix='native-', dir=scratch_root)
        self.selector = selectors.DefaultSelector()
        try:
            shutil.copyfile(source, Path(self.root) / 'source.pdf')
            self.fingerprint = extract.document_fingerprint(Path(self.root) / 'source.pdf')
            if extract.document_fingerprint(source) != self.fingerprint:
                raise ValueError('Source changed while preparing native worker')
            environment = ocr._env()
            environment.update(TMPDIR=self.root, PYTHONNOUSERSITE='1', PYTHONFAULTHANDLER='1')
            # Fixed installed script and -I exclude cwd, user site and PYTHONPATH.
            read_fd, write_fd = os.pipe()
            self.control = os.fdopen(read_fd, 'rb', buffering=0)
            try:
                self.process = subprocess.Popen(
                    [sys.executable, '-I', str(WORKER), str(os.getpid()),
                     os.path.realpath(cache_root) if cache_root else '', str(write_fd)],
                    cwd=self.root, env=environment, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
                    pass_fds=(write_fd,))
            finally: os.close(write_fd)
            # Drain separately from the control pipe: native warnings cannot
            # block the worker or grow an unbounded diagnostic file. No native
            # library work happens on this plain byte-reader thread.
            self.drain = threading.Thread(target=self._drain_errors, daemon=True)
            self.drain.start()
            self.selector.register(self.control, selectors.EVENT_READ)
            self.buffer = b''
            self.page_count = self.call('open', {'fingerprint': self.fingerprint})
        except BaseException:
            self.close()
            raise

    def __len__(self): return self.page_count
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

    def _drain_errors(self):
        while True:
            data = self.process.stdout.read(8192)
            if not data: return
            self.error_tail = (self.error_tail + data)[-65536:]

    def _cancel(self):
        if self.should_stop and self.should_stop():
            from .model import AttemptCancelled
            raise AttemptCancelled('Native PDF work was cancelled')

    def _line(self):
        while b'\n' not in self.buffer:
            self._cancel()
            if not self.selector.select(.1):
                if self.process.poll() is not None:
                    raise ChildExited('Native PDF worker exited (%s) during %s' %
                                      (self.process.returncode, self.last_phase))
                continue
            data = os.read(self.control.fileno(), MAX_CONTROL + 1)
            if not data:
                # A closed control FD is already a failed protocol. Do not hang
                # the app waiting for an otherwise-live, misbehaving child.
                self.process.poll()
                raise ChildExited('Native PDF worker exited (%s) during %s' %
                                  (self.process.returncode, self.last_phase))
            self.buffer += data
            if len(self.buffer) > MAX_CONTROL:
                raise ValueError('Native control message exceeds bound')
        line, self.buffer = self.buffer.split(b'\n', 1)
        return json.loads(line)

    def call(self, operation, arguments, callback=None):
        if self.closed: raise ChildExited('Native worker is closed')
        self._cancel()
        self.seq += 1
        self.last_phase = operation
        write_owned(self.root, 'request.json', {'seq': self.seq, 'operation': operation, 'arguments': arguments})
        try:
            self.process.stdin.write((str(self.seq) + '\n').encode()); self.process.stdin.flush()
            while True:
                self._cancel()
                message = self._line()
                if not isinstance(message, dict) or message.get('seq') != self.seq:
                    raise ValueError('Native reply sequence mismatch')
                if set(message) == {'seq', 'progress'}:
                    event = message['progress']
                    if callback: callback(event)
                    elif self.progress:
                        from .pipeline import Progress
                        self.progress(Progress(**event))
                    self._cancel()
                    self.process.stdin.write(b'continue\n'); self.process.stdin.flush()
                    continue
                if set(message) != {'seq', 'ready'} or message['ready'] is not True:
                    raise ValueError('Native reply schema mismatch')
                reply = codec.loads(read_owned(self.root, 'response.json'))
                if (not isinstance(reply, dict) or set(reply) != {'seq', 'ok', 'value'}
                        or type(reply['ok']) is not bool or type(reply['seq']) is not int
                        or reply['seq'] != self.seq):
                    raise ValueError('Native response mismatch')
                if not reply['ok']:
                    from . import structural_ops, ocr, build_epub, model
                    error = {'ContractError': structural_ops.ContractError,
                             'OCRUnavailable': ocr.OCRUnavailable,
                             'OCRCancelled': ocr.OCRCancelled,
                             'BuildCancelled': build_epub.BuildCancelled,
                             'AttemptCancelled': model.AttemptCancelled}.get(reply['value'], ValueError)
                    raise error('Native PDF %s failed: %s' % (operation, str(reply['value'])[:120]))
                return reply['value']
        except (BrokenPipeError, EOFError) as exc:
            raise ChildExited('Native PDF worker disconnected during ' + operation) from exc

    def prepare_result(self, **arguments):
        arguments.pop('client', None); arguments.pop('ledger', None); arguments.pop('cache', None)
        arguments.pop('progress', None); arguments.pop('should_stop', None)
        opts = dict(arguments.get('recovery_opts') or {})
        opts.pop('cache_dir', None); opts.pop('scratch_dir', None)
        arguments['recovery_opts'] = opts
        return self.call('prepare', arguments)

    def prepare_operations(self, book, pno, revision, source_layer, **arguments):
        from dataclasses import replace
        source_page = arguments.pop('source_page', None)
        # Structure review never mutates Book. Bind once per object and let the
        # existing accept() compare the returned state digest before adoption.
        context = book if self.operation_book is not book else None
        result = self.call('operations', dict(book=context, pno=pno, revision=revision,
                           source_layer=source_layer, source_bound=source_page is not None, **arguments))
        from .structural_ops import Prepared
        if not isinstance(result, Prepared) or result.page != pno or result.pdf_digest != self.fingerprint:
            raise ValueError('invalid native prepared source binding')
        self.operation_book = book
        return replace(result, source_page=source_page)

    def build(self, book, out_path, **arguments):
        report = arguments.pop('report_html', None)
        trace = arguments.pop('runtime_progress', None)
        arguments.pop('should_stop', None)
        evidence = arguments.pop('evidence_progress', None)
        transform = arguments.pop('figure_transform', None)
        recovery = getattr(transform, '__self__', None)
        from .source import Recovery
        if transform is not None and (not isinstance(recovery, Recovery)
                                      or transform.__func__ is not Recovery.figure_rect):
            raise ValueError('native build requires the source recovery geometry transform')
        if callable(report):
            raise ValueError('native build requires a serializable report recipe')
        def progress(event):
            if event.get('kind') == 'evidence_progress':
                if (set(event) != {'kind', 'done', 'total'} or type(event['done']) is not int
                        or type(event['total']) is not int or not 0 <= event['done'] <= event['total']):
                    raise ValueError('invalid native evidence progress')
                if evidence: evidence(event['done'], event['total'])
            elif trace: trace(event)
        result = self.call('build', dict(book=book, report_html=report, recovery=recovery,
                                       **arguments), callback=progress)
        from .build_epub import BuildResult
        if not isinstance(result, BuildResult) or result.path != 'candidate.epub':
            raise ValueError('invalid native build result')
        candidate = Path(self.root) / 'candidate.epub'
        fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as src, open(out_path, 'xb') as dst:
            if not stat.S_ISREG(os.fstat(src.fileno()).st_mode): raise ValueError('invalid candidate')
            shutil.copyfileobj(src, dst)
        result.path = out_path
        return result

    def close(self):
        if self.closed: return
        self.closed = True
        process = self.process
        if process:
            try: process.stdin.close()
            except OSError: pass
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=2)
            # Tesseract can outlive the Python parent. Reap only this owned group.
            try: os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError): pass
            if self.drain:
                self.drain.join(timeout=2)
            try:
                self._exit_record(process)
            except OSError:
                # Lifecycle diagnostics are optional; accounting and task-final
                # ledger writes remain the caller's existing critical boundary.
                pass
        if self.control: self.control.close()
        self.selector.close()
        if process and self.drain and not self.drain.is_alive(): process.stdout.close()
        shutil.rmtree(self.root)

    def _exit_record(self, process):
        if self.ledger is not None:
            if self.error_tail:
                diagnostic = str(self.ledger.path) + '.native-stderr'
                fd = os.open(diagnostic, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as output: output.write(self.error_tail)
            self.ledger.record({'kind': 'native_worker', 'event': 'exit',
                                'exit_code': process.returncode, 'phase': self.last_phase}, durable=True)
