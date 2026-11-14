# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Issue #1190: "No book found for checksum" must say why it missed.

One log line used to cover three causes that need different answers: a
checksum row whose book id is gone, nothing registered at all, and a
checksum the server never registered. The extra detail is rate-limited per
checksum because a stuck device polls repeatedly.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import cps
from cps.progress_syncing.models import BookFormatChecksum

CHECKSUM = "9e107d9d372bb6826bd81d3542a419d6"


def _kosync():
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    return sys.modules["cps.progress_syncing.protocols.kosync"]


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    BookFormatChecksum.__table__.create(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _fresh_rate_limit_state():
    _kosync()._miss_diag_last.clear()
    yield
    _kosync()._miss_diag_last.clear()


def _describe(session, checksum=CHECKSUM):
    ns = SimpleNamespace(session=session)
    with patch.object(cps, "calibre_db", ns, create=True):
        return _kosync()._describe_checksum_miss(checksum)


@pytest.mark.unit
class TestChecksumMissDiagnostics:
    def test_nothing_registered_names_backfill(self, session):
        msg = _describe(session)
        assert "no checksums are registered" in msg
        assert "backfill" in msg

    def test_other_checksums_registered_names_unserved_copy(self, session):
        session.add(BookFormatChecksum(1, "EPUB", "a" * 32))
        session.add(BookFormatChecksum(2, "EPUB", "b" * 32))
        session.commit()
        msg = _describe(session)
        assert "2 checksums are registered" in msg
        assert "never served" in msg

    def test_row_for_missing_book_names_stale_id(self, session):
        session.add(BookFormatChecksum(42, "EPUB", CHECKSUM))
        session.commit()
        msg = _describe(session)
        assert "book id 42" in msg
        assert "stale" in msg

    def test_detail_is_rate_limited_per_checksum(self, session):
        assert _describe(session) != ""
        assert _describe(session) == ""
        assert _describe(session, checksum="c" * 32) != ""

    def test_query_failure_does_not_raise(self):
        broken = SimpleNamespace(session=SimpleNamespace(
            query=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))))
        with patch.object(cps, "calibre_db", broken, create=True):
            msg = _kosync()._describe_checksum_miss(CHECKSUM)
        assert "diagnostics unavailable" in msg

    def test_tracking_dict_is_bounded(self, session):
        k = _kosync()
        for i in range(k._MISS_DIAG_MAX_TRACKED + 10):
            _describe(session, checksum=f"{i:032d}")
        assert len(k._miss_diag_last) <= k._MISS_DIAG_MAX_TRACKED
