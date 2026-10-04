# SPDX-License-Identifier: GPL-3.0-or-later
"""The real service wrapper must give large PDFs more conversion time."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import importlib.util
import time

from pypdf import PdfWriter
import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / 'root/etc/s6-overlay/s6-rc.d/cwa-ingest-service/run'


def run_service(tmp_path, source, budget, *, helper=None, complete_pdf=None, event_mode=None, expected_exit=0):
    watch = tmp_path / 'watch'
    watch.mkdir(exist_ok=True)
    binaries = tmp_path / 'bin'
    binaries.mkdir(exist_ok=True)
    output = tmp_path / 'calls.json'
    # Observe the arguments and deadline passed by the real shell wrapper;
    # do not actually wait hours or invoke a conversion.
    timeout = binaries / 'timeout'
    timeout.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ['TEST_CALLS']).write_text(json.dumps({
    'timeout': int(sys.argv[1]), 'file': sys.argv[-1],
    'deadline': os.environ.get('CWA_CONVERSION_DEADLINE_SECONDS'),
    'readiness': os.environ.get('CWA_INGEST_READINESS_TIMEOUT_SECONDS')}))
''')
    timeout.chmod(0o755)
    if complete_pdf is not None:
        lsof = binaries / 'lsof'
        lsof.write_text('''#!/usr/bin/env python3
import os, sys
from pathlib import Path
record = Path(os.environ['TEST_WRITER_PROBES'])
first = not record.exists()
record.write_text(record.read_text() + 'probe\\n' if record.exists() else 'probe\\n')
if first:
    Path(sys.argv[-1]).write_bytes(Path(os.environ['TEST_COMPLETE_PDF']).read_bytes())
    print('aw')
''')
        lsof.chmod(0o755)
    env = dict(os.environ, PATH=str(binaries) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
               WATCH_FOLDER=str(watch), CWA_INGEST_SERVICE_TEST_MODE='1',
               CWA_INGEST_RETRY_QUEUE=str(tmp_path / 'queue'),
               CWA_INGEST_STATUS_FILE=str(tmp_path / 'status'),
               CWA_INGEST_PROCESSING_DIR=str(tmp_path / 'processing'),
               CWA_INGEST_RECENT_DIR=str(tmp_path / 'recent'),
               CWA_INGEST_PROCESSOR_CMD='/owned-observation-only',
               CWA_INGEST_BUDGET_HELPER=str(helper or ROOT / 'scripts/ingest_budget.py'),
               TEST_CALLS=str(output))
    if complete_pdf is not None:
        env.update(TEST_WRITER_PROBES=str(tmp_path / 'writer-probes'),
                   TEST_COMPLETE_PDF=str(complete_pdf))
    env.update(CWA_INGEST_BATCH_DIRTY_FILE=str(tmp_path / 'batch-dirty'),
               CWA_INGEST_BATCH_LAST_SUCCESS_FILE=str(tmp_path / 'batch-success'))
    command = 'source "$1" >/dev/null; run_processor_with_timeout "$2" "$3"'
    if event_mode is not None:
        command = '''source "$1" >/dev/null
get_timeout_from_db() { echo 900; }
cleanup_stale_temps() { :; }
is_recent_duplicate_event() { return 1; }
has_live_processing_marker() { return 1; }
mark_recent_path() { :; }
maybe_run_post_batch_follow_up() { :; }
'''
        if event_mode == 'moved':
            command += 'handle_event "$3" MOVED_TO'
        else:
            command += 'printf "%s\\n" "$3" > "$QUEUE_FILE"; process_retry_queue all'
            if complete_pdf is not None:
                command += '; process_retry_queue all'
    result = subprocess.run(['bash', '-c',
                             command,
                             'test', str(SERVICE), str(budget), str(source)],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == expected_exit, result.stderr
    if expected_exit:
        return {'source_present': source.exists(), 'processor_started': output.exists()}
    return json.loads(output.read_text())


def pdf(tmp_path, pages):
    path = tmp_path / 'Large book; literal $(touch injected).PDF'
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    writer.write(path)
    return path


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux address-space limit required; unsupported hosts keep configured budget')
def test_reported_2643_page_pdf_gets_scaled_watchdog_and_matching_deadline(tmp_path):
    source = pdf(tmp_path, 2643)
    record = run_service(tmp_path, source, 2700)
    assert record['timeout'] == 14273  # ceiling(2700 * 2643 / 500)
    assert int(record['deadline']) == 12846
    assert record['file'] == str(source)
    assert not (tmp_path / 'injected').exists()


@pytest.mark.parametrize('pages', [3, 500])
def test_short_pdf_keeps_existing_budget(tmp_path, pages):
    record = run_service(tmp_path, pdf(tmp_path, pages), 2700)
    assert record['timeout'] == 2700
    assert int(record['deadline']) == 2430


@pytest.mark.parametrize('suffix', ['.txt', '.mobi', '.pdf'])
def test_unknown_or_invalid_page_count_keeps_existing_budget(tmp_path, suffix):
    source = tmp_path / ('unpaged' + suffix)
    source.write_bytes(b'not a PDF; preserve the original ingest path')
    record = run_service(tmp_path, source, 2700)
    assert record['timeout'] == 2700
    assert int(record['deadline']) == 2430


def test_zero_keeps_explicit_unlimited_setting(tmp_path):
    record = run_service(tmp_path, pdf(tmp_path, 600), 0)
    assert record['timeout'] == 0
    assert record['deadline'] is None


def load_budget():
    spec = importlib.util.spec_from_file_location('ingest_budget', ROOT / 'scripts/ingest_budget.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_automatic_extension_caps_without_shortening_owner_override():
    module = load_budget()
    assert module.scaled_budget(2700, 10000) == 43200
    assert module.scaled_budget(72000, 10000) == 72000
    assert module.scaled_budget(2700, None) == 2700


def test_slow_page_probe_is_killed_reaped_and_keeps_base_budget(tmp_path, monkeypatch):
    module = load_budget()
    worker = tmp_path / 'slow-parser.py'
    pid_record = tmp_path / 'owned-child.pid'
    worker.write_text('import os, time\nfrom pathlib import Path\n'
                      f'Path({str(pid_record)!r}).write_text(str(os.getpid()))\n'
                      'time.sleep(20)\n')
    monkeypatch.setattr(module, 'WORKER', worker)
    monkeypatch.setattr(module, 'PDF_PROBE_TIMEOUT_SECONDS', 1)
    start = time.monotonic()
    pages = module.pdf_page_count(tmp_path / 'large.pdf')
    assert pages is None
    assert module.scaled_budget(2700, pages) == 2700
    assert time.monotonic() - start < 5
    child = int(pid_record.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_encrypted_pdf_is_not_rewritten_or_given_an_invented_page_count(tmp_path):
    source = tmp_path / 'encrypted.pdf'
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt('private-fixture-only')
    writer.write(source)
    original = source.read_bytes()
    module = load_budget()
    assert module.pdf_page_count(source) is None
    assert source.read_bytes() == original


def test_failed_budget_helper_preserves_finite_deadline(tmp_path):
    # A missing optional helper must not turn a configured timeout into 0.
    record = run_service(tmp_path, tmp_path / 'vanished.pdf', 2700,
                         helper=tmp_path / 'missing-budget-helper.py')
    assert record['timeout'] == 2700
    assert int(record['deadline']) == 2430


@pytest.mark.skipif(sys.platform != 'linux', reason='Actual bounded PDF counter requires Linux resource limits')
@pytest.mark.parametrize('event_mode', ['moved', 'retry'])
def test_page_budget_is_selected_after_writer_finishes_for_both_entry_paths(tmp_path, event_mode):
    complete = pdf(tmp_path, 600)
    source = tmp_path / 'moved-before-writer-close.pdf'
    source.write_bytes(b'%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n')
    record = run_service(tmp_path, source, 2700, complete_pdf=complete, event_mode=event_mode)
    assert record['timeout'] == 3240, 'The complete 600-page input must select its budget after readiness'
    assert int(record['deadline']) == 2916
    assert source.read_bytes() == complete.read_bytes()


def test_readwrite_descriptor_is_not_ready_until_closed(tmp_path, monkeypatch):
    module = load_budget()
    source = tmp_path / 'writer.pdf'
    source.write_bytes(b'owned fixture')
    outputs = iter(['au\n', 'ar\n'])
    calls = []
    def observe(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0, stdout=next(outputs))
    monkeypatch.setattr(module.subprocess, 'run', observe)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    assert module.wait_for_file_ready(source, 10)
    assert len(calls) == 2
    assert all(call[1]['timeout'] == 10 for call in calls)


@pytest.mark.skipif(shutil.which('lsof') is None, reason='Native lsof unavailable; root-owned Linux-image gate required (2026-10-03)')
@pytest.mark.parametrize('mode', ['w', 'r+'])
def test_real_open_writer_is_detected_and_closed_writer_is_ready(tmp_path, mode):
    module = load_budget()
    source = tmp_path / 'native-writer.pdf'
    source.write_bytes(b'owned fixture')
    with source.open(mode) as writer:
        assert writer.fileno() >= 0
        assert module.wait_for_file_ready(source, 0) is False
    assert module.wait_for_file_ready(source, 0) is True


@pytest.mark.parametrize('descriptor, ready', [('', True), ('aw\n', False), ('au\n', False)])
def test_zero_readiness_allowance_checks_once_without_waiting(tmp_path, monkeypatch, descriptor, ready):
    module = load_budget()
    source = tmp_path / 'unlimited.pdf'
    source.write_bytes(b'original fixture')
    calls = []
    def observe(*args, **kwargs):
        calls.append(args[0])
        return subprocess.CompletedProcess(args[0], 0, stdout=descriptor)
    monkeypatch.setattr(module.subprocess, 'run', observe)
    def forbidden_sleep(_):
        raise AssertionError('zero readiness allowance waited')
    monkeypatch.setattr(module.time, 'sleep', forbidden_sleep)
    assert module.wait_for_file_ready(source, 0) is ready
    assert len(calls) == 1


def test_busy_pdf_retains_configured_wait_and_requests_retry_without_counting(tmp_path, monkeypatch):
    module = load_budget()
    source = tmp_path / 'still-writing.pdf'
    source.write_bytes(b'original partial bytes')
    ticks = iter([0, 0, .5, 1.01])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, stdout='aw\n'))
    def forbidden_count(_):
        raise AssertionError('page counting started before the writer closed')
    monkeypatch.setattr(module, 'pdf_page_count', forbidden_count)
    monkeypatch.setattr(sys, 'argv', ['ingest_budget.py', '3', str(source)])
    assert module.main() == module.NOT_READY_EXIT
    assert source.read_bytes() == b'original partial bytes'


def test_not_ready_helper_status_keeps_source_and_does_not_start_processor(tmp_path):
    source = tmp_path / 'pending.pdf'
    source.write_bytes(b'original still copying')
    helper = tmp_path / 'not-ready.py'
    helper.write_text('raise SystemExit(75)\n')
    record = run_service(tmp_path, source, 2700, helper=helper, expected_exit=2)
    assert record == {'source_present': True, 'processor_started': False}


def test_retry_uses_immediate_readiness_in_helper_and_processor(tmp_path, monkeypatch):
    source = tmp_path / 'retry.pdf'
    source.write_bytes(b'owned fixture')
    arguments = tmp_path / 'helper-arguments.json'
    helper = tmp_path / 'observe-readiness.py'
    helper.write_text('import json,sys\nfrom pathlib import Path\n'
                      + f'Path({str(arguments)!r}).write_text(json.dumps(sys.argv))\n'
                      + 'print(sys.argv[1])\n')
    monkeypatch.setenv('CWA_INGEST_READINESS_TIMEOUT_SECONDS', '999')
    record = run_service(tmp_path, source, 2700, helper=helper, event_mode='retry')
    assert json.loads(arguments.read_text())[3:] == ['0']
    assert record['readiness'] == '0'
    assert os.environ['CWA_INGEST_READINESS_TIMEOUT_SECONDS'] == '999'


def test_vanished_pdf_does_not_enter_counting(tmp_path, monkeypatch):
    module = load_budget()
    source = tmp_path / 'vanished.pdf'
    monkeypatch.setattr(sys, 'argv', ['ingest_budget.py', '2700', str(source)])
    assert module.main() == module.NOT_READY_EXIT
