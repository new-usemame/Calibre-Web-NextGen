# SPDX-License-Identifier: GPL-3.0-or-later
"""A valid proven span must survive normal cold-alignment latency.

A live bookmark GET returned a percentage offer with a valid download proof:
its 50 ms deadline elapsed while the worker was still aligning the archives.
The same worker produced the exact place afterwards. Warming is best effort
and does not survive an application restart, so the request budget must allow
this measured 120 ms conversion while still rejecting a stalled converter.
"""
import threading
import time

import pytest

from tests.unit.kobo_state_world import read_column_class  # noqa: F401
from tests.unit.share_place_span_world import sw  # noqa: F401
from tests.unit.test_share_place_web_gates import (  # noqa: F401
    cfi_of, file_backed, resume, rows, w,
)
from tests.unit.test_kobo_span_web_resume import (  # noqa: F401
    _newer_kobo_place, gates_on, library,
)

pytestmark = pytest.mark.unit


def test_proven_span_opens_exactly_when_alignment_exceeds_fifty_ms(
        library, rows, monkeypatch):
    from cps.services import kepub_alignment

    _newer_kobo_place(library, rows, proof="proven")
    expected = cfi_of(library, rows["late"])
    original = kepub_alignment.span_to_xpointer
    completed = threading.Event()
    converted = []

    def cold_bridge(*args, **kwargs):
        try:
            # Deterministic stand-in for the measured cold work; use the real
            # converter afterwards, including proof and fingerprint checks.
            time.sleep(0.12)
            value = original(*args, **kwargs)
            converted.append(value)
            return value
        finally:
            completed.set()

    monkeypatch.setattr(kepub_alignment, "span_to_xpointer", cold_bridge)
    start = time.monotonic()
    try:
        served = resume(library)["resume"]
        elapsed = time.monotonic() - start
        assert served["mode"] == "automatic"
        assert served["cfi"] == expected
        assert served["epub_sha256"]
        assert converted and converted[0]
        assert elapsed < 0.5, "exact resume still has a sub-second request bound"
    finally:
        # A failing baseline times out before conversion finishes. Leave no
        # fixture worker running when pytest restores its patches/files.
        assert completed.wait(2)
