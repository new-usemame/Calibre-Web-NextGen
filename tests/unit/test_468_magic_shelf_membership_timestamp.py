# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork issue #468 (reporters @Glennza1962, @bigbold1023).

Symptom: every Kobo sync re-downloads the user's whole magic-shelf set —
books arrive as "Download"/"Unread" again, losing the local copy and the
reading position. The reporter's two most diagnostic clues:

  1. It happens with a *combined* magic-shelf membership UNDER 100 books,
     which rules out the SYNC_ITEM_LIMIT(100) batch-walk bug
     (notes/468-kobo-magic-shelf-resend-rootcause.md): that bug only bites
     at >= 100, this user is under it. So the count boundary is NOT the
     cause of this symptom.
  2. It happens "every time, UNLESS I do a back-to-back sync."

ROOT CAUSE (confirmed, code-grounded — magic_shelf.py + kobo.py):

  - get_book_ids_for_magic_shelf caches a magic shelf's membership with a
    30-minute TTL (magic_shelf.py). On expiry it DELETES the cache row and
    ADDS a fresh one, whose created_at defaults to now() — even when the
    rebuilt membership is byte-for-byte the same set of books.
  - created_at doubles as the Kobo sync's "membership added" timestamp:
    get_magic_shelf_membership_added_at() returns max(created_at) across the
    user's kobo_sync magic shelves (T_magic).
  - The Kobo sync magic-shelf arm fires when T_magic > device cursor
    (kobo.py:386-390), and on fire it resets the sub-cursor to -1
    (kobo.py:407-413) so it re-emits the ENTIRE membership. After a clean
    draining sync the device cursor equals the previous T_magic.
  - Therefore: any TTL rebuild advances T_magic past the cursor and the
    next sync re-emits the whole shelf as ChangedEntitlement, regardless of
    shelf size. A back-to-back sync inside the 30-min TTL window does NOT
    rebuild the cache, so T_magic is stable and the arm stays silent —
    exactly clue #2.

FIX (magic_shelf.py): when the TTL rebuild produces the SAME membership set,
preserve the existing created_at instead of stamping a new one. T_magic then
advances only when membership actually changes — which is the only time the
Kobo arm should re-deliver the shelf.

The model tests below exercise the sync timestamp contract. Actual cache-row
preservation and live membership changes are exercised against SQLite in
``test_magic_shelf_sort_cache_timestamp.py`` and
``test_shared_book_continuation.py``; the former source-string pins have been
replaced by those behavioral checks.
"""

import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MAGIC_SHELF_PY = REPO_ROOT / "cps" / "magic_shelf.py"

SYNC_ITEM_LIMIT = 100
DATETIME_MIN = "0000-00-00 00:00:00.000000"


# ---------------------------------------------------------------------------
# Faithful model of the Kobo magic-shelf membership arm across two syncs.
# Mirrors cps/kobo.py HandleSyncRequest lines 386-413 (arm activation +
# sub-cursor reset) and 635-648 (the batch_drained fold). Only the
# magic-shelf arm is in play (the reporter has no regular shelves), so a
# sub-limit shelf drains in a single batch.
# ---------------------------------------------------------------------------


class _Token:
    """The slice of SyncToken the magic-shelf arm reads/writes."""

    def __init__(self):
        self.books_last_modified = DATETIME_MIN   # cursor_lm
        self.magic_shelf_membership_at = DATETIME_MIN
        self.magic_shelf_last_id = -1


def _run_sync(token, magic_ids, t_magic, limit=SYNC_ITEM_LIMIT):
    """Return the list of magic book ids emitted this sync and mutate the
    token cursor in place, faithfully to HandleSyncRequest."""
    magic_ids = sorted(magic_ids)

    # kobo.py:386-390
    arm_active = bool(magic_ids and t_magic is not None and t_magic > token.books_last_modified)

    # kobo.py:407-413 — sub-cursor reset on cache-rebuild detection
    if t_magic is not None and t_magic > token.magic_shelf_membership_at:
        magic_shelf_last_id = -1
    else:
        magic_shelf_last_id = token.magic_shelf_last_id

    # The arm emits magic books with id > magic_shelf_last_id, ordered, capped.
    if arm_active:
        emitted = [i for i in magic_ids if i > magic_shelf_last_id][:limit]
    else:
        emitted = []

    new_books_last_modified = token.books_last_modified
    new_magic_shelf_last_id = magic_shelf_last_id

    if arm_active and emitted:
        new_magic_shelf_last_id = max(magic_shelf_last_id, max(emitted))

    # kobo.py:635-643 — fold fires only on a partial (drained) batch
    batch_drained = len(emitted) < limit
    if arm_active and batch_drained:
        if t_magic > new_books_last_modified:
            new_books_last_modified = t_magic
            new_magic_shelf_last_id = -1

    # Token writeback (incl. the membership_at high-water mark).
    token.books_last_modified = new_books_last_modified
    token.magic_shelf_last_id = new_magic_shelf_last_id
    if t_magic is not None:
        token.magic_shelf_membership_at = max(token.magic_shelf_membership_at, t_magic)

    return emitted


@pytest.mark.unit
class TestMembershipTimestampDrivesRefire:
    """The whole behavior turns on whether T_magic advances between syncs."""

    # Sub-100 sizes only: this is the reporter's case and the open question
    # this fix closes. The single-batch model is exact below SYNC_ITEM_LIMIT
    # (the shelf drains in one sync). The >= 100 within-sync continuation walk
    # is a separate, already-fixed bug pinned by
    # test_kobo_magic_shelf_sub_cursor_large_shelves.py.
    @pytest.mark.parametrize("shelf_size", [50, 99])
    def test_bug_refires_whole_shelf_when_timestamp_advances(self, shelf_size):
        """OLD behavior: the TTL rebuild stamps a fresh created_at every
        time, so T_magic advances on sync #2 even though membership is
        unchanged. The whole shelf re-fires — and crucially this happens at
        SUB-100 sizes, reproducing the reporter's clue #1 that the count
        boundary is not the cause."""
        magic_ids = set(range(1, shelf_size + 1))
        token = _Token()

        # Sync #1 at T_magic = t1 (cache freshly built).
        first = _run_sync(token, magic_ids, t_magic="2026-06-18 10:00:00.000000")
        assert set(first) == magic_ids, "sync #1 must deliver the whole shelf once"

        # Sync #2 after a 30-min TTL rebuild that re-stamped created_at -> t2 > t1.
        second = _run_sync(token, magic_ids, t_magic="2026-06-18 10:31:00.000000")

        assert set(second) == magic_ids, (
            f"BUG: with an advancing membership timestamp, sync #2 re-fires "
            f"the whole shelf (size {shelf_size}) instead of staying quiet. "
            f"re-emitted {len(second)} books."
        )

    @pytest.mark.parametrize("shelf_size", [50, 99])
    def test_fix_quiet_second_sync_when_timestamp_preserved(self, shelf_size):
        """FIXED behavior: an unchanged-membership rebuild preserves
        created_at, so T_magic is the SAME on sync #2. The arm stays silent
        and nothing re-downloads."""
        magic_ids = set(range(1, shelf_size + 1))
        token = _Token()
        t_stable = "2026-06-18 10:00:00.000000"

        first = _run_sync(token, magic_ids, t_magic=t_stable)
        assert set(first) == magic_ids, "sync #1 must deliver the whole shelf once"

        # Second sync with the SAME T_magic (membership preserved).
        second = _run_sync(token, magic_ids, t_magic=t_stable)
        assert second == [], (
            f"FIX: an unchanged shelf (size {shelf_size}) must NOT re-fire "
            f"on the next sync when the membership timestamp is preserved. "
            f"Got {len(second)} re-emitted books."
        )

    def test_back_to_back_sync_is_quiet_reporter_clue_2(self):
        """Reporter clue #2: 'happens every time UNLESS I do a back-to-back
        sync.' A back-to-back sync lands inside the 30-min TTL, so the cache
        is not rebuilt and T_magic is unchanged — the arm stays silent. This
        holds regardless of the fix (it's why the bug looked intermittent),
        and the test pins that our model reproduces the reported behavior."""
        magic_ids = set(range(1, 51))   # sub-100, matches the reporter
        token = _Token()
        t1 = "2026-06-18 10:00:00.000000"

        _run_sync(token, magic_ids, t_magic=t1)
        # Back-to-back: same TTL window, cache not rebuilt, same created_at.
        immediate = _run_sync(token, magic_ids, t_magic=t1)
        assert immediate == [], "a back-to-back sync (T_magic unchanged) must be quiet"

    def test_genuine_membership_change_still_delivers(self):
        """The fix must NOT suppress real changes: when a book is added to
        the shelf the membership set differs, created_at advances (modeled by
        a later T_magic), and the new book is delivered."""
        token = _Token()
        magic_ids = set(range(1, 51))
        _run_sync(token, magic_ids, t_magic="2026-06-18 10:00:00.000000")

        # User adds book 51; membership changed -> created_at advances.
        magic_ids2 = set(range(1, 52))
        second = _run_sync(token, magic_ids2, t_magic="2026-06-18 11:00:00.000000")
        assert 51 in set(second), "a genuinely added book must still be delivered"
