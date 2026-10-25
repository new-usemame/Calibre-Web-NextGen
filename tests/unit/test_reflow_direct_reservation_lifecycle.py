"""Synthetic images and injected filesystem failures exercise request accounting."""
import io
import json
from pathlib import Path

import pytest
import requests
from PIL import Image

from cps.services.reflow import model, vision_reading
from cps.services.reflow.ledger import Ledger

pytestmark = pytest.mark.unit


def _image():
    buffer = io.BytesIO()
    Image.new('RGB', (24, 32), 'white').save(buffer, format='JPEG')
    return buffer.getvalue()


class _Rejected:
    status_code = 400
    headers = {}

    def json(self):
        return {'error': {'message': 'Synthetic rejection'}}


@pytest.mark.parametrize('method', ['read', 'read_marker'])
@pytest.mark.parametrize('failure', ['pending_write', 'payload_encoding', 'authorization_header'])
def test_local_failure_before_http_releases_reservation(tmp_path, monkeypatch, method, failure):
    ledger = Ledger(tmp_path / 'ledger.jsonl', .1)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', ledger)
    calls = []
    monkeypatch.setattr(requests, 'post', lambda *a, **k: calls.append(k) or _Rejected())
    original = Path.write_text

    def write(path, *args, **kwargs):
        if path.suffix == '.pending':
            # A partial receipt cannot make a local failure ambiguous.
            original(path, '{')
            raise OSError('Synthetic pending write failure')
        return original(path, *args, **kwargs)

    def encoding(*args, **kwargs):
        raise ValueError('Synthetic encoding failure')

    monkeypatch.setattr(Path, 'write_text', write if failure == 'pending_write' else original)
    if failure == 'payload_encoding':
        monkeypatch.setattr(vision_reading.base64, 'b64encode', encoding)
    if failure == 'authorization_header':
        reader.key = ['invalid-header-value']
    with pytest.raises((OSError, ValueError, TypeError)):
        getattr(reader, method)(_image())
    reopened = Ledger(ledger.path, .1)
    assert calls == []
    assert reopened.pending_usd() == reopened.spent() == 0
    assert len([e for e in reopened.entries('reservation') if e['event'] == 'released']) == 1
    assert not list(reader.cache.glob('*.pending'))


@pytest.mark.parametrize('method', ['read', 'read_marker'])
@pytest.mark.parametrize('after_release', [False, True])
def test_rejected_receipt_completes_release_once_after_restart(tmp_path, monkeypatch, method, after_release):
    ledger = Ledger(tmp_path / 'ledger.jsonl', .1)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', ledger)
    calls = []
    monkeypatch.setattr(requests, 'post', lambda *a, **k: calls.append(k) or _Rejected())
    release = Ledger.release_attempt

    def interrupted(current, *args, **kwargs):
        if after_release:
            release(current, *args, **kwargs)
        raise OSError('Synthetic interruption during rejection settlement')

    monkeypatch.setattr(Ledger, 'release_attempt', interrupted)
    with pytest.raises(OSError):
        getattr(reader, method)(_image())
    terminal = list(reader.cache.glob('*.rejected'))
    assert len(terminal) == 1
    receipt = json.loads(terminal[0].read_text())
    assert receipt['status'] == 'REJECTED_UNBILLED'
    assert receipt['reservation']
    monkeypatch.setattr(Ledger, 'release_attempt', release)
    for _ in range(2):
        fresh = vision_reading.Reader('inert-key', reader.cache, Ledger(ledger.path, .1))
        with pytest.raises(model.ModelError, match='rejected unbilled'):
            getattr(fresh, method)(_image())
        reopened = Ledger(ledger.path, .1)
        assert reopened.pending_usd() == reopened.spent() == 0
        assert len([e for e in reopened.entries('reservation') if e['event'] == 'released']) == 1
        assert not list(reader.cache.glob('*.pending'))
    assert len(calls) == 1


@pytest.mark.parametrize('method', ['read', 'read_marker'])
def test_pending_and_rejection_writes_can_both_fail_before_dispatch(tmp_path, monkeypatch, method):
    ledger = Ledger(tmp_path / 'ledger.jsonl', .1)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', ledger)
    calls = []
    monkeypatch.setattr(requests, 'post', lambda *a, **k: calls.append(k) or _Rejected())

    def fail(*args, **kwargs):
        raise OSError('Synthetic unwritable request receipts')

    monkeypatch.setattr(Path, 'write_text', fail)
    with pytest.raises(OSError):
        getattr(reader, method)(_image())
    assert calls == []
    assert Ledger(ledger.path, .1).pending_usd() == 0


@pytest.mark.parametrize('method', ['read', 'read_marker'])
def test_reservation_flush_failure_is_local_and_unbilled(tmp_path, monkeypatch, method):
    ledger = Ledger(tmp_path / 'ledger.jsonl', .1)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', ledger)
    calls = []
    monkeypatch.setattr(requests, 'post', lambda *a, **k: calls.append(k) or _Rejected())
    fsync = vision_reading.os.fsync
    attempts = []

    def fail_once(fd):
        attempts.append(fd)
        if len(attempts) == 1:
            raise OSError('Synthetic reservation flush failure')
        return fsync(fd)

    monkeypatch.setattr(vision_reading.os, 'fsync', fail_once)
    with pytest.raises(OSError):
        getattr(reader, method)(_image())
    assert calls == []
    assert Ledger(ledger.path, .1).pending_usd() == 0


@pytest.mark.parametrize('method', ['read', 'read_marker'])
@pytest.mark.parametrize('contradiction', ['bound', 'label', 'billed'])
def test_rejected_receipt_never_releases_inconsistent_or_billed_evidence(tmp_path, monkeypatch, method, contradiction):
    ledger = Ledger(tmp_path / 'ledger.jsonl', .1)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', ledger)
    calls = []
    monkeypatch.setattr(requests, 'post', lambda *a, **k: calls.append(k) or _Rejected())
    release = Ledger.release_attempt

    def interrupted(*args, **kwargs):
        raise OSError('Synthetic interruption before release')

    monkeypatch.setattr(Ledger, 'release_attempt', interrupted)
    with pytest.raises(OSError):
        getattr(reader, method)(_image())
    monkeypatch.setattr(Ledger, 'release_attempt', release)
    terminal, = reader.cache.glob('*.rejected')
    receipt = json.loads(terminal.read_text())
    reopened = Ledger(ledger.path, .1)
    if contradiction == 'bound':
        receipt['bound_usd'] *= 2
        terminal.write_text(json.dumps(receipt))
    elif contradiction == 'label':
        # A receipt for another image cannot settle this request.
        other = terminal.with_name('0' + terminal.name[1:])
        terminal.rename(other)
        terminal = other
    else:
        reopened.reconcile_attempt(receipt['reservation'], .001)
    with pytest.raises(model.UncertainBilling):
        reader._complete_direct_rejection(terminal.with_suffix('.pending'))
    final = Ledger(ledger.path, .1)
    assert not [e for e in final.entries('reservation') if e['event'] == 'released']
    assert (final.spent() == .001) if contradiction == 'billed' else (final.pending_usd() > 0)
    assert len(calls) == 1


@pytest.mark.parametrize('method', ['read', 'read_marker'])
@pytest.mark.parametrize('connect_failure', [False, True])
def test_proven_unbilled_outcome_releases_even_if_terminal_storage_fails(tmp_path, monkeypatch, method, connect_failure):
    ledger = Ledger(tmp_path / 'ledger.jsonl', .1)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', ledger)
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        if connect_failure:
            raise requests.ConnectTimeout('Synthetic connection failure')
        return _Rejected()

    monkeypatch.setattr(requests, 'post', post)
    write_text = Path.write_text

    def fail_terminal(path, *args, **kwargs):
        if path.name.endswith('.rejected.tmp'):
            raise OSError('Synthetic terminal receipt write failure')
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', fail_terminal)
    with pytest.raises(OSError):
        getattr(reader, method)(_image())
    assert len(calls) == 1
    assert Ledger(ledger.path, .1).pending_usd() == 0
    assert not list(reader.cache.glob('*.pending'))
