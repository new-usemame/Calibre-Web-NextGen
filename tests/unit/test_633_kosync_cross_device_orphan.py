# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork #633 — KOReader cross-device position sync.

Symptom (reported by @Glalith121): Kindle 1 is at 80%, Kindle 2 at 67%.
Opening Kindle 2 never prompts to jump forward; a manual pull says
"already synced". No errors in logs.

Root cause: a device whose local file checksum is NOT registered in
``book_format_checksums`` never resolves to a Calibre ``book_id``. Its
progress is stored/queried under the raw checksum, orphaned from the
``book_id``-keyed record the other device uses. Two failure vectors:

  * (helper.py) checksums were registered ONLY on metadata-embedded
    downloads, so a device that downloaded a raw (non-embedded) file, or
    a copy from before a metadata edit, never had its checksum mapped to
    the book — ``book_id`` never resolves for it.
  * (kosync.py) even when both checksums exist for a book, the progress
    lookup only matched ``(book_id, this_checksum)``, so a record stored
    under a *different* checksum of the same book was never found.

These tests pin the two query-side invariants (kosync) plus the
registration-coverage invariant (helper). The registration one uses the
static-source pattern already established by
``test_helper_koreader_checksum_guard.py`` because exercising
``do_download_file`` live needs the full Calibre worker init; the live
end-to-end path is covered by the container smoke in the PR.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps.progress_syncing.models import AppBase, KOSyncProgress, BookFormatChecksum

REPO_ROOT = Path(__file__).resolve().parents[2]


def _kosync_module():
    """Return the kosync *module* (not the re-exported Blueprint object)."""
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


@pytest.mark.unit
class TestCrossDeviceOrphanLookup:
    """kosync Fix A — unify progress across ALL of a book's registered
    checksums, not just the one the querying device presented."""

    def _seed_orphan(self, session):
        """Kindle 1 reached 80%, but its progress got stored under a raw
        file checksum 'C2' (book_id didn't resolve at PUT time). Kindle 2
        holds its own copy whose checksum is 'C1'."""
        newer = datetime.now(timezone.utc) - timedelta(minutes=2)
        orphan = KOSyncProgress(
            user_id=1, document="C2",  # stored under a raw checksum
            progress="cre://1/2/80", percentage=80.0,
            device="kindle1", device_id="dev-pw",
            timestamp=newer,
        )
        session.add(orphan)
        session.commit()

    def test_resolving_device_finds_orphan_under_other_checksum(self, in_memory_session):
        """Kindle 2 (checksum C1, resolves to book_id 42) must find Kindle
        1's 80% record even though it was stored under checksum C2.

        RED on current code: lookup is only in_((42, 'C1')) and misses the
        row stored under 'C2'."""
        self._seed_orphan(in_memory_session)
        kosync_mod = _kosync_module()
        # book 42's registered checksums include BOTH devices' files.
        with patch.object(kosync_mod, "get_book_checksums", create=True,
                          return_value=["C1", "C2"]), \
             patch.object(kosync_mod, "ub", MagicMock(session=in_memory_session)):
            record = kosync_mod.get_progress_record(
                user_id=1, document_checksum="C1", book_id=42,
            )
        assert record is not None, (
            "resolving device must find the sibling-checksum progress record (#633)"
        )
        assert record.percentage == 80.0

    def test_no_cross_book_leak_via_checksum_union(self, in_memory_session):
        """The checksum union must not pull in another book's record. Only
        checksums registered for THIS book_id are unioned."""
        # A record for a DIFFERENT book, under checksum 'CX'.
        other = KOSyncProgress(
            user_id=1, document="CX", progress="p", percentage=12.0,
            device="d", device_id="i", timestamp=datetime.now(timezone.utc),
        )
        in_memory_session.add(other)
        in_memory_session.commit()
        kosync_mod = _kosync_module()
        # book 42 only knows about C1/C2 — NOT CX.
        with patch.object(kosync_mod, "get_book_checksums", create=True,
                          return_value=["C1", "C2"]), \
             patch.object(kosync_mod, "ub", MagicMock(session=in_memory_session)):
            record = kosync_mod.get_progress_record(
                user_id=1, document_checksum="C1", book_id=42,
            )
        assert record is None, "must not union another book's checksum record"

    def test_no_book_checksum_query_when_book_id_absent(self, in_memory_session):
        """When book_id doesn't resolve, we must not attempt the book
        checksum union (nothing to union on) and fall back to the raw
        checksum match — preserving existing behavior."""
        self._seed_orphan(in_memory_session)
        kosync_mod = _kosync_module()
        gbc = MagicMock(return_value=["C1", "C2"])
        with patch.object(kosync_mod, "get_book_checksums", create=True, new=gbc), \
             patch.object(kosync_mod, "ub", MagicMock(session=in_memory_session)):
            record = kosync_mod.get_progress_record(
                user_id=1, document_checksum="C2", book_id=None,
            )
        assert record is not None and record.percentage == 80.0
        gbc.assert_not_called()


@pytest.mark.unit
class TestGetBookChecksumsRealDB:
    """Integration: exercise the REAL get_book_checksums + get_progress_record
    against real in-memory DBs (no mocked query), proving the query wiring
    (table/columns) and the end-to-end unification, which the container
    HTTP round-trip would otherwise be needed to cover."""

    def _wire(self, monkeypatch):
        import cps
        import sys

        calibre_engine = create_engine("sqlite:///:memory:")
        BookFormatChecksum.__table__.create(calibre_engine, checkfirst=True)
        calibre_session = sessionmaker(bind=calibre_engine)()

        app_engine = create_engine("sqlite:///:memory:")
        AppBase.metadata.create_all(app_engine)
        app_session = sessionmaker(bind=app_engine)()

        fake_calibre_db = MagicMock(session=calibre_session)
        monkeypatch.setattr(cps, "calibre_db", fake_calibre_db, raising=False)
        kosync_mod = _kosync_module()
        monkeypatch.setattr(kosync_mod, "ub", MagicMock(session=app_session))
        return kosync_mod, calibre_session, app_session

    def test_real_query_unifies_orphan_across_book_checksums(self, monkeypatch):
        kosync_mod, calibre_session, app_session = self._wire(monkeypatch)
        # Book 42 has TWO registered checksums (both devices downloaded it).
        calibre_session.add_all([
            BookFormatChecksum(book=42, format="EPUB", checksum="C1"),
            BookFormatChecksum(book=42, format="EPUB", checksum="C2"),
        ])
        calibre_session.commit()
        # Progress got stored under raw checksum C2 (orphan), 80%, newest.
        app_session.add(KOSyncProgress(
            user_id=1, document="C2", progress="cre://1/2/80",
            percentage=80.0, device="kindle1", device_id="pw",
            timestamp=datetime.now(timezone.utc),
        ))
        app_session.commit()

        # A device presenting C1 (resolves to book_id 42) must find the 80%.
        record = kosync_mod.get_progress_record(
            user_id=1, document_checksum="C1", book_id=42,
        )
        assert record is not None and record.percentage == 80.0

    def test_get_book_checksums_returns_all_registered(self, monkeypatch):
        kosync_mod, calibre_session, _ = self._wire(monkeypatch)
        calibre_session.add_all([
            BookFormatChecksum(book=7, format="EPUB", checksum="bin-hash",
                              version="koreader"),
            BookFormatChecksum(book=7, format="EPUB", checksum="fname-hash",
                              version="koreader_filename"),
            BookFormatChecksum(book=99, format="EPUB", checksum="other-book"),
        ])
        calibre_session.commit()
        result = set(kosync_mod.get_book_checksums(7))
        assert result == {"bin-hash", "fname-hash"}, (
            "must return every checksum (all versions) for the book, and only "
            "that book's"
        )

    def test_get_book_checksums_empty_for_falsy_book_id(self, monkeypatch):
        kosync_mod, _, _ = self._wire(monkeypatch)
        assert kosync_mod.get_book_checksums(None) == []
        assert kosync_mod.get_book_checksums(0) == []


@pytest.mark.unit
class TestPutRekeysOrphanToBookId:
    """kosync Fix C — a PUT that resolves a book_id must converge the found
    record onto the book_id key so the table self-heals."""

    def test_put_source_rekeys_found_record_to_book_id(self):
        """Source-pin: the PUT handler assigns the resolved book_id back to
        the progress record's document so future lookups from any device
        share it. Guards against reverting to checksum-only keying."""
        src = (REPO_ROOT / "cps" / "progress_syncing" / "protocols"
               / "kosync.py").read_text(encoding="utf-8")
        # The PUT handler must, when book_id resolves, set the record's
        # document to the book_id (str). Look for an assignment onto
        # .document guarded by book_id within the PUT region.
        assert re.search(r"\.document\s*=\s*str\(book_id\)", src), (
            "PUT handler must re-key the found record onto str(book_id) when "
            "the book resolves (#633 self-heal)"
        )


@pytest.mark.unit
class TestChecksumRegistrationCoverage:
    """helper.py Fix 1 — register the served file's checksum on EVERY
    KOReader-sync download, not only metadata-embedded ones, so raw
    downloads resolve to a book_id."""

    HELPER = REPO_ROOT / "cps" / "helper.py"

    def _read(self):
        return self.HELPER.read_text(encoding="utf-8")

    def test_registration_not_gated_on_metadata_embedded(self):
        """RED on current code: the checksum-registration block is wrapped
        in ``if metadata_was_embedded and ...``. That gate orphans every
        raw download. After the fix the block runs for any served file
        (still gated on is_koreader_sync_enabled for the table guard)."""
        src = self._read()
        call_idx = src.find("calculate_and_store_checksum(\n")
        if call_idx == -1:
            call_idx = src.find("calculate_and_store_checksum(")
        assert call_idx != -1
        # Inspect the guarding `if` immediately preceding the try/import
        # that wraps the call (~500 char window up to the call).
        window = src[max(0, call_idx - 600):call_idx]
        # The controlling condition must NOT require metadata_was_embedded.
        assert "if metadata_was_embedded and" not in window, (
            "checksum registration must not be gated on metadata_was_embedded "
            "— raw (non-embedded) downloads must register their checksum too "
            "(#633)"
        )

    def test_registration_still_gated_on_sync_enabled(self):
        """The CWA #1183 guard must remain: no checksum work when sync is
        off (else 'no such table' log spam)."""
        src = self._read()
        call_idx = src.find("calculate_and_store_checksum(")
        window = src[max(0, call_idx - 600):call_idx + 200]
        assert "is_koreader_sync_enabled" in window
