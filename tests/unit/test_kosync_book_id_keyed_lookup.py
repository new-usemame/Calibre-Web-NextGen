# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for upstream CWA #1225 (fork PR #99) — KOReader sync
should look up progress records by either the file checksum OR the calibre
book_id, preferring the most recently updated record.

Without this fix, two devices with the same Calibre book under different
file checksums fragment into separate progress records that never converge.
"""

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Build a minimal SQLAlchemy app so we can exercise the real filter expression
# instead of mocking out the query chain. This is closer to behavior-pinning
# than mock-based tests for query correctness.
from cps.progress_syncing.models import AppBase, KOSyncProgress


def _kosync_module():
    """Return the kosync *module* (not the re-exported Blueprint).

    `cps.progress_syncing.protocols.__init__` does
    `from .kosync import kosync`, which binds the Blueprint object as
    `protocols.kosync` and shadows the submodule attribute. The module
    itself is still in `sys.modules`, so we fetch it from there.
    """
    import sys
    import cps.progress_syncing.protocols.kosync  # noqa: F401 — populate sys.modules
    return sys.modules["cps.progress_syncing.protocols.kosync"]


@pytest.fixture
def in_memory_session():
    engine = create_engine("sqlite:///:memory:")
    AppBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def populated_session(in_memory_session):
    """User 1 has progress recorded under both a file checksum AND a book_id
    string for the same conceptual book. Records have distinct timestamps."""
    earlier = datetime.now(timezone.utc) - timedelta(hours=2)
    later = datetime.now(timezone.utc) - timedelta(minutes=5)

    checksum_record = KOSyncProgress(
        user_id=1, document="abc123checksum",
        progress="cre://1/2/3", percentage=42.0,
        device="kobo", device_id="dev-old",
        timestamp=earlier,
    )
    book_id_record = KOSyncProgress(
        user_id=1, document="42",  # book_id stored as string
        progress="cre://1/2/9", percentage=87.0,
        device="koreader", device_id="dev-new",
        timestamp=later,
    )
    other_user_record = KOSyncProgress(
        user_id=99, document="abc123checksum",
        progress="cre://x", percentage=5.0,
        device="other", device_id="other-dev",
        timestamp=later,
    )
    in_memory_session.add_all([checksum_record, book_id_record, other_user_record])
    in_memory_session.commit()
    return in_memory_session


@pytest.mark.unit
class TestGetProgressRecordCrossKeyLookup:
    """Pin the cross-key lookup semantics introduced in fork PR #99."""

    def test_returns_book_id_record_when_book_id_more_recent(self, populated_session):
        """Most-recent-wins: book_id record beats older checksum record."""
        kosync_mod = _kosync_module()
        with patch.object(kosync_mod, "ub", MagicMock(session=populated_session)):
            record = kosync_mod.get_progress_record(
                user_id=1, document_checksum="abc123checksum", book_id=42,
            )
        assert record is not None
        assert record.document == "42"
        assert record.percentage == 87.0
        assert record.device == "koreader"

    def test_returns_checksum_record_when_book_id_unknown(self, populated_session):
        """Calibre lookup didn't resolve a book_id — fall back to checksum match."""
        kosync_mod = _kosync_module()
        with patch.object(kosync_mod, "ub", MagicMock(session=populated_session)):
            record = kosync_mod.get_progress_record(
                user_id=1, document_checksum="abc123checksum", book_id=None,
            )
        assert record is not None
        assert record.document == "abc123checksum"
        assert record.percentage == 42.0

    def test_returns_book_id_record_when_checksum_unknown(self, populated_session):
        """KOReader presented a fresh checksum we've never seen, but the book_id
        matched. We should still find the existing book_id-keyed progress."""
        kosync_mod = _kosync_module()
        with patch.object(kosync_mod, "ub", MagicMock(session=populated_session)):
            record = kosync_mod.get_progress_record(
                user_id=1, document_checksum="newchecksum_unseen", book_id=42,
            )
        assert record is not None
        assert record.document == "42"

    def test_returns_none_when_no_records_match(self, populated_session):
        kosync_mod = _kosync_module()
        with patch.object(kosync_mod, "ub", MagicMock(session=populated_session)):
            record = kosync_mod.get_progress_record(
                user_id=1, document_checksum="never-seen", book_id=99999,
            )
        assert record is None

    def test_does_not_leak_other_users_records(self, populated_session):
        """User-id is part of the filter — never return another user's record."""
        kosync_mod = _kosync_module()
        # User 2 has nothing but user 99 has a record under the same checksum
        with patch.object(kosync_mod, "ub", MagicMock(session=populated_session)):
            record = kosync_mod.get_progress_record(
                user_id=2, document_checksum="abc123checksum", book_id=42,
            )
        assert record is None, (
            "filter must scope by user_id; cross-user lookup leaked a record"
        )

    def test_orders_by_timestamp_desc(self, in_memory_session):
        """Equal percentages on legacy keys return the newer locator."""
        kosync_mod = _kosync_module()
        old = KOSyncProgress(
            user_id=7, document="42", progress="p1", percentage=99.0,
            device="d1", device_id="i1",
            timestamp=datetime.now(timezone.utc) - timedelta(days=1),
        )
        new = KOSyncProgress(
            user_id=7, document="abc123checksum", progress="p2", percentage=99.0,
            device="d2", device_id="i2",
            timestamp=datetime.now(timezone.utc),
        )
        in_memory_session.add_all([old, new])
        in_memory_session.commit()
        with patch.object(kosync_mod, "ub", MagicMock(session=in_memory_session)):
            record = kosync_mod.get_progress_record(
                user_id=7, document_checksum="abc123checksum", book_id=42,
            )
        assert record is not None
        assert record.progress == "p2", "expected most-recent tied locator"
