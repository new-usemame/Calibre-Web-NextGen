"""Content changes cannot hide behind equal filesystem identity timestamps."""
from pathlib import Path

import pytest

from cps.services.reflow import ocr
from tests.unit.test_reflow_engine_probe_cache import engine


def _freeze_stats(monkeypatch, executable, data):
    paths = [executable, data / 'eng.traineddata', data / 'osd.traineddata']
    original = ocr._identity_stat
    stamps = {str(p.resolve()): original(p) for p in paths}
    monkeypatch.setattr(ocr, '_identity_stat',
                        lambda path: stamps.get(str(Path(path).resolve()), original(path)))


@pytest.mark.parametrize('changed', ['executable', 'language', 'orientation'])
def test_equal_stat_changed_bytes_invalidate_success(engine, monkeypatch, changed):
    executable, data, calls = engine
    _freeze_stats(monkeypatch, executable, data)
    first = ocr._engine('eng')
    path = executable if changed == 'executable' else data / (
        'eng.traineddata' if changed == 'language' else 'osd.traineddata')
    before = path.read_bytes()
    replacement = b'engine-two' if changed == 'executable' else b'new'
    assert len(before) == len(replacement) and before != replacement
    path.write_bytes(replacement)
    second = ocr._engine('eng')
    assert calls.count('--version') == 2
    assert calls.count('--list-langs') == 2
    assert second[2] != first[2]
    def forbidden(*args, **kwargs):
        raise AssertionError('unchanged content must reuse the successful probe')
    monkeypatch.setattr(ocr.subprocess, 'run', forbidden)
    assert ocr._engine('eng') == second


def test_equal_stat_engine_change_during_probe_is_unavailable(engine, monkeypatch):
    executable, data, calls = engine
    _freeze_stats(monkeypatch, executable, data)
    original_run = ocr.subprocess.run
    def changed_during_probe(command, **kwargs):
        result = original_run(command, **kwargs)
        if command[-1] == '--version':
            executable.write_bytes(b'engine-two')
        return result
    monkeypatch.setattr(ocr.subprocess, 'run', changed_during_probe)
    with pytest.raises(ocr.OCRUnavailable):
        ocr._engine('eng')
    assert len(ocr._ENGINE_IDENTITIES) == 0
