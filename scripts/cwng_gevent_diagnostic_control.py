from pathlib import Path
import datetime, hashlib, importlib.metadata, importlib.util, json, os, platform, signal, sys, threading, time, traceback

P = Path(os.environ['CWNG_GEVENT_DIAGNOSTIC_DIR']) / 'stdlib-control'
P.mkdir(parents=True, exist_ok=True)
W = Path.cwd()
H = '846aa4b0e537e7cd9f1f8691547299f11a598dfa'
mode = 'stdlib'
assert mode in ('gevent', 'stdlib')
record = {'status': 'FAILED', 'mode': mode, 'head': H, 'pid': os.getpid(), 'platform': platform.platform(), 'started': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'events': [], 'heartbeat': [], 'stacks': [], 'results': [], 'Linux_metrics': {'status': 'NOT_SAMPLED'} }
main_ident = threading.get_ident()
main_tid = threading.get_native_id()
record['main_native_tid'] = main_tid
sampler = None
record['context'] = 'Separate fresh child sensitivity control after full original pytest; not in-suite inherited state'
stop_event = threading.Event()
old_profile = sys.getprofile()
originals = {}


def emit(kind, **fields):
    record['events'].append({'kind': kind, 'monotonic_ns': time.monotonic_ns(), 'native_tid': threading.get_native_id(), **fields})


def linux_metrics():
    if sys.platform != 'linux':
        return {'status': 'MISSING', 'reason': 'Linux schedstat/cgroup files unavailable on this platform'}
    out = {}
    paths = {'main_schedstat': Path('/proc/self/task') / str(main_tid) / 'schedstat', 'main_status': Path('/proc/self/task') / str(main_tid) / 'status', 'cgroup': Path('/proc/self/cgroup')}
    for key, path in paths.items():
        try:
            raw = path.read_text()
            if key == 'main_schedstat':
                fields = raw.split()
                out[key] = {'cpu_ns': int(fields[0]), 'runqueue_wait_ns': int(fields[1]), 'timeslices': int(fields[2]), 'raw': raw}
            elif key == 'main_status':
                out[key] = [line for line in raw.splitlines() if line.startswith(('voluntary_ctxt_switches:', 'nonvoluntary_ctxt_switches:'))]
            else:
                out[key] = raw
                unified = next((line.split(':', 2)[2] for line in raw.splitlines() if line.startswith('0::')), None)
                if unified is not None:
                    cpu = Path('/sys/fs/cgroup') / unified.lstrip('/') / 'cpu.stat'
                    try:
                        out['cgroup_cpu_stat'] = {'path': str(cpu), 'raw': cpu.read_text()}
                    except OSError as error:
                        out['cgroup_cpu_stat'] = {'status': 'MISSING', 'error': repr(error)}
                else:
                    out['cgroup_cpu_stat'] = {'status': 'MISSING', 'reason': 'No unified cgroup entry; no fabricated v1 metric'}
        except (OSError, ValueError, IndexError) as error:
            out[key] = {'status': 'MISSING', 'error': repr(error)}
    return out


def sample():
    while not stop_event.wait(0.02):
        if len(record['stacks']) >= 500:
            record['sampler_limit_reached'] = True
            return
        frame = sys._current_frames().get(main_ident)
        stack = []
        while frame is not None and len(stack) < 32:
            stack.append({'file': frame.f_code.co_filename, 'function': frame.f_code.co_name, 'line': frame.f_lineno})
            frame = frame.f_back
        record['stacks'].append({'monotonic_ns': time.monotonic_ns(), 'sampler_native_tid': threading.get_native_id(), 'sampler_thread_cpu_ns': time.thread_time_ns(), 'main_native_tid': main_tid, 'stack': stack, 'Linux_metrics': linux_metrics()})


def controlled_stop(signum, frame):
    raise InterruptedError('controlled signal %s' % signum)


for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, controlled_stop)

try:
    source = W / 'tests/unit/test_request_fanout_gevent_responsiveness.py'
    spec = importlib.util.spec_from_file_location('private_exact_fanout_test', source)
    test = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(test)
    parallel = test.parallel
    import gevent, gevent.monkey, greenlet
    record['versions'] = {name: importlib.metadata.version(name) for name in ('gevent', 'greenlet', 'pytest', 'coverage')}
    record['python'] = sys.version
    record['source_sha256'] = {str(path.relative_to(W)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (source, W / 'cps/services/parallel.py')}
    record['monkey_flags'] = {name: gevent.monkey.is_module_patched(name) for name in ('time', 'threading', 'socket', 'select', 'os')}
    assert not record['monkey_flags']['time'] and not record['monkey_flags']['threading'], 'Diagnostic requires the actual unpatched test conditions'
    record['flags_original'] = {'pool': parallel._HAVE_GEVENT_POOL, 'sleep': parallel._HAVE_GEVENT_SLEEP}
    assert parallel._HAVE_GEVENT_POOL
    assert test._JOB_SECONDS == 1.0 and test._MAX_TOLERABLE_STALL == 0.3
    record['threads_before'] = [{'name': t.name, 'ident': t.ident, 'native_id': t.native_id, 'daemon': t.daemon} for t in threading.enumerate()]
    record['greenlet_before'] = {'type': type(gevent.getcurrent()).__name__, 'hub_type': type(gevent.get_hub()).__name__}
    record['idle_context_worst'] = test._worst_stall_while(lambda: gevent.sleep(0.1))
    record['idle_is_context_only_not_subtracted'] = True
    originals = {'time': test.time, 'job': test._blocking_job, 'worst': test._worst_stall_while, 'fan_out': parallel.fan_out, 'pool_flag': parallel._HAVE_GEVENT_POOL}

    class ObservedTime:
        def monotonic(self):
            value = time.monotonic()
            if sys._getframe(1).f_code.co_name == 'heartbeat':
                record['heartbeat'].append({'monotonic': value, 'main_thread_cpu_ns': time.thread_time_ns(), 'Linux_metrics': linux_metrics()})
            return value
        def __getattr__(self, name):
            return getattr(time, name)

    def observed_job(value):
        actual_job = originals['job'](value)
        def run():
            emit('worker_start', value=value)
            try:
                return actual_job()
            finally:
                emit('worker_end', value=value)
        return run

    def observed_fan_out(*args, **kwargs):
        emit('fan_out_enter')
        try:
            for key, result in originals['fan_out'](*args, **kwargs):
                record['results'].append({'key': key, 'result_key': result.key, 'value': result.value, 'exception': type(result.exception).__name__ if result.exception is not None else None, 'elapsed_ms': result.elapsed_ms})
                emit('result_consumed', key=key)
                yield key, result
        finally:
            emit('fan_out_exit')

    def observed_worst(run):
        emit('original_heartbeat_helper_enter')
        value = originals['worst'](run)
        record['worst_gap_seconds'] = value
        emit('original_heartbeat_helper_exit', worst=value)
        return value

    test.time = ObservedTime()
    test._blocking_job = observed_job
    test._worst_stall_while = observed_worst
    parallel.fan_out = observed_fan_out
    parallel._HAVE_GEVENT_POOL = mode == 'gevent'
    record['Linux_metrics'] = {'before': linux_metrics()}
    sampler = threading.Thread(target=sample, name='private-fanout-stack-sampler', daemon=False)
    sampler.start()
    record['profile_observation'] = 'No profile callback installed; preexisting callback preserved'
    try:
        test.TestFanOutKeepsHubResponsive().test_fan_out_yields_to_other_greenlets_while_jobs_run()
        record['original_test_outcome'] = 'PASS'
    except AssertionError as error:
        record['original_test_outcome'] = 'ASSERTION_FAILED'
        record['original_test_error'] = str(error)
        record['original_test_traceback'] = traceback.format_exc()
    finally:
        record['profile_same_object_after_call'] = sys.getprofile() is old_profile
    expected = {f'p{i}': f'r{i}' for i in range(5)}
    got = {r['key']: r['value'] for r in record['results']}
    record['all_five_results_correct'] = len(record['results']) == 5 and got == expected and all(r['exception'] is None and r['result_key'] == r['key'] for r in record['results'])
    assert record['all_five_results_correct'], 'Setup/runtime/result failure cannot be a meaningful expected RED'
    worst = record['worst_gap_seconds']
    if mode == 'gevent':
        assert record['original_test_outcome'] == 'PASS' and worst < 0.3
        record['status'] = 'LOCAL_INSTRUMENTED_PASS'
    else:
        assert record['original_test_outcome'] == 'ASSERTION_FAILED' and worst >= 0.3 and record['original_test_error'].startswith('fan_out froze the gevent hub')
        record['status'] = 'MEANINGFUL_STDLIB_EXPECTED_RED'
    record['Linux_metrics']['after'] = linux_metrics()
except BaseException as error:
    record['status'] = 'FAILED'
    record['error'] = repr(error)
    record['traceback'] = traceback.format_exc()
finally:
    record['profile_same_object_finally'] = sys.getprofile() is old_profile
    stop_event.set()
    if sampler is not None:
        sampler.join(timeout=3)
        record['sampler_stopped'] = not sampler.is_alive()
        if sampler.is_alive():
            record['status'] = 'FAILED'
    if originals:
        test.time = originals['time']
        test._blocking_job = originals['job']
        test._worst_stall_while = originals['worst']
        parallel.fan_out = originals['fan_out']
        parallel._HAVE_GEVENT_POOL = originals['pool_flag']
    record['threads_after'] = [{'name': t.name, 'ident': t.ident, 'native_id': t.native_id, 'daemon': t.daemon} for t in threading.enumerate()]
    record['finished'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    (P / (mode + '-actual.json')).write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'status': record['status'], 'mode': mode, 'pid': os.getpid()}))
raise SystemExit(0 if record['status'] in ('LOCAL_INSTRUMENTED_PASS', 'MEANINGFUL_STDLIB_EXPECTED_RED') else 1)
