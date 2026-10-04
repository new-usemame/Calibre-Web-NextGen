"""Private manual diagnostic observer; never changes the original test assertion."""
from collections import deque
from pathlib import Path
import gc
import hashlib
import importlib.metadata
import inspect
import json
import os
import platform
import sys
import threading
import time

import pytest

TARGET = 'tests/unit/test_request_fanout_gevent_responsiveness.py::TestFanOutKeepsHubResponsive::test_fan_out_yields_to_other_greenlets_while_jobs_run'
PRODUCT_SHA = '846aa4b0e537e7cd9f1f8691547299f11a598dfa'
EXPECTED_SOURCE_HASHES = {'tests/unit/test_request_fanout_gevent_responsiveness.py': '5cb15172535c54722829ab0ccc4e58ab19d57764aac8eb46a1461e171eda656c', 'cps/services/parallel.py': '72ea08e86b5af62918f71d9a9eade533d15cd2c0a3197c25fe27199e46e7e9cc'}
ROOT = Path(os.environ['CWNG_GEVENT_DIAGNOSTIC_DIR'])
WORKER = os.environ.get('PYTEST_XDIST_WORKER', 'controller')
CONTEXT = deque(maxlen=32)
PARENT_EVENT_COUNT = 0
PARENT_EVENT_LIMIT = 60000
PARENT_LIMIT_REACHED = False
INSTRUMENT_FAILURES = []
LIMITS = {'events': 2000, 'stacks': 500, 'frames_per_stack': 32}


def clock():
    return time.monotonic_ns()


def callback_name(fn):
    if fn is None:
        return None
    identity = {'module': getattr(fn, '__module__', type(fn).__module__), 'qualname': getattr(fn, '__qualname__', type(fn).__qualname__)}
    try:
        source = inspect.getsourcefile(fn)
        if source:
            try:
                identity['sourcefile'] = str(Path(source).resolve().relative_to(Path.cwd().resolve()))
            except ValueError:
                identity['sourcefile'] = Path(source).name
        else:
            identity['sourcefile'] = 'MISSING'
    except (TypeError, OSError):
        identity['sourcefile'] = 'UNAVAILABLE'
    return identity


def append(path, data):
    global PARENT_EVENT_COUNT, PARENT_LIMIT_REACHED
    if path == 'parent-test-progress.jsonl':
        if PARENT_EVENT_COUNT >= PARENT_EVENT_LIMIT:
            PARENT_LIMIT_REACHED = True
            return False
        PARENT_EVENT_COUNT += 1
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        with (ROOT / path).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(data, ensure_ascii=True) + '\n')
            stream.flush()
        return True
    except (OSError, TypeError, ValueError) as artifact_error:
        if len(INSTRUMENT_FAILURES) < 200:
            INSTRUMENT_FAILURES.append({'phase': 'artifact_write', 'file': path, 'error_type': type(artifact_error).__name__})
        print('[PRIVATE OBSERVER ARTIFACT FAILURE] ' + type(artifact_error).__name__, file=sys.stderr)
        return False


def thread_state():
    return [{'name': t.name, 'ident': t.ident, 'native_tid': t.native_id, 'daemon': t.daemon} for t in threading.enumerate()]


def linux_metrics(tid):
    if sys.platform != 'linux':
        return {'status': 'MISSING', 'reason': 'Linux counters unavailable on this platform'}
    out = {'sample_monotonic_start_ns': clock()}
    base = Path('/proc/self/task') / str(tid)
    for name in ('schedstat', 'status'):
        try:
            raw = (base / name).read_text()
            if name == 'schedstat':
                cpu, wait, slices = raw.split()[:3]
                out[name] = {'cpu_ns': int(cpu), 'runqueue_wait_ns': int(wait), 'timeslices': int(slices)}
            else:
                out[name] = [v for v in raw.splitlines() if v.startswith(('voluntary_ctxt_switches:', 'nonvoluntary_ctxt_switches:'))]
        except (OSError, ValueError) as error:
            out[name] = {'status': 'MISSING', 'error_type': type(error).__name__}
    try:
        raw = Path('/proc/self/cgroup').read_text()
        unified = next((v.split(':', 2)[2] for v in raw.splitlines() if v.startswith('0::')), None)
        if unified is None:
            out['cgroup'] = {'status': 'MISSING', 'reason': 'No unified cgroup entry; no invented v1 counters'}
        else:
            base = Path('/sys/fs/cgroup') / unified.lstrip('/')
            out['cgroup'] = {}
            for name in ('cpu.stat', 'cpu.max'):
                try:
                    out['cgroup'][name] = (base / name).read_text()
                except OSError as error:
                    out['cgroup'][name] = {'status': 'MISSING', 'error_type': type(error).__name__}
    except OSError as error:
        out['cgroup'] = {'status': 'MISSING', 'error_type': type(error).__name__}
    out['sample_monotonic_end_ns'] = clock()
    return out


def pytest_configure(config):
    append('processes.jsonl', {'kind': 'configure', 'pid': os.getpid(), 'worker': WORKER, 'monotonic_ns': clock(), 'python': sys.version, 'platform': platform.platform(), 'trace': callback_name(sys.gettrace()), 'profile': callback_name(sys.getprofile())})


def pytest_runtest_logstart(nodeid, location):
    data = {'kind': 'start', 'nodeid': nodeid, 'worker': WORKER, 'pid': os.getpid(), 'monotonic_ns': clock()}
    CONTEXT.append(data)
    if WORKER == 'controller':
        append('parent-test-progress.jsonl', data)


def pytest_runtest_logreport(report):
    data = {'kind': 'report', 'nodeid': report.nodeid, 'when': report.when, 'outcome': report.outcome, 'worker': getattr(report, 'worker_id', WORKER), 'pid': os.getpid(), 'monotonic_ns': clock()}
    CONTEXT.append(data)
    if WORKER == 'controller' or report.nodeid == TARGET:
        append('parent-test-progress.jsonl' if WORKER == 'controller' else WORKER + '-reports.jsonl', data)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_call(item):
    if item.nodeid != TARGET:
        return (yield)
    module = item.module
    parallel = module.parallel
    main_ident = threading.get_ident()
    main_tid = threading.get_native_id()
    trace_before = sys.gettrace()
    profile_before = sys.getprofile()
    stopped = threading.Event()
    sampler = None
    events = []
    results = []
    observer_errors = []
    original = {'time': module.time, 'job': module._blocking_job, 'worst': module._worst_stall_while, 'fan_out': parallel.fan_out}
    summary = {'status': 'INCOMPLETE', 'target': TARGET, 'worker': WORKER, 'pid': os.getpid(), 'main_ident': main_ident, 'main_native_tid': main_tid, 'context': list(CONTEXT), 'limits': LIMITS, 'monotonic_start_ns': clock(), 'trace_before': callback_name(trace_before), 'profile_before': callback_name(profile_before), 'threads_before': thread_state(), 'Linux_metrics_before': linux_metrics(main_tid), 'overhead': 'Observer wrappers/20ms native stack+counter sampler/GC callback and artifact writes perturb scheduling; no baseline or runnable delay subtraction'}
    append(WORKER + '-summary.jsonl', summary)

    def emit(kind, **fields):
        if len(events) < LIMITS['events']:
            data = {'kind': kind, 'monotonic_ns': clock(), 'native_tid': threading.get_native_id(), **fields}
            events.append(data)
            try:
                append(WORKER + '-events.jsonl', data)
            except (OSError, ValueError, TypeError) as error:
                observer_errors.append({'phase': 'event_write', 'error_type': type(error).__name__})
        else:
            summary['event_limit_reached'] = True

    def gc_observed(phase, info):
        emit('gc_' + phase, generation=info.get('generation'), collected=info.get('collected'), uncollectable=info.get('uncollectable'))

    def sample_body():
        for _ in range(LIMITS['stacks']):
            if stopped.wait(0.02):
                return
            frame = sys._current_frames().get(main_ident)
            stack = []
            while frame is not None and len(stack) < LIMITS['frames_per_stack']:
                filename = frame.f_code.co_filename
                try:
                    filename = str(Path(filename).relative_to(Path.cwd()))
                except ValueError:
                    filename = Path(filename).name
                stack.append({'file': filename, 'function': frame.f_code.co_name, 'line': frame.f_lineno})
                frame = frame.f_back
            append(WORKER + '-stacks.jsonl', {'monotonic_ns': clock(), 'sampler_native_tid': threading.get_native_id(), 'sampler_thread_cpu_ns': time.thread_time_ns(), 'main_native_tid': main_tid, 'main_Linux_metrics': linux_metrics(main_tid), 'stack': stack})
        summary['sampler_limit_reached'] = True

    def sample():
        try:
            sample_body()
        except BaseException as error:
            observer_errors.append({'phase': 'sampler', 'error_type': type(error).__name__})

    class ObservedTime:
        def monotonic(self):
            value = original['time'].monotonic()
            if sys._getframe(1).f_code.co_name == 'heartbeat':
                emit('heartbeat_original_clock', monotonic=value, main_thread_cpu_ns=time.thread_time_ns(), main_Linux_metrics=linux_metrics(main_tid))
            return value
        def __getattr__(self, name):
            return getattr(original['time'], name)

    def observed_job(value):
        real = original['job'](value)
        def run():
            emit('native_worker_start', expected_value=value)
            try:
                return real()
            finally:
                emit('native_worker_end', expected_value=value)
        return run

    def observed_fan_out(*args, **kwargs):
        emit('fan_out_enter')
        try:
            for key, value in original['fan_out'](*args, **kwargs):
                row = {'key': key, 'value': value.value, 'result_key': value.key, 'exception_type': type(value.exception).__name__ if value.exception is not None else None, 'elapsed_ms': value.elapsed_ms}
                results.append(row)
                emit('actual_result', result=row)
                yield key, value
        finally:
            emit('fan_out_exit')

    def observed_worst(run):
        emit('original_heartbeat_helper_enter')
        worst = original['worst'](run)
        summary['actual_worst_gap_seconds'] = worst
        emit('original_heartbeat_helper_exit', worst=worst)
        return worst

    try:
        import gevent.monkey
        summary['monkey_flags'] = {name: gevent.monkey.is_module_patched(name) for name in ('time', 'threading', '_thread', 'socket', 'select', 'os')}
        summary['versions'] = {}
        for name in ('pytest', 'pytest-xdist', 'pytest-cov', 'coverage', 'gevent', 'greenlet'):
            try:
                summary['versions'][name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                summary['versions'][name] = 'MISSING'
        summary['actual_runtime_bindings'] = {name: callback_name(fn) for name, fn in {'fan_out': original['fan_out'], 'GeventThreadPool': parallel._GeventThreadPool, 'gevent_sleep': getattr(parallel, '_gevent_sleep', None), 'test_blocking_job': original['job'], 'heartbeat_helper': original['worst']}.items()}
        summary['flags'] = {'HAVE_GEVENT_POOL': parallel._HAVE_GEVENT_POOL, 'HAVE_GEVENT_SLEEP': parallel._HAVE_GEVENT_SLEEP}
        summary['source_hashes'] = {str(path.relative_to(Path.cwd())): hashlib.sha256(path.read_bytes()).hexdigest() for path in (Path(module.__file__).resolve(), Path(parallel.__file__).resolve())}
        summary['physical_sources_match_fixed846'] = summary['source_hashes'] == EXPECTED_SOURCE_HASHES
        summary['existing_offload_pool'] = callback_name(type(parallel._OFFLOAD_POOL)) if getattr(parallel, '_OFFLOAD_POOL', None) is not None else None
        summary['actual_test_callable'] = callback_name(item.obj)
        summary['postfixture_constants'] = {'JOB_SECONDS': module._JOB_SECONDS, 'MAX_TOLERABLE_STALL': module._MAX_TOLERABLE_STALL}
        summary['fixed_expected_conditions_match'] = module._JOB_SECONDS == 1.0 and module._MAX_TOLERABLE_STALL == 0.3
        module.time = ObservedTime()
        module._blocking_job = observed_job
        module._worst_stall_while = observed_worst
        parallel.fan_out = observed_fan_out
        gc.callbacks.append(gc_observed)
        summary['sampler_constructor'] = callback_name(threading.Thread)
        if not summary['monkey_flags']['threading'] and not summary['monkey_flags']['_thread']:
            sampler = threading.Thread(target=sample, name='cwng-private-gevent-sampler', daemon=False)
            sampler.start()
        else:
            summary['sampler_context'] = 'MISSING: inherited monkey-patched threading cannot be certified as native; original call still executes unchanged'
        summary['original_call_entered_after_fixtures'] = True
        emit('original_pytest_item_call_after_fixtures')
        try:
            result = yield
        except BaseException as original_error:
            summary['original_call_exception'] = type(original_error).__name__
            summary['status'] = 'ORIGINAL_CALL_FAILED'
            raise
        else:
            summary['original_call_exception'] = None
            summary['status'] = 'ORIGINAL_CALL_PASSED'
            return result
    finally:
        active_primary_error = sys.exception()
        cleanup_errors = []

        def reconcile(label, action):
            try:
                return action()
            except BaseException as cleanup_error:
                cleanup_errors.append({'phase': label, 'error_type': type(cleanup_error).__name__})
                return None

        # Attempt every owned restoration even if an earlier snapshot/write fails.
        reconcile('sampler_stop', stopped.set)
        if sampler is not None and sampler.ident is not None:
            reconcile('sampler_join', lambda: sampler.join(timeout=3))
            summary['sampler_stopped'] = not sampler.is_alive()
        else:
            summary['sampler_stopped'] = True
            summary['sampler_not_started'] = True
        if not summary['sampler_stopped']:
            cleanup_errors.append({'phase': 'sampler_join', 'error_type': 'OwnedSamplerStillAlive'})
        if gc_observed in gc.callbacks:
            reconcile('gc_callback_restore', lambda: gc.callbacks.remove(gc_observed))
        for target, attr, value in [(module, 'time', original['time']),
                                    (module, '_blocking_job', original['job']),
                                    (module, '_worst_stall_while', original['worst']),
                                    (parallel, 'fan_out', original['fan_out'])]:
            reconcile('restore_' + attr, lambda target=target, attr=attr, value=value: setattr(target, attr, value))
        summary['trace_same_object'] = sys.gettrace() is trace_before
        summary['profile_same_object'] = sys.getprofile() is profile_before
        summary['results'] = results
        summary['all_five_results_correct'] = len(results) == 5 and {v['key']: v['value'] for v in results} == {f'p{i}': f'r{i}' for i in range(5)} and all(v['exception_type'] is None and v['result_key'] == v['key'] for v in results)
        summary['Linux_metrics_after'] = reconcile('metrics_after', lambda: linux_metrics(main_tid))
        summary['threads_after'] = reconcile('threads_after', thread_state)
        summary['monotonic_finished_ns'] = clock()
        summary['observer_errors'] = observer_errors
        summary['artifact_failures'] = list(INSTRUMENT_FAILURES)
        summary['cleanup_errors'] = cleanup_errors
        summary['observer_evidence_valid'] = (sampler is not None and not observer_errors and not cleanup_errors and not INSTRUMENT_FAILURES
            and not summary.get('event_limit_reached', False) and not summary.get('sampler_limit_reached', False)
            and summary.get('original_call_entered_after_fixtures', False) and summary['status'] in ('ORIGINAL_CALL_PASSED', 'ORIGINAL_CALL_FAILED')
            and summary.get('physical_sources_match_fixed846', False) and summary.get('fixed_expected_conditions_match', False)
            and summary['sampler_stopped'] and summary['trace_same_object'] and summary['profile_same_object']
            and summary['all_five_results_correct'] and 'actual_worst_gap_seconds' in summary)
        reconcile('final_summary_write', lambda: append(WORKER + '-summary.jsonl', summary))
        if cleanup_errors:
            summary['observer_evidence_valid'] = False
            summary['cleanup_errors'] = cleanup_errors
            # A failed evidence write may also leave only the earlier flushed partial file.
            try:
                append(WORKER + '-observer-cleanup-errors.jsonl', {'errors': cleanup_errors, 'active_primary_error_type': type(active_primary_error).__name__ if active_primary_error is not None else None})
            except BaseException:
                pass
            if active_primary_error is None:
                raise RuntimeError('Independent diagnostic observer cleanup failed; original-call status remains separately recorded')
        # Never replace an active original-call exception with cleanup errors.


def pytest_sessionfinish(session, exitstatus):
    append('processes.jsonl', {'kind': 'sessionfinish', 'pid': os.getpid(), 'worker': WORKER, 'monotonic_ns': clock(), 'actual_pytest_exitstatus': int(exitstatus), 'artifact_failures': list(INSTRUMENT_FAILURES), 'parent_progress_limit_reached': PARENT_LIMIT_REACHED})


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids):
    append('actual-worker-collection.jsonl', {'worker_id': node.gateway.id, 'parent_pid': os.getpid(), 'monotonic_ns': clock(), 'collected_count': len(ids), 'nodeids_sha256': hashlib.sha256(('\n'.join(ids)+'\n').encode()).hexdigest()})


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    append('actual-worker-down.jsonl', {'worker_id': node.gateway.id, 'parent_pid': os.getpid(), 'monotonic_ns': clock(), 'error_type': type(error).__name__ if error is not None else None, 'unexpected': error is not None})
