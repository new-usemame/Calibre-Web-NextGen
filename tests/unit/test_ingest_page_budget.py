# SPDX-License-Identifier: GPL-3.0-or-later
"""The real service wrapper must give large PDFs more conversion time."""
import json
import os
from pathlib import Path
import subprocess
import sys
import importlib.util
import time

from pypdf import PdfWriter
import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / 'root/etc/s6-overlay/s6-rc.d/cwa-ingest-service/run'


def run_service(tmp_path, source, budget, *, helper=None):
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
    'deadline': os.environ.get('CWA_CONVERSION_DEADLINE_SECONDS')}))
''')
    timeout.chmod(0o755)
    env = dict(os.environ, PATH=str(binaries) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
               WATCH_FOLDER=str(watch), CWA_INGEST_SERVICE_TEST_MODE='1',
               CWA_INGEST_RETRY_QUEUE=str(tmp_path / 'queue'),
               CWA_INGEST_STATUS_FILE=str(tmp_path / 'status'),
               CWA_INGEST_PROCESSING_DIR=str(tmp_path / 'processing'),
               CWA_INGEST_RECENT_DIR=str(tmp_path / 'recent'),
               CWA_INGEST_PROCESSOR_CMD='/owned-observation-only',
               CWA_INGEST_BUDGET_HELPER=str(helper or ROOT / 'scripts/ingest_budget.py'),
               TEST_CALLS=str(output))
    result = subprocess.run(['bash', '-c',
                             'source "$1" >/dev/null; run_processor_with_timeout "$2" "$3"',
                             'test', str(SERVICE), str(budget), str(source)],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
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
