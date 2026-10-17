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
import time

from . import native_codec as codec
from .model import AttemptCancelled
from .native_resources import Lease, ResourceStopped

WORKER = Path(__file__).with_name('native_worker.py')
MAX_CONTROL = 4096
SHUTDOWN_EOF_SECONDS = 2
NATIVE_REPAIR_STAGES = frozenset((
    'native_assessment', 'native_book_style', 'native_assembly',
    'native_routing', 'native_route_summary', 'native_heading_ladder',
    'native_style_profile', 'native_note_backbone', 'native_conservation'))
MAX_REPAIR_EVENTS = 4096
# A completed build may release hundreds of native page/image objects at close.
# Grant this bounded interval only after the child reports entering doc.close().
BUILD_DOCUMENT_CLOSE_GRACE_SECONDS = 30


def operation_timeout(operation, arguments, page_count):
    """Absolute native-work walls, including progress and local acknowledgements.

    Bulk work can perform bounded OCR for every selected page. Give it a page
    allowance capped at six hours; bounded parent transport waits are excluded. Opening and
    survey have smaller limits; neither performs OCR or model dispatch.
    """
    if operation == 'open':
        return 60
    if operation == 'survey':
        return min(3600, 120 + 2 * page_count)
    if operation == 'vision_sample_start':
        selected = min(page_count, max(2 * arguments.get('count', 20) + 1, 12))
        return min(21600, 1800 + 60 * selected)
    if operation in ('prepare', 'build', 'vision_convert', 'vision_quote'):
        selected = arguments.get('page_html') if operation == 'build' else arguments.get('page_numbers')
        pages = page_count if selected is None else len(selected)
        return min(21600, 1800 + 60 * pages)
    return 1800


class NativeDeadlineExceeded(TimeoutError):
    pass


class ChildExited(RuntimeError):
    pass


def read_owned(root, name):
    if name not in ('request.json', 'response.json', 'reader-request.json', 'reader-response.json', 'candidate.epub'):
        raise ValueError('unknown native artifact')
    path = Path(root) / name
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > codec.MAX_BYTES:
            raise ValueError('native artifact is not a bounded regular file')
        return handle.read(codec.MAX_BYTES + 1)


def write_owned(root, name, value):
    if name not in ('request.json', 'response.json', 'reader-request.json', 'reader-response.json'):
        raise ValueError('unknown native manifest')
    raw = codec.dumps(value)
    path = Path(root) / name
    temporary = Path(root) / (name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def _unit_context(controller, fingerprint):
    """Transport ownership metadata only; never a reader or retry capability."""
    records = {n: {'blocks': [dict(id=b['id'], role=b['role'],
                    degraded_source_context=bool(b.get('degraded_source_context')),
                    **({'fragment_sha256': b['fragment_sha256']} if b.get('fragment_sha256') else {}))
                    for b in report.get('blocks', [])]}
               for n, report in controller.records.items() if n in controller.pages}
    return dict(source_sha256=fingerprint, pages=sorted(controller.pages), records=records,
                blocked=[dict(page=n, block=bid,
                    **({'fragment_sha256': case['fragment_sha256']} if case.get('fragment_sha256') else {}))
                    for (n, bid), case in controller.degraded.items()])


def _repair_events(events):
    """Export fixed stage/type metadata and proof hashes, never source wording."""
    from . import unit_recovery
    import hashlib
    import re
    if not isinstance(events, list) or len(events) > MAX_REPAIR_EVENTS:
        raise unit_recovery.SourceIntegrityError('Invalid native repair event count')
    result = []
    for event in events:
        if (not isinstance(event, dict) or
                event.get('transition') != 'DETERMINISTIC_SOURCE_REPAIR' or
                not isinstance(event.get('stage'), str) or
                event['stage'] not in NATIVE_REPAIR_STAGES or
                not isinstance(event.get('reason'), str) or
                not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', event['reason']) or
                not isinstance(event.get('proof'), str)):
            raise unit_recovery.SourceIntegrityError('Invalid native repair event')
        result.append(dict(stage=event['stage'], transition=event['transition'],
            cause_type=event['reason'],
            proof_sha256=hashlib.sha256(event['proof'].encode('utf-8')).hexdigest()))
    return result


def _remote_result(value, controller, fingerprint):
    """Validate the complete repair envelope before admitting any events."""
    from . import unit_recovery
    import re
    if (not isinstance(value, dict) or
            set(value) != {'kind', 'source_sha256', 'value', 'events'} or
            value['kind'] != 'native_recovery_result' or
            value['source_sha256'] != fingerprint or
            not isinstance(value['events'], list) or len(value['events']) > MAX_REPAIR_EVENTS):
        raise unit_recovery.SourceIntegrityError('Invalid native recovery result')
    for event in value['events']:
        if (not isinstance(event, dict) or
                set(event) != {'stage', 'transition', 'cause_type', 'proof_sha256'} or
                not isinstance(event['stage'], str) or
                event['stage'] not in NATIVE_REPAIR_STAGES or
                event['transition'] != 'DETERMINISTIC_SOURCE_REPAIR' or
                not isinstance(event['cause_type'], str) or
                not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', event['cause_type']) or
                not isinstance(event['proof_sha256'], str) or
                not re.fullmatch(r'[a-f0-9]{64}', event['proof_sha256'])):
            raise unit_recovery.SourceIntegrityError('Invalid remote repair event')
    controller.events.extend(dict(event, origin='native_worker') for event in value['events'])
    return value['value']


def _remote_unit_failure(value, controller, fingerprint):
    """Authenticate the worker's explicit SOURCE owners before reconstruction."""
    from . import unit_recovery
    import re
    expected = {'kind', 'source_sha256', 'units', 'stage', 'cause_type'}
    if (not isinstance(value, dict) or set(value) != expected or
            value['kind'] != 'native_unit_failure' or value['source_sha256'] != fingerprint or
            not isinstance(value['units'], list) or not value['units'] or
            len(value['units']) > len(controller.pages) or
            not isinstance(value['stage'], str) or not re.fullmatch(r'[a-z_]{1,80}', value['stage']) or
            not isinstance(value['cause_type'], str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', value['cause_type']) or
            any(not isinstance(u, dict) or set(u) != {'page', 'block'} or
                u['block'] is not None and (not isinstance(u['block'], str) or
                    not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,159}', u['block']))
                for u in value['units'])):
        raise unit_recovery.SourceIntegrityError('Invalid remote source-unit ownership')
    controller.check_units(value['units'])
    units = []
    for unit in value['units']:
        owned = dict(unit)
        records = controller.records.get(unit['page'], {}).get('blocks', [])
        matching = [b for b in records if b['id'] == unit['block']]
        if len(matching) == 1 and matching[0].get('fragment_sha256'):
            owned['fragment_sha256'] = matching[0]['fragment_sha256']
        units.append(owned)
    return unit_recovery.UnitFailure(units, value['stage'],
        ValueError('Native source unit failed: ' + value['cause_type']))


class NativeDocument:
    """Named PDF handle whose parsing/rendering executes only in its child.

    The parent uses the original name only for SHA-256 checks. The child reads a
    private snapshot and accepts a fixed operation vocabulary, never credentials,
    a library destination, a Python callable, or an arbitrary path. Pixel-reader
    transport and financial requests use a fixed, source-bound parent broker.
    """
    def __init__(self, source, *, scratch_root, cache_root=None, should_stop=None,
                 progress=None, ledger=None):
        from . import extract, ocr
        self.name = os.fspath(source)
        self.cache_root = Path(cache_root).resolve() if cache_root else None
        self.reader_broker = None
        self.should_stop = should_stop
        self.progress = progress
        self.ledger = ledger
        self.process = None
        self.drain = None
        self.error_tail = b''
        self.stderr_drain_state = 'not_started'
        self.stderr_drain_error = None
        self.control = None
        self.closed = False
        self.seq = 0
        self.last_phase = 'open'
        self.last_completed_phase = None
        self.last_completed_sequence = None
        self.shutdown_evidence = None
        self.operation_book = None
        self.resource_lease = None
        self.operation_deadline = None
        self.deadline_exceeded = False
        self.root = None
        self.selector = selectors.DefaultSelector()
        try:
            self.resource_lease = Lease(cache_root or Path(scratch_root).parent, should_stop)
            self.resource_lease.remove_stale_scratch(scratch_root)
            os.makedirs(scratch_root, mode=0o700, exist_ok=True)
            self.root = tempfile.mkdtemp(prefix='native-', dir=scratch_root)
            shutil.copyfile(source, Path(self.root) / 'source.pdf')
            self.fingerprint = extract.document_fingerprint(Path(self.root) / 'source.pdf')
            if extract.document_fingerprint(source) != self.fingerprint:
                raise ValueError('Source changed while preparing native worker')
            environment = ocr._env()
            alternate_data = os.environ.get('REFLOW_LATIN_TESSDATA')
            if alternate_data is not None:
                environment['REFLOW_LATIN_TESSDATA'] = alternate_data
            environment.update(TMPDIR=self.root, PYTHONNOUSERSITE='1', PYTHONFAULTHANDLER='1')
            environment['REFLOW_NATIVE_LEASE_FD'] = str(self.resource_lease.fd)
            # Fixed installed script and -I exclude cwd, user site and PYTHONPATH.
            read_fd, write_fd = os.pipe()
            self.control = os.fdopen(read_fd, 'rb', buffering=0)
            try:
                self.resource_lease.before_launch()
                self.process = subprocess.Popen(
                    [sys.executable, '-I', str(WORKER), str(os.getpid()),
                     os.path.realpath(cache_root) if cache_root else '', str(write_fd)],
                    cwd=self.root, env=environment, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
                    pass_fds=(write_fd, self.resource_lease.fd))
            finally: os.close(write_fd)
            # Drain separately from the control pipe: native warnings cannot
            # block the worker or grow an unbounded diagnostic file. No native
            # library work happens on this plain byte-reader thread.
            self.stderr_drain_state = 'pending'
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
        try:
            while True:
                data = self.process.stdout.read(8192)
                if not data:
                    self.stderr_drain_state = 'eof'
                    return
                self.error_tail = (self.error_tail + data)[-65536:]
        except Exception as exc:
            # Thread termination is not proof of EOF. Keep only a type name,
            # never arbitrary exception text from a native diagnostic stream.
            self.stderr_drain_error = type(exc).__name__
            self.stderr_drain_state = 'error'

    def _cancel(self):
        deadline = getattr(self, 'operation_deadline', None)
        if deadline is not None and time.monotonic() >= deadline:
            raise NativeDeadlineExceeded("The native PDF operation exceeded its safety deadline.")
        self.check_resources()
        if self.should_stop and self.should_stop():
            from .model import AttemptCancelled
            raise AttemptCancelled('Native PDF work was cancelled')

    def check_resources(self, force=False):
        if self.resource_lease is not None:
            return self.resource_lease.check(force=force)
        return False

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
        from . import unit_recovery
        controller = unit_recovery.current()
        def invoke():
            try:
                return self._call(operation, arguments, callback)
            except ChildExited as error:
                if controller is not None:
                    raise unit_recovery.SourceIntegrityError('Native worker transport failed') from error
                raise
        # These fixed operations declare one physical SOURCE page in their API.
        # Multi-page preparation/build uses the narrower worker guards instead.
        if controller is not None and operation in ('operations', 'layout_prepare'):
            field = 'pno' if operation == 'operations' else 'page'
            if field not in arguments:
                raise unit_recovery.SourceIntegrityError('Native operation has no source owner')
            with unit_recovery.guard(arguments[field], 'native_remote_' + operation):
                return invoke()
        return invoke()

    def _call(self, operation, arguments, callback=None):
        if self.closed: raise ChildExited('Native worker is closed')
        self._cancel()
        self.seq += 1
        self.last_phase = operation
        from . import unit_recovery
        controller = unit_recovery.current()
        def protocol_error(message, cause=None):
            error = unit_recovery.SourceIntegrityError if controller is not None else ValueError
            if cause is not None:raise error(message) from cause
            raise error(message)
        if controller is not None and operation in ('prepare','build','operations','layout_prepare','layout_figures'):
            arguments = dict(arguments, _unit_recovery=_unit_context(controller, self.fingerprint))
        write_owned(self.root, 'request.json', {'seq': self.seq, 'operation': operation, 'arguments': arguments})
        self.operation_deadline = time.monotonic() + operation_timeout(
            operation, arguments, getattr(self, 'page_count', 0))
        try:
            self.process.stdin.write((str(self.seq) + '\n').encode()); self.process.stdin.flush()
            bridge_sequence = 0
            while True:
                self._cancel()
                try:
                    message = self._line()
                except (ValueError, UnicodeError) as error:
                    protocol_error('Invalid native control reply', error)
                if not isinstance(message, dict) or message.get('seq') != self.seq:
                    protocol_error('Native reply sequence mismatch')
                if set(message) == {'seq', 'reader_request'}:
                    if (operation != 'vision_convert' or self.reader_broker is None
                            or type(message['reader_request']) is not int
                            or message['reader_request'] != bridge_sequence + 1):
                        protocol_error('Unsolicited native reading request')
                    bridge_sequence = message['reader_request']
                    request = codec.loads(read_owned(self.root, 'reader-request.json'))
                    # A native wall measures renderer work. Synchronous parent
                    # finance/HTTP has its own fixed transport limits; a native
                    # timeout must never kill a worker during an in-flight bill.
                    started = time.monotonic()
                    try:
                        answer = self.reader_broker.handle(request)
                    finally:
                        self.operation_deadline += time.monotonic() - started
                    write_owned(self.root, 'reader-response.json', answer)
                    self.process.stdin.write(b'continue\n'); self.process.stdin.flush()
                    continue
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
                    protocol_error('Native reply schema mismatch')
                try:
                    reply = codec.loads(read_owned(self.root, 'response.json'))
                except (ValueError, UnicodeError) as error:
                    protocol_error('Invalid native response artifact', error)
                if (not isinstance(reply, dict) or set(reply) != {'seq', 'ok', 'value'}
                        or type(reply['ok']) is not bool or type(reply['seq']) is not int
                        or reply['seq'] != self.seq):
                    protocol_error('Native response mismatch')
                if not reply['ok']:
                    if isinstance(reply['value'], dict):
                        if controller is None:
                            raise ValueError('Unsolicited native recovery reply')
                        value = reply['value']
                        if value.get('kind') == 'native_unit_failure':
                            raise _remote_unit_failure(value, controller, self.fingerprint)
                        if (set(value) != {'kind','source_sha256','cause_type','errno'} or
                                value.get('kind') != 'native_fatal_error' or
                                value.get('source_sha256') != self.fingerprint or
                                value.get('cause_type') not in ('MemoryError','SourceIntegrityError','OSError') or
                                value['errno'] is not None and type(value['errno']) is not int):
                            raise unit_recovery.SourceIntegrityError('Invalid native fatal-error reply')
                        if value['cause_type'] == 'MemoryError':raise MemoryError('Native worker memory failure')
                        if value['cause_type'] == 'OSError':raise OSError(value['errno'], 'Native worker storage failure')
                        raise unit_recovery.SourceIntegrityError('Native worker source/configuration failure')
                    from . import structural_ops, ocr, build_epub, model, extract, native_limits
                    if reply['value'] == 'PasswordProtected':
                        raise extract.PasswordProtected(
                            'This PDF is password-protected. Remove its password before converting it.')
                    if reply['value'] == 'DecodedBudgetExceeded':
                        raise native_limits.DecodedBudgetExceeded(
                            'The decoded PDF stream exceeds the 128 MiB safety budget, or the decoded PDF document exceeds the 8 GiB safety budget.')
                    error = {'ContractError': structural_ops.ContractError,
                             'OCRUnavailable': ocr.OCRUnavailable,
                             'OCRCancelled': ocr.OCRCancelled,
                             'BuildCancelled': build_epub.BuildCancelled,
                             'AttemptCancelled': model.AttemptCancelled}.get(reply['value'], ValueError)
                    raise error('Native PDF %s failed: %s' % (operation, str(reply['value'])[:120]))
                value = reply['value']
                if controller is not None and operation in ('prepare','build','operations','layout_prepare','layout_figures'):
                    value = _remote_result(value, controller, self.fingerprint)
                self.last_completed_phase = operation
                self.last_completed_sequence = self.seq
                return value
        except NativeDeadlineExceeded:
            self.deadline_exceeded = True
            self.close()
            raise
        except (BrokenPipeError, EOFError) as exc:
            raise ChildExited('Native PDF worker disconnected during ' + operation) from exc
        finally:
            self.operation_deadline = None

    def vision_sample_start(self, count):
        if type(count) is not int or not 1 <= count <= 60:
            raise ValueError('Invalid native sample size')
        value = self.call('vision_sample_start', {'count': count})
        if type(value) is not int or not 0 <= value < self.page_count:
            raise ValueError('Invalid native sample origin')
        return value

    def vision_quote(self, *, page_numbers=None):
        return self.call('vision_quote', {'page_numbers': page_numbers})

    def convert_vision(self, out_path, reader, *, page_numbers=None, metadata=None,
                       quote=None, job_id=None):
        from .vision_bridge import ParentBroker
        from . import vision_reading
        if not isinstance(reader, vision_reading.Reader):
            raise ValueError('Native conversion requires the page Reader')
        numbers = list(range(self.page_count)) if page_numbers is None else list(page_numbers)
        if (not numbers or len(set(numbers)) != len(numbers) or any(
                type(n) is not int or not 0 <= n < self.page_count for n in numbers)):
            raise ValueError('Invalid native page scope')
        if self.cache_root is None:
            raise ValueError('Native vision conversion needs an owned cache root')
        cache = reader.cache.resolve()
        try:
            relative = cache.relative_to(self.cache_root)
        except ValueError as error:
            raise ValueError('Reading cache must belong to the native data root') from error
        if not relative.parts or any(part.startswith('.') for part in relative.parts):
            raise ValueError('Invalid owned reading cache')
        authorizer = None
        if quote is not None:
            from . import vision_quote
            authorizer = vision_quote.Authorizer(quote, self.fingerprint, numbers)
        self.reader_broker = ParentBroker(reader, self.fingerprint, numbers,
            authorizer=authorizer, should_stop=self.should_stop)
        brokers = getattr(self, '_vision_brokers', [])
        brokers.append(self.reader_broker)
        self._vision_brokers = brokers
        try:
            result = self.call('vision_convert', dict(page_numbers=numbers,
                metadata=dict(metadata or {}), cache=str(relative), job_id=job_id, reader={
                    'enabled': bool(reader.key), 'cap_usd': reader.ledger.cap_usd,
                    'token_confidence': reader.token_confidence,
                    'confidence_protocol': reader.confidence_protocol,
                    'reuse_legacy': reader.reuse_legacy}))
        finally:
            self.reader_broker = None
        if not isinstance(result, dict) or result.get('source_document_sha256') != self.fingerprint:
            raise ValueError('Invalid native vision result source')
        candidate = Path(self.root) / 'candidate.epub'
        Path(out_path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as src, open(out_path, 'xb') as dst:
            if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
                raise ValueError('Invalid native vision candidate')
            shutil.copyfileobj(src, dst)
        return result

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

    def prepare_layout(self, book, pno, source, raw, previous_source=None, previous_raw=None):
        from . import layout_native, source_inventory, layout_ops
        args=dict(page=pno,book_digest=layout_native.book_digest(book),source_identity=source.identity,
            raw_digest=source_inventory.digest(raw),
            previous_identity=previous_source.identity if previous_source else None,
            previous_raw_digest=source_inventory.digest(previous_raw) if previous_raw else None)
        if source.report().get('source_readings'):args['reading_source']=source
        if previous_source and previous_source.report().get('source_readings'):args['previous_reading_source']=previous_source
        result=self.call('layout_prepare',args)
        if type(result) is not layout_ops.PreparedLayout or result.page!=pno or result.pdf_digest!=self.fingerprint:
            raise ValueError('invalid native prepared layout binding')
        return result

    def layout_figure_failures(self, book, plans):
        from . import layout_native
        result=self.call('layout_figures',dict(book_digest=layout_native.book_digest(book),plans=plans))
        pages={p.prepared.page for p in plans}
        if not isinstance(result,dict) or not set(result).issubset(pages) or any(
                not isinstance(refs,list) or any(not isinstance(src,str) or not src.startswith('images/fig_p')
                    for src in refs) for refs in result.values()):
            raise ValueError('invalid native layout figure verdict')
        return result

    def capture_operation_audit(self, result, book_id):
        from . import operation_audit, native_audit
        plans = [p for p in getattr(result, 'operation_plans', ()) if p.selected]
        if not plans: return [], {}
        if self.operation_book is not result.book:
            raise ValueError('native audit requires current prepared book')
        operations = []
        for plan in plans:
            p = plan.prepared
            plan.compile(result.book, self, source_page=p.source_page)
            operations.append(dict(page=p.page, snapshot_id=p.snapshot_id,
                source_identity=p.source_page.identity if p.source_page is not None else None,
                selected=list(plan.selected)))
        pages = {p['page'] for p in operations}
        records = [{k:r[k] for k in ('page','stage','request_sha256','cached','attempt') if k in r}
                   for r in getattr(result, 'stage_records', ()) if r['page'] in pages]
        args = native_audit.request(dict(book_id=book_id, fingerprint=self.fingerprint,
            operations=operations, stage_records=records), self.fingerprint, self.page_count)
        expected, matching = operation_audit.capture(result, book_id)
        reply = self.call('operation_audit', args)
        return native_audit.validate_reply(reply, expected, matching, plans, result.book)

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
        if recovery is not None:
            from dataclasses import replace
            # The build needs only provenance for figure_rect, not the complete
            # OCR/native analysis graph already consumed during preparation.
            recovery = replace(recovery, pages=[])
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
        try:
            self._close_process()
        finally:
            try:
                if self.process is not None and self.process.poll() is not None:
                    for broker in getattr(self, '_vision_brokers', []):
                        broker.finalize_worker_exit()
                    self._vision_brokers = []
            finally:
                if self.resource_lease is not None:
                    self.resource_lease.close()

    def require_clean_shutdown(self):
        evidence = self.shutdown_evidence
        if (not evidence or evidence.get('exit_code') != 0
                or evidence.get('entry_exit_code') is not None
                or evidence.get('eof') != 'closed'
                or evidence.get('completed_phase') != evidence.get('phase')
                or evidence.get('completed_sequence') != evidence.get('sequence')
                or not evidence.get('direct_child_reaped')
                or evidence.get('group_state') != 'absent'
                or not evidence.get('stderr_drain_complete')
                or evidence.get('cleanup_error') or evidence.get('record_error')
                or any(item['reason'] in ('eof_timeout', 'document_close_timeout', 'cancelled', 'resource_stop', 'operation_deadline')
                       for item in evidence['signals'])):
            raise ChildExited('Native runtime cleanup failed or could not be verified')

    @staticmethod
    def _group_state(pid):
        try:
            os.killpg(pid, 0)
            return 'present'
        except ProcessLookupError:
            return 'absent'
        except OSError:
            return 'unknown'

    def _read_shutdown_phases(self):
        """Read only the finite post-command diagnostic tail, never wait on it."""
        if self.control is None:
            return [], None
        try:
            os.set_blocking(self.control.fileno(), False)
            raw = getattr(self, 'buffer', b'')
            while len(raw) <= 4096:
                try:
                    part = os.read(self.control.fileno(), 4097 - len(raw))
                except BlockingIOError:
                    break
                if not part:
                    break
                raw += part
            if len(raw) > 4096:
                raise ValueError('oversized shutdown diagnostics')
            # Retain frames while the parent observes a running close. The final
            # read after reap must include those already consumed for the grace.
            self.buffer = raw
            if raw and not raw.endswith(b'\n'):
                raise ValueError('partial shutdown diagnostics')
            rows = []
            phases = {'stdin_eof', 'parent_gone', 'command_loop_error',
                      'document_close_started', 'document_closed', 'runtime_returning'}
            for line in raw.splitlines():
                row = json.loads(line)
                if (not isinstance(row, dict) or set(row) != {'seq', 'shutdown_phase', 'monotonic_ns', 'cpu_ns'}
                        or row['seq'] != self.seq or row['shutdown_phase'] not in phases
                        or any(type(row[k]) is not int or row[k] < 0 for k in ('seq', 'monotonic_ns', 'cpu_ns'))):
                    raise ValueError('invalid shutdown diagnostics')
                rows.append(dict(phase=row['shutdown_phase'], seq=row['seq'],
                                 monotonic_ns=row['monotonic_ns'], cpu_ns=row['cpu_ns']))
            if len(rows) > 4:
                raise ValueError('too many shutdown diagnostics')
            return rows, None
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return [], type(exc).__name__

    def _close_process(self):
        process = self.process
        evidence = None
        started = time.monotonic()
        if process:
            evidence = dict(kind='native_shutdown', event='shutdown', phase=self.last_phase,
                completed_phase=self.last_completed_phase,
                completed_sequence=self.last_completed_sequence, sequence=self.seq,
                pid=process.pid, pgid=process.pid, entry_exit_code=process.poll(),
                eof='not_requested', wait='not_started', signals=[],
                close_entry_monotonic_ns=time.monotonic_ns())
        try:
            if process:
                def kill_group(reason):
                    request = dict(signal='SIGKILL', reason=reason, result='requested',
                        elapsed_seconds=time.monotonic() - started,
                        group_state_before=self._group_state(process.pid))
                    evidence['signals'].append(request)
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                        request['result'] = 'sent'
                    except ProcessLookupError:
                        request['result'] = 'absent'
                    except PermissionError:
                        request['result'] = 'permission_denied'
                    except OSError:
                        request['result'] = 'error'
                        raise
                try:
                    process.stdin.close()
                    evidence['eof'] = 'closed'
                    evidence['eof_closed_monotonic_ns'] = time.monotonic_ns()
                except OSError:
                    evidence['eof'] = 'error'
                deadline = time.monotonic() + SHUTDOWN_EOF_SECONDS
                grace_granted = False
                close_finished = False
                termination_reason = 'operation_deadline' if getattr(self, 'deadline_exceeded', False) else None
                while process.poll() is None and termination_reason is None:
                    stop = getattr(self, 'should_stop', None)
                    if stop:
                        try: cancelled = bool(stop())
                        except Exception: cancelled = True
                        if cancelled:
                            termination_reason = 'cancelled'
                            break
                    try:
                        self.check_resources()
                    except BaseException as exc:
                        # Keep the lease checkpoint active during native close.
                        # A stop (including an unknown measurement) must reach
                        # owned-child cleanup before close() releases the lease.
                        if isinstance(exc, AttemptCancelled) and not isinstance(exc, ResourceStopped):
                            termination_reason = 'cancelled'
                        else:
                            termination_reason = 'resource_stop'
                            evidence['resource_stop_error'] = type(exc).__name__
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        termination_reason = ('document_close_timeout' if grace_granted
                                              and not close_finished else 'eof_timeout')
                        break
                    try:
                        process.wait(timeout=min(.1, remaining))
                    except subprocess.TimeoutExpired:
                        if (self.last_phase == 'build'
                                and self.last_completed_phase == 'build'
                                and self.last_completed_sequence == self.seq):
                            observed, error = self._read_shutdown_phases()
                            if not error:
                                names = [row['phase'] for row in observed]
                                if ('document_close_started' in names and not grace_granted
                                        and 'document_closed' not in names):
                                    grace_granted = True
                                    deadline = time.monotonic() + BUILD_DOCUMENT_CLOSE_GRACE_SECONDS
                                    evidence['document_close_grace'] = dict(
                                        trigger='document_close_started',
                                        allowed_seconds=BUILD_DOCUMENT_CLOSE_GRACE_SECONDS,
                                        granted_elapsed_seconds=time.monotonic() - started)
                                if grace_granted and 'document_closed' in names and not close_finished:
                                    close_finished = True
                                    deadline = min(deadline, time.monotonic() + SHUTDOWN_EOF_SECONDS)
                                    evidence['document_close_grace']['finished_elapsed_seconds'] = time.monotonic() - started
                if termination_reason is None:
                    process.wait()
                    evidence['wait'] = 'reaped'
                else:
                    evidence['wait'] = 'timeout' if termination_reason != 'cancelled' else 'cancelled'
                    kill_group(termination_reason)
                    process.wait(timeout=SHUTDOWN_EOF_SECONDS)
                    evidence['wait'] = 'reaped_after_signal_request'
                # Tesseract can outlive the Python parent. Reap only this owned group.
                kill_group('descendant_cleanup')
                if self.drain:
                    self.drain.join(timeout=2)
                phases, diagnostic_error = self._read_shutdown_phases()
                evidence['child_shutdown_phases'] = phases
                evidence['child_shutdown_diagnostic_error'] = diagnostic_error
                try:
                    self._exit_record(process)
                except OSError:
                    pass
            if self.control: self.control.close()
            self.selector.close()
            if process and self.drain and not self.drain.is_alive(): process.stdout.close()
            if self.root is not None:
                shutil.rmtree(self.root)
        except BaseException as exc:
            if evidence is not None: evidence['cleanup_error'] = type(exc).__name__
            raise
        finally:
            if evidence is not None:
                evidence.update(elapsed_seconds=time.monotonic() - started,
                    exit_code=process.poll(),
                    direct_child_reaped=process.returncode is not None,
                    group_state=self._group_state(process.pid),
                    stderr_drain_state=self.stderr_drain_state,
                    stderr_drain_error=self.stderr_drain_error,
                    stderr_drain_complete=(self.stderr_drain_state == 'eof'
                        and self.drain is not None and not self.drain.is_alive()))
                # This is evidence, not an inferred success category. A sent signal
                # does not prove causation; present/unknown groups are not reaped.
                # Emit before close() releases the parent's inherited lease handle.
                self.shutdown_evidence = evidence
                if self.ledger:
                    try: self.ledger.record(evidence, durable=True)
                    except OSError as exc: evidence['record_error'] = type(exc).__name__

    def _exit_record(self, process):
        if self.ledger is not None:
            if self.error_tail:
                diagnostic = str(self.ledger.path) + '.native-stderr'
                fd = os.open(diagnostic, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as output: output.write(self.error_tail)
            self.ledger.record({'kind': 'native_worker', 'event': 'exit',
                                'exit_code': process.returncode, 'phase': self.last_phase}, durable=True)
