# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork issue #1167: the "Duplicates found" popup keeps
naming books that were deleted outside Calibre-Web, while a fresh scan and the
/duplicates page both correctly report none.

Root cause — a second, unguarded consumer of the same cache.

Fork #737 established that ``cwa_duplicate_cache`` is serialized at scan time,
so it does not reflect books the user has since ARCHIVED, HIDDEN, or that were
deleted from the library. The fix added ``filter_visible_duplicate_groups()``,
which mirrors what the /duplicates page does (drop any group with fewer than
two books still visible) and wired it into ``/duplicates/status``.

But ``/duplicates/status`` is not the only consumer. ``render_template.py``
builds a ``duplicate_notification`` dict on EVERY classic page render (injected
via ``layout.html``), reading the very same cache — and it never called the
re-validation. Its ``count`` was ``len()`` straight off the raw cache. So a
book deleted in Calibre desktop disappears from ``metadata.db`` (a scan and the
/duplicates page correctly find nothing) while the popup, rendered from the
stale cache, still insists on it. That is the reporter's symptom exactly, and
it explains the "can't reproduce exactly when": it depends on whether the cache
was last written before or after the external deletion.

A second drift bug in the same block: the dismissed-group filtering was
hand-rolled against ``group_hash``. ``filter_dismissed_groups()`` deliberately
matches on ``duplicate_key`` instead, because ``group_hash`` derives from the
display title/author of whichever book sorts first — so a metadata edit or new
ingest rotates it and dismissed groups silently resurface. The popup carried
that bug; the status endpoint did not.

Fix: both consumers now route through the same two shared helpers, via
``_build_duplicate_notification()``. Single source of truth, no drift.

These tests pin the drop/trim behaviour against a controlled visibility oracle
(so the reporter's case — a pair with one book deleted — is proven to leave the
popup), pin that dismissals match on the stable key, and source-pin that the
render path actually wires the re-validation in.
"""

from __future__ import annotations

import pathlib

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RENDER_PY = REPO_ROOT / "cps" / "render_template.py"


def _group(*book_ids, title="Dune", author="Herbert", key=None):
    return {
        "title": title,
        "author": author,
        "count": len(book_ids),
        "group_hash": "H-" + title,
        "duplicate_key": key or ("K-" + title),
        "book_ids": list(book_ids),
    }


@pytest.fixture
def visibility(mocker):
    """Pin the DB-backed visibility oracle so the pure group-drop logic can be
    exercised without a real Calibre library."""

    def _set(visible_ids):
        visible = {int(b) for b in visible_ids}
        mocker.patch(
            "cps.duplicates._visible_duplicate_book_ids",
            lambda book_ids, user_id: {int(b) for b in book_ids if int(b) in visible},
        )

    return _set


@pytest.fixture
def no_dismissals(mocker):
    """Neutralise the dismissed-group DB lookup unless a test wants it."""
    mocker.patch(
        "cps.duplicates.filter_dismissed_groups",
        lambda groups, user_id=None: groups,
    )


# ---------------------------------------------------------------------------
# Behaviour: the popup re-validates the cache against the live library
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("no_dismissals")
class TestNotificationRevalidatesCache:
    def test_pair_with_one_book_deleted_leaves_the_popup(self, visibility):
        """The reporter's case (#1167): both books were added as duplicates,
        then one was deleted in Calibre desktop. The group is no longer a
        duplicate, so the popup must not name it."""
        from cps import render_template as rt

        visibility({1})  # book 2 deleted from the library
        payload = rt._build_duplicate_notification(
            [_group(1, 2)], user_id=9, notifications_enabled=True
        )
        assert payload["count"] == 0, (
            "the duplicate popup counted a pair whose second book was deleted "
            "outside Calibre-Web, while a scan and /duplicates both show none "
            "(#1167)"
        )
        assert payload["preview"] == []

    def test_group_with_both_books_deleted_leaves_the_popup(self, visibility):
        from cps import render_template as rt

        visibility(set())
        payload = rt._build_duplicate_notification(
            [_group(1, 2)], user_id=9, notifications_enabled=True
        )
        assert payload["count"] == 0
        assert payload["preview"] == []

    def test_genuine_live_duplicate_still_reported(self, visibility):
        """The fix must not suppress real duplicates — both books present."""
        from cps import render_template as rt

        visibility({1, 2})
        payload = rt._build_duplicate_notification(
            [_group(1, 2)], user_id=9, notifications_enabled=True
        )
        assert payload["count"] == 1
        assert payload["preview"][0]["title"] == "Dune"
        assert payload["preview"][0]["count"] == 2

    def test_partially_deleted_group_is_trimmed_not_dropped(self, visibility):
        """A three-book group with one book deleted is still a duplicate, but
        the popup must show the reduced count, not the stale one."""
        from cps import render_template as rt

        visibility({1, 2})
        payload = rt._build_duplicate_notification(
            [_group(1, 2, 3)], user_id=9, notifications_enabled=True
        )
        assert payload["count"] == 1
        assert payload["preview"][0]["count"] == 2, (
            "the popup showed the stale cached book count for a group that "
            "lost a book to an external delete"
        )

    def test_only_live_groups_survive_a_mixed_cache(self, visibility):
        from cps import render_template as rt

        groups = [
            _group(1, 2, title="Dune"),
            _group(3, 4, title="Neuromancer"),
            _group(5, 6, title="Hyperion"),
        ]
        visibility({1, 2, 5})  # Neuromancer fully gone, Hyperion lost one
        payload = rt._build_duplicate_notification(
            groups, user_id=9, notifications_enabled=True
        )
        assert payload["count"] == 1
        assert [p["title"] for p in payload["preview"]] == ["Dune"]

    def test_preview_is_capped_at_three(self, visibility):
        from cps import render_template as rt

        groups = [_group(i, i + 100, title=f"T{i}") for i in range(1, 6)]
        visibility({i for i in range(1, 6)} | {i + 100 for i in range(1, 6)})
        payload = rt._build_duplicate_notification(
            groups, user_id=9, notifications_enabled=True
        )
        assert payload["count"] == 5
        assert len(payload["preview"]) == 3

    def test_empty_cache_is_not_reported(self, visibility):
        from cps import render_template as rt

        visibility(set())
        payload = rt._build_duplicate_notification(
            [], user_id=9, notifications_enabled=True
        )
        assert payload["count"] == 0
        assert payload["preview"] == []

    def test_notifications_disabled_is_preserved(self, visibility):
        from cps import render_template as rt

        visibility({1, 2})
        payload = rt._build_duplicate_notification(
            [_group(1, 2)], user_id=9, notifications_enabled=False
        )
        assert payload["enabled"] is False
        assert payload["count"] == 1, (
            "disabling the popup must not change the underlying count"
        )

    def test_scan_pending_marks_the_payload_stale(self, visibility):
        from cps import render_template as rt

        visibility({1, 2})
        payload = rt._build_duplicate_notification(
            [_group(1, 2)], user_id=9, notifications_enabled=True, scan_pending=True
        )
        assert payload["stale"] is True
        assert payload["cached"] is True

    def test_anonymous_user_falls_back_to_the_raw_cache(self, visibility):
        """With no concrete user there is no per-user view to re-validate
        against; degrade to the cache rather than hiding real duplicates."""
        from cps import render_template as rt

        visibility(set())
        payload = rt._build_duplicate_notification(
            [_group(1, 2)], user_id=None, notifications_enabled=True
        )
        assert payload["count"] == 1


# ---------------------------------------------------------------------------
# Behaviour: dismissals match on the stable key, not the volatile hash
# ---------------------------------------------------------------------------

class TestDismissalsUseTheSharedHelper:
    def test_dismissed_group_is_filtered_via_shared_helper(self, mocker, visibility):
        """The popup must delegate dismissal matching to
        ``filter_dismissed_groups`` (which keys on the stable
        ``duplicate_key``) instead of hand-rolling a ``group_hash`` match."""
        from cps import render_template as rt

        seen = {}

        def _fake(groups, user_id=None):
            seen["user_id"] = user_id
            return [g for g in groups if g.get("duplicate_key") != "K-Dune"]

        mocker.patch("cps.duplicates.filter_dismissed_groups", _fake)
        visibility({1, 2, 3, 4})

        payload = rt._build_duplicate_notification(
            [_group(1, 2, title="Dune"), _group(3, 4, title="Neuromancer")],
            user_id=9,
            notifications_enabled=True,
        )
        assert seen["user_id"] == 9, "the user id must reach the dismissal filter"
        assert payload["count"] == 1
        assert payload["preview"][0]["title"] == "Neuromancer"

    def test_dismissal_survives_a_rotated_group_hash(self, mocker, visibility):
        """``group_hash`` rotates when a metadata edit changes which book sorts
        first. Keying dismissals on it silently resurfaces them — the exact bug
        the shared helper exists to avoid."""
        from cps import render_template as rt

        dismissed_key = "K-Dune"
        mocker.patch(
            "cps.duplicates.filter_dismissed_groups",
            lambda groups, user_id=None: [
                g for g in groups if g.get("duplicate_key") != dismissed_key
            ],
        )
        visibility({1, 2})

        rotated = _group(1, 2, title="Dune")
        rotated["group_hash"] = "H-ROTATED-BY-METADATA-EDIT"

        payload = rt._build_duplicate_notification(
            [rotated], user_id=9, notifications_enabled=True
        )
        assert payload["count"] == 0, (
            "a dismissed group resurfaced in the popup after its group_hash "
            "rotated — dismissals must match on duplicate_key"
        )


# ---------------------------------------------------------------------------
# Resilience: a broken filter must never take down every page render
# ---------------------------------------------------------------------------

class TestDegradesGracefully:
    def test_filter_failure_degrades_to_the_cache(self, mocker):
        """``duplicate_notification`` is built on every classic page render.
        If re-validation raises, fall back to the cached groups rather than
        500-ing the whole site."""
        from cps import render_template as rt

        mocker.patch(
            "cps.duplicates.filter_dismissed_groups",
            lambda groups, user_id=None: groups,
        )
        mocker.patch(
            "cps.duplicates.filter_visible_duplicate_groups",
            side_effect=RuntimeError("db gone"),
        )
        payload = rt._build_duplicate_notification(
            [_group(1, 2)], user_id=9, notifications_enabled=True
        )
        assert payload["count"] == 1
        assert payload["preview"][0]["title"] == "Dune"


# ---------------------------------------------------------------------------
# Source pins — these are the lines missing on main
# ---------------------------------------------------------------------------

class TestSourcePins:
    def test_render_path_revalidates_against_live_view(self):
        body = RENDER_PY.read_text()
        assert "filter_visible_duplicate_groups" in body, (
            "render_template.py builds the duplicate popup from the scan-time "
            "cache on every page render; it must route through "
            "filter_visible_duplicate_groups so a deleted/archived book cannot "
            "keep the popup alive (#1167)"
        )

    def test_render_path_uses_the_shared_dismissal_filter(self):
        body = RENDER_PY.read_text()
        assert "filter_dismissed_groups" in body, (
            "render_template.py must delegate dismissal matching to the shared "
            "filter_dismissed_groups (stable duplicate_key) instead of "
            "hand-rolling a group_hash comparison (#1167)"
        )

    def test_no_hand_rolled_dismissal_query_remains(self):
        body = RENDER_PY.read_text()
        assert "DismissedDuplicateGroup" not in body, (
            "the hand-rolled DismissedDuplicateGroup query in render_template.py "
            "keyed on the volatile group_hash and drifted from the shared "
            "helper; it must be gone (#1167)"
        )

    def test_import_is_lazy_to_avoid_a_cycle(self):
        """cps.duplicates imports render_title_template from render_template,
        so a module-level import here would be circular. Pin the lazy import."""
        body = RENDER_PY.read_text()
        assert "from .duplicates import" in body, "expected a lazy duplicates import"
        for line in body.splitlines():
            if line.startswith("from .duplicates import"):
                pytest.fail(
                    "cps.duplicates imports from cps.render_template, so this "
                    "import must stay inside the function (lazy), not at module "
                    "level — a top-level import creates a circular import"
                )
