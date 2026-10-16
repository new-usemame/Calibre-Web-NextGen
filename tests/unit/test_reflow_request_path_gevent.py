# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reflow's request path must not stall the gevent hub (N3, 7daffa5 retest).

CWNG serves every request from one OS thread and never monkey-patches, so a
blocking call on a request greenlet freezes every other user until it returns.
Opening the Reflow page hashed the whole PDF (twice per estimate, three times per
start, again for a preparation key) and probed the Tesseract binary with two
subprocesses, all on the request greenlet. A 500 MB scan is seconds of SHA-256.

The stand-ins below block the thread exactly the way a large read or a
subprocess does (``time.sleep`` cannot be preempted by an unpatched hub), and a
heartbeat greenlet measures the longest any other request would have waited.
"""
import time
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

gevent = pytest.importorskip("gevent", reason="production WSGI server is gevent")

from tests.unit.test_reflow_api import _book, _wire, mod, pdf_on_disk  # noqa: E402,F401

_BLOCK_SECONDS = 0.6
_MAX_TOLERABLE_STALL = 0.3


def _worst_stall_while(run):
    gaps, done, failed = [], [], []

    def heartbeat():
        last = time.monotonic()
        while True:
            gevent.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - last)
            last = now
            if done:
                return

    beat = gevent.spawn(heartbeat)
    gevent.sleep(0)

    def runner():
        try:
            run()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            failed.append(exc)
        finally:
            done.append(True)

    gevent.joinall([gevent.spawn(runner)])
    beat.join(timeout=1)
    beat.kill(block=True)
    if failed:
        raise failed[0]
    assert gaps, "heartbeat never sampled"
    return max(gaps)


def _slow(value):
    def call(*_args, **_kwargs):
        time.sleep(_BLOCK_SECONDS)
        return value
    return call


def test_pricing_a_book_does_not_freeze_other_requests(mod, monkeypatch, pdf_on_disk):
    """The estimate hashes the PDF and asks whether OCR is installed. Neither may
    park the hub. Breaks if either goes back to running on the request greenlet."""
    book = _wire(mod, monkeypatch, pdf_on_disk)
    source = mod._format_path(book, "PDF")
    monkeypatch.setattr(mod.extract, "document_fingerprint", _slow("f" * 64))
    monkeypatch.setattr(mod.ocr, "_engine", _slow(("/usr/bin/tesseract", "tesseract 5", "d" * 64)))
    mod._engine_checks.clear()

    worst = _worst_stall_while(lambda: mod._estimate_payload(book, source))

    assert worst < _MAX_TOLERABLE_STALL, (
        "pricing a book froze the gevent hub for %.0f ms" % (worst * 1000))


def test_preparing_an_estimate_does_not_freeze_other_requests(mod, monkeypatch, pdf_on_disk, tmp_path):
    """A preparation's identity is the PDF's hash plus the OCR runtime's. Starting
    one must not compute either on the request greenlet."""
    book = _wire(mod, monkeypatch, pdf_on_disk)
    source = mod._format_path(book, "PDF")
    monkeypatch.setattr(mod.extract, "document_fingerprint", _slow("f" * 64))
    monkeypatch.setattr(mod.ocr, "_engine", _slow(("/usr/bin/tesseract", "tesseract 5", "d" * 64)))
    store = mod.quote_preparation.PreparationStore(tmp_path / "quotes")
    monkeypatch.setattr(mod, "_quote_store", lambda: store)
    options = {"source_recovery": "auto", "ocr_language": "eng"}

    def never(*_args):
        raise mod.model.AttemptCancelled("not under test")

    try:
        worst = _worst_stall_while(
            lambda: mod._start_preparation(7, book.id, source, options, never))
    finally:
        store.close()
    assert worst < _MAX_TOLERABLE_STALL, (
        "starting a preparation froze the gevent hub for %.0f ms" % (worst * 1000))
