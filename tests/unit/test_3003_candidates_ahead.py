# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#3003: the Kobo sync summary says how many books are still ahead.

After a forced full sync the walk restarts and every sync delivers one page,
so a reader asked "is it worth syncing again?" had nothing in the INFO log to
go on.  The one-line ``Kobo Sync summary`` now ends its counters with
``candidates_ahead``: the books in the account's Kobo scope that sit past the
incoming cursor, this page included.  Books the Kobo already holds are part of
that number, so it bounds the walk rather than promising a delivery count.
These run through the real ``HandleSyncRequest`` body over the in-memory world
in ``kobo_state_world``.
"""

import logging
import re

import pytest

from cps import kobo
from tests.unit.kobo_state_world import (  # noqa: F401  (fixtures)
    _now, _states, kw, read_column_class,
)

pytestmark = pytest.mark.unit


def _candidates_ahead(caplog):
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("Kobo Sync summary:")]
    assert lines, "no Kobo Sync summary was logged"
    found = re.search(r" candidates_ahead=(\S+) cursors ", lines[-1])
    assert found, lines[-1]
    return found.group(1)


def test_a_first_sync_counts_every_book_in_scope(kw, caplog):
    caplog.set_level(logging.INFO, logger="cps.kobo")

    kw.sync(kw.a)

    assert _candidates_ahead(caplog) == str(len(kw.books))


def test_a_settled_kobo_has_nothing_ahead(kw, caplog):
    kw.settle(kw.a)
    caplog.clear()
    caplog.set_level(logging.INFO, logger="cps.kobo")

    kw.sync(kw.a)

    assert _candidates_ahead(caplog) == "0"
