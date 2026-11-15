# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2804: say at DEBUG which place a Kobo was sent, and why.

Reporters see a Kobo show the web reader's new percentage but open the book
at its own older page. That is what the server deliberately sends a Kobo
that has not finished downloading the library's current KEPUB: the shared
percentage with the Kobo's own Location (share-my-place spec, 14c). Nothing
in the log said so, and the Kobo's own sync response is not logged, so
neither the reporter nor we could tell this apart from a firmware change.

The Kobo GET /state below is the real route in the share-place span world.
"""

import logging

import pytest

from tests.unit.share_place_span_world import location_of, sw  # noqa: F401  (fixture)
from tests.unit.test_share_place_kobo_download_proof import (  # noqa: F401  (fixtures)
    _bookmark, _download, gates_enforced, rows, stale_kobo,
)

pytestmark = pytest.mark.unit


def _place_records(caplog):
    return [r.getMessage() for r in caplog.records if "Kobo place for book" in r.getMessage()]


def test_a_kobo_without_the_current_kepub_logs_that_it_was_sent_its_own_place(
        caplog, stale_kobo):
    sw = stale_kobo
    with caplog.at_level(logging.DEBUG, logger="cps.services.kobo_place_gate"):
        bookmark = _bookmark(sw)
    assert location_of(bookmark) == sw.own

    records = _place_records(caplog)
    assert records, caplog.text
    message = records[-1]
    assert "held" in message
    assert "has not finished downloading the library's current KEPUB" in message
    assert "ProgressPercent=40" in message
    assert sw.own["Value"] in message


def test_a_kobo_holding_the_current_kepub_logs_the_exact_span_it_was_sent(
        monkeypatch, caplog, stale_kobo):
    sw = stale_kobo
    served = _download(monkeypatch, sw)
    assert served.status == 200 and served.complete

    with caplog.at_level(logging.DEBUG, logger="cps.services.kobo_place_gate"):
        bookmark = _bookmark(sw)
    assert location_of(bookmark) == sw.exact

    message = _place_records(caplog)[-1]
    assert "exact" in message
    assert sw.exact["Value"] in message
    assert "held" not in message
