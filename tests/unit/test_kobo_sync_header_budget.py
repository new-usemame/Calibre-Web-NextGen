# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #1857: oversized fresh and replayed pages must announce proxy risk."""
import logging
from types import SimpleNamespace

import pytest

from tests.unit.test_1925_kobo_sync_dedownload import (
    sync_harness,
    _sync_through_flask_error_pipeline,
)
from tests.unit.test_kobo_synctoken_compression_331 import (
    _durable_token, _fake_store_jwt_high_entropy,
)

pytestmark = pytest.mark.unit


def _header_bytes(response):
    # HTTP/1 header fields, including separators and the terminating empty line.
    return len((str(response.headers)).encode("latin-1"))


def _warnings(caplog):
    return [r for r in caplog.records
            if r.name == "cps.kobo" and r.levelno == logging.WARNING]


def _store(monkeypatch, padding=""):
    from cps import kobo
    monkeypatch.setattr(kobo.config, "config_kobo_proxy", True)
    monkeypatch.setattr(kobo, "make_request_to_kobo_store", lambda _: SimpleNamespace(
        json=lambda: [], headers={
            "x-kobo-synctoken": _fake_store_jwt_high_entropy(),
            "x-kobo-recent-reads": padding,
        },
    ))


def _assert_warning(response, caplog, proxy=True):
    records = _warnings(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    token = response.headers["x-kobo-synctoken"]
    assert f"header_bytes={_header_bytes(response)}" in message
    assert f"synctoken_bytes={len(token.encode('latin-1'))}" in message
    assert f"store_proxy_enabled={proxy}" in message
    assert "README" in message and "nginx buffer sizes" in message
    assert "proxy_buffer_size 32k" in message
    assert token not in message
    assert response.status_code == 200


def test_fresh_sync_warns_once(sync_harness, monkeypatch, caplog):
    _store(monkeypatch, "private-recent-reads" * 220)
    caplog.set_level(logging.WARNING, logger="cps.kobo")
    response = _sync_through_flask_error_pipeline(sync_harness)
    assert _header_bytes(response) >= 4096
    _assert_warning(response, caplog)
    assert "private-recent-reads" not in caplog.text


def test_pending_replay_warns_once(sync_harness, monkeypatch, caplog):
    from cps import kobo
    _store(monkeypatch, "private-recent-reads" * 220)
    first = _sync_through_flask_error_pipeline(sync_harness)
    caplog.clear()
    caplog.set_level(logging.WARNING, logger="cps.kobo")
    monkeypatch.setattr(kobo, "make_request_to_kobo_store",
                        lambda _: pytest.fail("replay must use the stored page"))
    replay = _sync_through_flask_error_pipeline(sync_harness)
    assert (replay.status, replay.data, sorted(replay.headers)) == (
        first.status, first.data, sorted(first.headers))
    _assert_warning(replay, caplog)


def test_ordinary_sync_is_silent(sync_harness, caplog):
    caplog.set_level(logging.WARNING, logger="cps.kobo")
    response = _sync_through_flask_error_pipeline(sync_harness)
    assert response.status_code == 200
    assert _header_bytes(response) < 4096
    assert _warnings(caplog) == []


def test_header_budget_measurements(sync_harness, monkeypatch, caplog):
    from cps import kobo
    caplog.set_level(logging.WARNING, logger="cps.kobo")
    with sync_harness.app.test_request_context('/v1/library/sync'):
        for label, proxy, token in (
            ("typical", False, _durable_token()),
            ("store-default-cursors", True, kobo.SyncToken.SyncToken()),
            ("store-full-durable", True, _durable_token()),
        ):
            if proxy:
                _store(monkeypatch)
            response = kobo.generate_sync_response(token, [])
            print(f"{label}: header_bytes={_header_bytes(response)} "
                  f"synctoken_bytes={len(response.headers['x-kobo-synctoken'])}")
            assert response.status_code == 200
            assert _header_bytes(response) < 4096
    assert _warnings(caplog) == []


@pytest.mark.parametrize('size', [4095, 4096])
def test_exact_header_budget_boundary(monkeypatch, caplog, size):
    from cps import kobo
    from flask import Response
    monkeypatch.setattr(kobo.config, 'config_kobo_proxy', False, raising=False)
    response = Response('[]', content_type='application/json; charset=utf-8')
    response.headers['x-kobo-synctoken'] = 'opaque'
    response.headers.add('X-Padding', '')
    response.headers.add('X-Padding', 'é')  # Duplicate fields and Latin-1 bytes count.
    response.headers['X-Fill'] = ''
    response.headers['X-Fill'] = 'x' * (size - _header_bytes(response))
    before = response.status, response.data, list(response.headers)
    caplog.set_level(logging.WARNING, logger='cps.kobo')
    kobo._warn_sync_header_budget(response)
    assert (response.status, response.data, list(response.headers)) == before
    if size == 4096:
        _assert_warning(response, caplog, proxy=False)
    else:
        assert _warnings(caplog) == []


@pytest.mark.parametrize('failure', ['measurement', 'logging'])
def test_diagnostic_failure_preserves_response(monkeypatch, failure):
    from cps import kobo
    from flask import Response

    monkeypatch.setattr(kobo.config, 'config_kobo_proxy', False, raising=False)

    class Unmeasurable(str):
        def encode(self, *args, **kwargs):
            raise RuntimeError('measurement unavailable')

    response = Response('[]', headers={'x-kobo-synctoken': 'x' * 4200})
    if failure == 'measurement':
        response.headers['X-Probe'] = Unmeasurable('unchanged')
    else:
        def broken_logger(*args, **kwargs):
            raise RuntimeError('logging unavailable')
        monkeypatch.setattr(kobo.log, 'warning', broken_logger)
    before = response.status, response.data, list(response.headers)
    kobo._warn_sync_header_budget(response)
    assert (response.status, response.data, list(response.headers)) == before
