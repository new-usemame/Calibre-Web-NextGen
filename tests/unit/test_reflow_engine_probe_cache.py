"""Successful OCR prerequisite reuse must preserve receipt identity and invalidation."""
import subprocess
from types import SimpleNamespace

import pytest
from cps.services.reflow import ocr


@pytest.fixture
def engine(tmp_path, monkeypatch):
    executable = tmp_path / 'tesseract'
    executable.write_bytes(b'engine-one')
    data = tmp_path / 'tessdata'
    data.mkdir()
    for language in ('eng', 'fra', 'osd'):
        (data / (language + '.traineddata')).write_bytes(language.encode())
    monkeypatch.setattr(ocr, '_ENGINE_IDENTITIES', {}, raising=False)
    monkeypatch.setattr(ocr.shutil, 'which', lambda name: str(executable))
    monkeypatch.setenv('TESSDATA_PREFIX', str(data))
    calls = []
    def run(command, **kwargs):
        calls.append(command[-1])
        return SimpleNamespace(stdout='tesseract test\n' if command[-1] == '--version' else
                               f'List of available languages in "{data}" (3):\neng\nfra\nosd\n')
    monkeypatch.setattr(ocr.subprocess, 'run', run)
    return executable, data, calls


def test_successful_identity_avoids_repeated_prerequisite_processes(engine, monkeypatch):
    first = ocr._engine('eng')
    def unavailable(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], 10)
    monkeypatch.setattr(ocr.subprocess, 'run', unavailable)
    assert ocr._engine('eng') == first
    assert engine[2] == ['--version', '--list-langs']


@pytest.mark.parametrize('change', ['language', 'executable', 'traineddata', 'osd', 'prefix', 'environment'])
def test_identity_changes_reprobe_and_never_accept_stale_receipt(engine, monkeypatch, tmp_path, change):
    executable, data, calls = engine
    first = ocr._engine('eng')
    language = 'eng'
    if change == 'language': language = 'fra'
    elif change == 'environment': monkeypatch.setenv('LC_ALL', 'C')
    elif change == 'executable': executable.write_bytes(b'engine-two')
    elif change in ('traineddata', 'osd'):
        (data / ('eng.traineddata' if change == 'traineddata' else 'osd.traineddata')).write_bytes(b'changed')
    else: monkeypatch.setenv('TESSDATA_PREFIX', str(tmp_path / 'missing'))
    if change == 'prefix':
        with pytest.raises(ocr.OCRUnavailable): ocr._engine(language)
    else:
        second = ocr._engine(language)
        if change in ('language', 'traineddata', 'osd'): assert second[2] != first[2]
    assert calls.count('--version') == 2


def test_failed_probe_or_removed_language_is_not_cached(engine, monkeypatch):
    real = ocr.subprocess.run
    def unavailable(*a, **kw): raise subprocess.TimeoutExpired(a[0], 10)
    monkeypatch.setattr(ocr.subprocess, 'run', unavailable)
    with pytest.raises(ocr.OCRUnavailable, match='unavailable'): ocr._engine('eng')
    monkeypatch.setattr(ocr.subprocess, 'run', real)
    ocr._engine('eng')
    (engine[1] / 'eng.traineddata').unlink()
    with pytest.raises(ocr.OCRUnavailable): ocr._engine('eng')


def test_actual_receipt_reuse_needs_no_new_engine_process(tmp_path, monkeypatch):
    import hashlib
    import shutil
    from tests.unit.test_reflow_ocr_source_contract import _scan
    if not shutil.which('tesseract'): pytest.skip('needs installed OCR engine')
    monkeypatch.setattr(ocr, '_ENGINE_IDENTITIES', {})
    with _scan() as document:
        fingerprint = hashlib.sha256(document.tobytes()).hexdigest()
        first = ocr.recognize_page(document[0], source_sha256=fingerprint, cache_dir=tmp_path)
        def forbidden(*args, **kwargs): raise AssertionError('cached page must not launch a process')
        monkeypatch.setattr(ocr.subprocess, 'run', forbidden)
        monkeypatch.setattr(ocr, '_invoke', forbidden)
        second = ocr.recognize_page(document[0], source_sha256=fingerprint, cache_dir=tmp_path)
        assert second == first and second.reused


def test_language_symlink_retarget_invalidates_success(engine):
    _, data, calls = engine
    language = data / 'eng.traineddata'
    first_target = data / 'first-data'
    language.rename(first_target)
    language.symlink_to(first_target)
    first = ocr._engine('eng')
    second_target = data / 'second-data'
    second_target.write_bytes(b'new-language-model')
    language.unlink()
    language.symlink_to(second_target)
    second = ocr._engine('eng')
    assert second[2] != first[2]
    assert calls.count('--version') == 2
