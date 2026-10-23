"""Synthetic transport replies exercise durable reservation outcomes offline."""
import io
import json

import pytest
import requests
from PIL import Image

from cps.services.reflow import model, vision_reading
from cps.services.reflow.ledger import Ledger

pytestmark = pytest.mark.unit


def image(color='white'):
    buffer = io.BytesIO()
    Image.new('RGB', (24, 32), color).save(buffer, format='JPEG')
    return buffer.getvalue()


class Rejection:
    def __init__(self, status=400, generation=None, header=False):
        self.status_code = status
        self.headers = {'X-Generation-Id': generation} if header else {}
        self.body = {'error': {'message': 'Synthetic request rejection'}}
        if generation and not header:
            self.body['id'] = generation

    def json(self):
        return self.body

    def raise_for_status(self):
        raise requests.HTTPError('Synthetic request rejection', response=self)


@pytest.mark.parametrize('method', ['read', 'read_marker'])
@pytest.mark.parametrize('status', [400, 401, 402, 403, 404, 413, 422])
def test_proved_rejection_releases_hold_and_is_terminal(tmp_path, monkeypatch, method, status):
    ledger = Ledger(tmp_path / 'ledger.jsonl', .1)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', ledger)
    calls = []

    def post(*args, **kwargs):
        assert Ledger(ledger.path, .1).pending_usd() > 0
        calls.append(kwargs['json'])
        return Rejection(status)

    monkeypatch.setattr(requests, 'post', post)
    with pytest.raises(model.ModelError):
        getattr(reader, method)(image())
    restarted = Ledger(ledger.path, .1)
    assert restarted.pending_usd() == restarted.spent() == 0
    assert reader.financial_dispatch_allowed()
    assert not list(reader.cache.glob('*.pending'))
    terminal = list(reader.cache.glob('*.rejected'))
    assert len(terminal) == 1
    record = json.loads(terminal[0].read_text())
    assert record['status'] == 'REJECTED_UNBILLED'
    assert record['http_status'] == status
    assert record['reservation']
    with pytest.raises(model.ModelError):
        getattr(vision_reading.Reader('inert-key', reader.cache, restarted), method)(image())
    assert len(calls) == 1
    # A different image can dispatch; its own rejection also settles to zero.
    with pytest.raises(model.ModelError):
        getattr(reader, method)(image('black'))
    assert len(calls) == 2 and Ledger(ledger.path, .1).pending_usd() == 0


@pytest.mark.parametrize('method', ['read', 'read_marker'])
@pytest.mark.parametrize('reply', [Rejection(429), Rejection(500),
                                  Rejection(400, 'synthetic-generation'),
                                  Rejection(400, 'synthetic-generation', header=True)])
def test_ambiguous_rejection_keeps_original_hold(tmp_path, monkeypatch, method, reply):
    ledger = Ledger(tmp_path / 'ledger.jsonl', .1)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', ledger)
    calls = []
    monkeypatch.setattr(requests, 'post', lambda *a, **k: calls.append(k) or reply)
    with pytest.raises(model.UncertainBilling):
        getattr(reader, method)(image())
    held = Ledger(ledger.path, .1).pending_usd()
    assert held > 0 and not reader.financial_dispatch_allowed()
    pending = list(reader.cache.glob('*.pending'))
    assert len(pending) == 1 and not list(reader.cache.glob('*.rejected'))
    original = pending[0].read_bytes()
    with pytest.raises(model.UncertainBilling):
        getattr(reader, method)(image())
    assert len(calls) == 1 and pending[0].read_bytes() == original
    assert Ledger(ledger.path, .1).pending_usd() == held


@pytest.mark.parametrize('method', ['read', 'read_marker'])
@pytest.mark.parametrize('connect', [True, False])
def test_transport_proof_distinguishes_connect_from_read_timeout(tmp_path, monkeypatch, method, connect):
    ledger = Ledger(tmp_path / 'ledger.jsonl', .1)
    reader = vision_reading.Reader('inert-key', tmp_path / 'cache', ledger)

    def fail(*args, **kwargs):
        raise (requests.ConnectTimeout if connect else requests.ReadTimeout)('Synthetic timeout')

    monkeypatch.setattr(requests, 'post', fail)
    with pytest.raises(model.ModelError if connect else requests.ReadTimeout):
        getattr(reader, method)(image())
    restarted = Ledger(ledger.path, .1)
    assert (restarted.pending_usd() == 0) is connect
    assert reader.financial_dispatch_allowed() is connect
    assert bool(list(reader.cache.glob('*.pending'))) is (not connect)
    assert bool(list(reader.cache.glob('*.rejected'))) is connect
