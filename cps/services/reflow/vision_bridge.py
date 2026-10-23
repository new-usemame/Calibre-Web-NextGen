"""Fixed finance/transport bridge for source reading in the native worker.

PDF geometry, transcription repair and cache validation stay with the renderer.
The parent alone holds credentials and the authoritative financial ledger.
"""
import base64
from contextlib import contextmanager
import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import re

from . import ledger as ledger_mod, model, shared_budget, unit_recovery, vision_reading

LEDGER_METHODS = frozenset(('spent', 'pending_usd', 'remaining', 'would_exceed',
    'reserve', 'reserve_attempt', 'release_attempt', 'reconcile_attempt',
    'entries', '_financial_state', 'record'))
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def compact_report(report):
    """Share equal report values before the existing bounded DAG transport.

    Publication metrics can repeat once per marked word and in multiple sweeps.
    Reports are value snapshots: sharing equal containers preserves every key,
    value and Python container type while keeping that redundant representation
    out of the native wire. The EPUB's complete JSON report is unchanged.
    """
    from . import native_codec
    pool = {}; visited = {}; active = set()
    def visit(value, depth=0):
        if depth > native_codec.MAX_DEPTH:
            raise ValueError('Native report nesting exceeds bound')
        kind = type(value)
        if value is None or kind in (str, bool, int):
            return value, (kind.__name__, value)
        if kind is float and math.isfinite(value):
            return value, ('float', value.hex())
        if kind not in (dict, list, tuple):
            raise ValueError('Unsupported native report value')
        identity = id(value)
        if identity in active: raise ValueError('Cyclic native report')
        if identity in visited: return visited[identity]
        active.add(identity)
        if kind is dict:
            if any(type(key) not in (str, int) for key in value):
                raise ValueError('Unsupported native report key')
            rows = [(key, visit(item, depth + 1)) for key, item in value.items()]
            signature = ('dict', tuple((type(key).__name__, key, item[1]) for key, item in rows))
            create = lambda: {key: item[0] for key, item in rows}
        else:
            rows = [visit(item, depth + 1) for item in value]
            signature = (kind.__name__, tuple(item[1] for item in rows))
            create = lambda: kind(item[0] for item in rows)
        active.remove(identity)
        if signature not in pool:
            pool[signature] = (create(), ('container', len(pool)))
        result = pool[signature]
        visited[identity] = result
        return result
    try:
        result, _ = visit(report)
        if type(result) is not dict: raise ValueError('Invalid native vision report')
        return result
    finally:
        # The returned snapshot owns its values; traversal indexes are temporary.
        pool.clear()
        visited.clear()
        active.clear()
        visit = None


def error_reply(error):
    """Transport a fixed exception vocabulary, never a Python constructor."""
    if isinstance(error, ledger_mod.CapExceeded):
        return dict(ok=False, error='CapExceeded', spent=error.spent,
                    cap=error.cap, projected=error.projected)
    if isinstance(error, shared_budget.BudgetError):
        return dict(ok=False, error='BudgetError', code=error.code)
    allowed = (unit_recovery.SourceIntegrityError, model.AttemptCancelled,
        model.UncertainBilling, model.UnusableAnswer, model.ModelError, OSError)
    import requests
    if isinstance(error, requests.RequestException):
        name = 'TransportUncertain'
    else:
        name = next((kind.__name__ for kind in allowed if isinstance(error, kind)),
                    'SourceIntegrityError')
    return dict(ok=False, error=name, message=str(error)[:1000],
                errno=getattr(error, 'errno', None))


def checked_reply(reply):
    if not isinstance(reply, dict) or type(reply.get('ok')) is not bool:
        raise unit_recovery.SourceIntegrityError('Invalid parent reading response')
    if reply['ok']:
        if set(reply) != {'ok', 'value'}:
            raise unit_recovery.SourceIntegrityError('Invalid parent response fields')
        return reply['value']
    kind = reply.get('error')
    if kind == 'CapExceeded':
        raise ledger_mod.CapExceeded(reply['spent'], reply['cap'], reply['projected'])
    if kind == 'BudgetError':
        raise shared_budget.BudgetError(reply['code'])
    if kind == 'OSError':
        raise OSError(reply.get('errno'), reply.get('message', 'Reading storage failure'))
    if kind == 'TransportUncertain':
        import requests
        raise requests.RequestException(reply.get('message', 'Reading transport is unresolved'))
    errors = {cls.__name__: cls for cls in (unit_recovery.SourceIntegrityError,
        model.AttemptCancelled, model.UncertainBilling, model.UnusableAnswer, model.ModelError)}
    error = errors.get(kind, unit_recovery.SourceIntegrityError)
    raise error(reply.get('message', 'Parent reading operation failed'))


class RemoteLedger:
    """A local fsync witness and fixed RPC methods, never financial authority."""
    def __init__(self, rpc, root, cap_usd):
        self.rpc = rpc
        self.cap_usd = cap_usd
        self.path = str(Path(root) / 'reader-fsync.jsonl')
        Path(self.path).touch(mode=0o600, exist_ok=True)

    def __getattr__(self, method):
        if method not in LEDGER_METHODS:
            raise AttributeError(method)
        return lambda *args, **kwargs: self.rpc('ledger',
            dict(method=method, args=list(args), kwargs=kwargs))


class ChildReader(vision_reading.Reader):
    """Preserve Reader's native repair callbacks; delegate only money and HTTP."""
    def __init__(self, rpc, cache, root, cap_usd, *, enabled=False,
                 token_confidence=False, confidence_protocol=None, reuse_legacy=False):
        self.rpc = rpc
        super().__init__('parent-owned-reader' if enabled else None, cache,
            RemoteLedger(rpc, root, cap_usd), token_confidence=token_confidence,
            confidence_protocol=confidence_protocol, reuse_legacy=reuse_legacy)

    @contextmanager
    def _budget(self):
        # The real parent budget lock guards every financial RPC. This local
        # lock preserves Reader's intra-process sequencing without file replay.
        with self._budget_lock:
            yield

    def financial_dispatch_allowed(self):
        return self.rpc('financial_dispatch_allowed', {})

    def _retry_reader(self, *, offline=False):
        retry = ChildReader(self.rpc, self.cache, Path(self.ledger.path).parent,
            self.ledger.cap_usd, enabled=bool(self.key) and not offline,
            token_confidence=self.token_confidence,
            confidence_protocol=self.confidence_protocol, reuse_legacy=False)
        retry.prompt += ('\nReview this same page after a rejected reading. Recheck every block against its printed pixels, all coordinates and confidence fields, and the exact correspondence of printed note references to note IDs. Preserve all original content; do not infer missing ink.\n')
        return retry

    def _direct_post(self, payload, pending, reservation, bound, *, title, timeout):
        import requests
        value = self.rpc('post', dict(payload=payload, pending=pending.name,
            reservation=reservation, bound_usd=bound, title=title, timeout=list(timeout)))
        response = requests.Response()
        response.status_code = value['status']
        response._content = value['body']
        response.headers.update(value['headers'])
        return response


def recover_unsent_receipts(cache, ledger):
    """Remove only an exact unsent request after its authoritative release.

    Page lock precedes job lock, matching Reader.read. Charged responses,
    malformed receipts and dispatched attempts are never discarded here.
    """
    cache = Path(cache)
    if not cache.is_dir() or cache.is_symlink(): return
    for pending in cache.glob('*.pending'):
        if (pending.is_symlink() or not re.fullmatch(
                r'(?:marker-)?[a-f0-9]{64}-[a-f0-9]{64}\.pending', pending.name)):
            continue
        label = pending.stem.rsplit('-', 1)[0]
        with (cache / (label + '.lock')).open('a') as page_lock:
            fcntl.flock(page_lock, fcntl.LOCK_EX)
            with open(ledger.path + '.lock', 'a') as job_lock:
                fcntl.flock(job_lock, fcntl.LOCK_EX)
                try:
                    if pending.stat().st_size > 4096: continue
                    receipt = json.loads(pending.read_text())
                    if not isinstance(receipt, dict) or set(receipt) != {'reservation', 'bound_usd'}:
                        continue
                    entries = [json.loads(line) for line in Path(ledger.path).read_bytes().splitlines() if line.strip()]
                    attempt = receipt['reservation']
                    original = [e for e in entries if e.get('kind') == 'reservation'
                        and e.get('event') == 'pending' and e.get('attempt') == attempt]
                    released = any(e.get('kind') == 'reservation' and e.get('event') == 'released'
                        and e.get('attempt') == attempt and e.get('reason') == 'worker_exited_before_dispatch'
                        for e in entries)
                    unsafe = any(e.get('attempt') == attempt and (e.get('kind') == 'vision_dispatch'
                        or e.get('event') == 'reconciled' or e.get('cost_usd') is not None) for e in entries)
                    if (not released or unsafe or len(original) != 1
                            or original[0].get('page_label') != label
                            or original[0].get('bound_usd') != receipt['bound_usd']):
                        continue
                    pending.unlink()
                    fd = os.open(cache, os.O_RDONLY)
                    try: os.fsync(fd)
                    finally: os.close(fd)
                except FileNotFoundError:
                    continue
                except (ValueError, TypeError, AttributeError):
                    continue


class ParentBroker:
    """One selected source scope, one real Reader, one consent authorizer."""
    def __init__(self, reader, source_sha256, pages, *, authorizer=None, should_stop=None):
        self.reader = reader
        self.source_sha256 = source_sha256
        self.pages = frozenset(pages)
        self.authorizer = authorizer
        self.should_stop = should_stop
        self.dispatched = set()

    def finalize_worker_exit(self):
        """Called only after the owned native worker has been reaped."""
        recover = getattr(self.reader.ledger, 'recover_authoritative', None)
        if recover is not None:
            recover(worker_gone=True)
            recover_unsent_receipts(self.reader.cache, self.reader.ledger)

    def handle(self, request):
        try:
            value = self._handle(request)
            return dict(ok=True, value=value)
        except Exception as error:
            return error_reply(error)

    def _handle(self, request):
        if (not isinstance(request, dict) or set(request) != {'operation', 'arguments', 'context'}
                or not isinstance(request['arguments'], dict)):
            raise unit_recovery.SourceIntegrityError('Invalid reading bridge request')
        context = request['context']
        if (not isinstance(context, dict) or set(context) != {'source_sha256', 'page_index0'}
                or context['source_sha256'] != self.source_sha256
                or context['page_index0'] is not None and
                (type(context['page_index0']) is not int or context['page_index0'] not in self.pages)):
            raise unit_recovery.SourceIntegrityError('Reading source scope changed')
        operation, args = request['operation'], request['arguments']
        if operation == 'financial_dispatch_allowed':
            if args:
                raise unit_recovery.SourceIntegrityError('Invalid finance query')
            # The application authorizes no further paid page after an unknown
            # outcome. Cached readings and disclosed originals remain available.
            with self.reader._budget():
                if self.reader.ledger.pending_usd():
                    return False
            return self.reader.financial_dispatch_allowed()
        if operation == 'ledger':
            if (set(args) != {'method', 'args', 'kwargs'} or args['method'] not in LEDGER_METHODS
                    or not isinstance(args['args'], list) or not isinstance(args['kwargs'], dict)):
                raise unit_recovery.SourceIntegrityError('Invalid ledger method')
            if args['method'] == 'record':
                if not args['args'] or args['args'][0].get('kind') != 'billing_quarantine':
                    raise unit_recovery.SourceIntegrityError('Invalid reader ledger record')
            if args['method'] == 'reconcile_attempt':
                values = args['args']
                if len(values) != 2 or args['kwargs']:
                    raise unit_recovery.SourceIntegrityError('Invalid reading settlement')
                metered = [e for e in self.reader.ledger.entries('reservation')
                    if e.get('event') == 'reconciled' and e.get('attempt') == values[0]]
                if not metered or any(e.get('cost_usd') != values[1] for e in metered):
                    raise unit_recovery.SourceIntegrityError('Child settlement lacks a parent metered response')
            if args['method'] == 'release_attempt':
                values = args['args']
                if len(values) != 2 or args['kwargs'] or values[1] not in (
                        'local_pre_dispatch', 'pre_dispatch', 'rejected_before_generation'):
                    raise unit_recovery.SourceIntegrityError('Invalid reading release')
                if values[0] in self.dispatched:
                    # Dispatch outcome is parent-owned. The real transport has
                    # already released a proved rejection; a child cannot erase
                    # either an ambiguous hold or a metered debit.
                    with self.reader._budget():
                        state = self.reader.ledger._financial_state()
                        if (values[0] in state[1] or any(e.get('attempt') == values[0]
                                and e.get('event') == 'reconciled'
                                for e in self.reader.ledger.entries('reservation'))):
                            raise unit_recovery.SourceIntegrityError('Dispatched reading cannot be released by child')
                    return None
            if args['method'] == 'reserve_attempt':
                values = args['args']
                if (context['page_index0'] is None or len(values) != 4 or args['kwargs']
                        or values[2] != vision_reading.MODEL
                        or values[3] not in (vision_reading.VERSION, 'printed-marker-v1')
                        or type(values[1]) not in (float, int) or not math.isfinite(values[1]) or values[1] <= 0):
                    raise unit_recovery.SourceIntegrityError('Invalid source reading reservation')
            with self.reader._budget():
                if args['method'] == 'reserve_attempt':
                    # Provenance is committed in the store BEFORE the pending
                    # mirror: recovery can identify a store-only reservation.
                    return self.reader.ledger.reserve_attempt(*args['args'],
                        context={'route_version': 'vision-parent-v1'})
                return getattr(self.reader.ledger, args['method'])(*args['args'], **args['kwargs'])
        if operation == 'post':
            return self._post(args, context)
        raise unit_recovery.SourceIntegrityError('Unsupported reading bridge operation')

    def _post(self, args, context):
        if set(args) != {'payload','pending','reservation','bound_usd','title','timeout'}:
            raise unit_recovery.SourceIntegrityError('Invalid reading dispatch envelope')
        name = args['pending']
        if not isinstance(name, str) or not re.fullmatch(r'(?:marker-)?[a-f0-9]{64}-[a-f0-9]{64}\.pending', name):
            raise unit_recovery.SourceIntegrityError('Invalid reading receipt identity')
        pending = self.reader.cache / name
        attempt, bound = args['reservation'], args['bound_usd']
        with self.reader._budget():
            reservations = [r for r in self.reader.ledger.entries('reservation')
                if r.get('event') == 'pending' and r.get('attempt') == attempt]
            if (len(reservations) != 1 or type(bound) not in (float, int)
                    or not math.isfinite(bound) or bound <= 0
                    or reservations[0]['bound_usd'] != bound
                    or reservations[0]['page_label'] != pending.stem.rsplit('-', 1)[0]
                    or self.reader.ledger._financial_state()[1].get(attempt) != bound):
                raise unit_recovery.SourceIntegrityError('Reading reservation does not match its request')
        try:
            if self.should_stop and self.should_stop():
                raise model.AttemptCancelled('Reading was cancelled before dispatch')
            if not self.reader.key:
                raise unit_recovery.SourceIntegrityError('No parent reading key is configured')
            payload = args['payload']
            encoded = payload['messages'][0]['content'][1]['image_url']['url']
            prefix = 'data:image/jpeg;base64,'
            if not encoded.startswith(prefix):
                raise unit_recovery.SourceIntegrityError('Unsupported reading image encoding')
            image = base64.b64decode(encoded[len(prefix):], validate=True)
            dispatch = dict(context, operation='marker' if name.startswith('marker-') else 'page',
                image_sha256=hashlib.sha256(image).hexdigest(), reservation=attempt, bound_usd=bound)
            if self.authorizer is None:
                raise unit_recovery.SourceIntegrityError('Reading has no current consent quote')
            self.authorizer.authorize(payload, dispatch)
        except BaseException:
            self.reader._reject_direct(pending, attempt, bound, 'local_pre_dispatch')
            raise
        marker = name.startswith('marker-')
        # Child input never selects an endpoint, header, timeout or credential.
        try:
            self.reader.ledger.record(dict(kind='vision_dispatch', attempt=attempt,
                model=vision_reading.MODEL, page=context['page_index0']), durable=True)
        except BaseException:
            self.reader._reject_direct(pending, attempt, bound, 'local_pre_dispatch')
            raise
        self.dispatched.add(attempt)
        response = self.reader._direct_post(payload, pending, attempt, bound,
            title='CWNG printed note marker' if marker else 'CWNG vision page reading',
            timeout=(30, 120) if marker else (30, 240))
        status = getattr(response, 'status_code', 200)
        data = response.json()
        body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(body) > MAX_RESPONSE_BYTES:
            raise model.UncertainBilling('Reading response exceeds its transport budget')
        cost = data.get('usage', {}).get('cost')
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise model.UncertainBilling('Invalid metered reading charge')
        # Settle known usage before IPC can observe cancellation or child death.
        # Child's same-attempt settlement is idempotent in both ledger stores.
        with self.reader._budget():
            self.reader.ledger.reconcile_attempt(attempt, cost)
        receipt = dict(image_sha256=dispatch['image_sha256'], config_sha256=pending.stem.rsplit('-',1)[1],
            cost_usd=cost, reservation=attempt, response=data)
        temporary = pending.with_suffix('.bridge.tmp')
        temporary.write_text(json.dumps(receipt, ensure_ascii=False))
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, pending)
        return dict(status=status, body=body, headers={'Content-Type':'application/json'})
