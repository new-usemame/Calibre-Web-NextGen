# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork issue #737: archiving one book of a duplicate pair
leaves the Admin "Duplicates found" notifier stuck in "limbo".

Root cause: the ``/duplicates/status`` endpoint (which drives the sidebar badge
and the notifier popup via ``duplicate-notifier.js``) serves its ``count`` from
``cwa_duplicate_cache``, which was serialized at scan time. It applied only
``filter_dismissed_groups`` — never the per-user archived/hidden exclusion.

The actual ``/duplicates`` page re-validates every group against
``get_common_filters`` and drops any that fall below two visible books
(``get_duplicate_groups_from_index``: ``if len(books) < 2: continue``). So after
a user archives one of a pair, the page correctly shows nothing while the badge
kept counting the group — the notifier insisted on a duplicate the page would
not show. That is the reporter's symptom.

Fix: ``get_duplicate_status`` now routes the cached groups through
``filter_visible_duplicate_groups``, which mirrors the page — dropping groups
with fewer than two books still visible to the user and reflecting the reduced
count.

These pin the group-drop behaviour against a controlled visibility oracle
(so the reporter's exact case — a two-book group with one archived — is proven
to drop), plus source pins that the status route wires the filter in and that
the visibility helper routes through ``get_common_filters`` (the same SSOT the
page uses).
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

import pytest

pytestmark = pytest.mark.unit

_HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = _HERE.parents[1]
DUP_SRC = (REPO_ROOT / "cps" / "duplicates.py").read_text()


@pytest.fixture(autouse=True)
def _isolate_sys_modules():
    """Restore sys.modules after the stub harness clobbers cps.* imports."""
    saved = sys.modules.copy()
    yield
    for name in list(sys.modules):
        if name not in saved:
            del sys.modules[name]
    for name, module in saved.items():
        if sys.modules.get(name) is not module:
            sys.modules[name] = module


def _harness():
    path = _HERE / "test_duplicate_delete_index_maintenance.py"
    spec = importlib.util.spec_from_file_location("_dup_stub_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load():
    harness = _harness()
    module, _books, _calls = harness._load_duplicates_module([])
    return module


def _group(*book_ids, title="Dune", author="Herbert", key="KEY"):
    return {
        "title": title,
        "author": author,
        "count": len(book_ids),
        "group_hash": "H-" + title,
        "duplicate_key": key,
        "book_ids": list(book_ids),
    }


def _with_visibility(module, visible_ids):
    """Pin the DB-backed visibility oracle to a fixed set so the pure
    group-drop logic can be exercised without a real library."""
    visible = {int(b) for b in visible_ids}
    module._visible_duplicate_book_ids = lambda book_ids, user_id: {
        int(b) for b in book_ids if int(b) in visible
    }


# ---------------------------------------------------------------------------
# Behaviour: cached groups re-validated against the user's current view
# ---------------------------------------------------------------------------

class TestGroupDropBehaviour:
    def test_pair_with_one_archived_book_is_dropped(self):
        """The reporter's case: a two-book duplicate group where one book has
        been archived is no longer a duplicate — it must leave the count."""
        module = _load()
        _with_visibility(module, {1})  # book 2 archived/hidden for this user
        out = module.filter_visible_duplicate_groups([_group(1, 2)], user_id=9)
        assert out == [], (
            "a duplicate pair with one book archived still counted in the "
            "status badge, leaving the notifier stuck in 'limbo' (#737)"
        )

    def test_fully_visible_pair_is_kept_unchanged(self):
        module = _load()
        _with_visibility(module, {1, 2})
        group = _group(1, 2)
        out = module.filter_visible_duplicate_groups([group], user_id=9)
        assert len(out) == 1
        assert out[0]["book_ids"] == [1, 2]
        assert out[0]["count"] == 2

    def test_triple_with_one_archived_is_kept_with_reduced_count(self):
        """Three copies, one archived → still a duplicate, count 3 → 2."""
        module = _load()
        _with_visibility(module, {1, 3})  # book 2 archived
        out = module.filter_visible_duplicate_groups([_group(1, 2, 3)], user_id=9)
        assert len(out) == 1
        assert out[0]["count"] == 2
        assert out[0]["book_ids"] == [1, 3]

    def test_mixed_groups_only_survivors_remain(self):
        module = _load()
        _with_visibility(module, {1, 10, 11})  # group A loses book 2; B intact
        groups = [_group(1, 2, title="A", key="KA"),
                  _group(10, 11, title="B", key="KB")]
        out = module.filter_visible_duplicate_groups(groups, user_id=9)
        assert [g["title"] for g in out] == ["B"]

    def test_visibility_resolved_with_one_query_not_per_group(self):
        """All groups' ids are resolved against the user's view in ONE visibility
        query (on the union of every group's ids), not one query per group. The
        notifier polls every few seconds per open admin/edit tab, so one query
        per poll scales with the union size, not with the duplicate-group count.
        Pins the batching so it can't silently regress to G queries per poll."""
        module = _load()
        calls = {"n": 0}
        visible = {1, 2, 10, 11}

        def _counting(book_ids, user_id):
            calls["n"] += 1
            return {int(b) for b in book_ids if int(b) in visible}

        module._visible_duplicate_book_ids = _counting
        groups = [_group(1, 2, title="A", key="KA"),
                  _group(10, 11, title="B", key="KB"),
                  _group(100, 101, title="C", key="KC")]  # both archived -> dropped
        out = module.filter_visible_duplicate_groups(groups, user_id=9)
        assert calls["n"] == 1, (
            "visibility must be resolved once for the whole set of groups (one "
            "query per poll), not once per group"
        )
        # Behaviour preserved: A and B kept, C dropped (no visible books).
        assert [g["title"] for g in out] == ["A", "B"]

    def test_anonymous_user_returns_cache_unchanged(self):
        """No concrete user → no per-user archive state to apply. The cache is
        passed through untouched (and the visibility oracle is never called)."""
        module = _load()
        called = {"hit": False}

        def _boom(book_ids, user_id):
            called["hit"] = True
            return set()

        module._visible_duplicate_book_ids = _boom
        groups = [_group(1, 2)]
        out = module.filter_visible_duplicate_groups(groups, user_id=None)
        assert out is groups
        assert called["hit"] is False

    def test_group_without_book_ids_is_kept(self):
        """An older cache shape without per-book ids can't be re-validated, so
        the group is kept rather than silently dropped."""
        module = _load()
        _with_visibility(module, set())
        legacy = {"title": "Old", "author": "A", "count": 2,
                  "group_hash": "H", "duplicate_key": "K"}  # no book_ids
        out = module.filter_visible_duplicate_groups([legacy], user_id=9)
        assert out == [legacy]

    def test_visibility_helper_coerces_and_short_circuits(self):
        """_visible_duplicate_book_ids returns all ids verbatim when there is no
        user (nothing to exclude), coercing str ids to int."""
        module = _load()
        assert module._visible_duplicate_book_ids(["1", "2", None], user_id=None) == {1, 2}


# ---------------------------------------------------------------------------
# Source pins — the wiring the behaviour depends on (fails on pre-fix code)
# ---------------------------------------------------------------------------

class TestSourcePins:
    def test_status_route_applies_visibility_filter(self):
        """The status endpoint must route its cached count through
        filter_visible_duplicate_groups — this is the line missing on main
        that let the badge count archived books (#737)."""
        m = re.search(r"def get_duplicate_status\(\):(.*?)\n@duplicates\.route",
                      DUP_SRC, re.S)
        assert m, "get_duplicate_status not found"
        body = m.group(1)
        assert "filter_visible_duplicate_groups(" in body, (
            "get_duplicate_status must re-validate cached groups against the "
            "user's current view (#737) before computing the badge count"
        )

    def test_visibility_helper_uses_common_filters_ssot(self):
        m = re.search(r"def _visible_duplicate_book_ids\(.*?\n(?=\ndef )",
                      DUP_SRC, re.S)
        assert m, "_visible_duplicate_book_ids not found"
        body = m.group(0)
        assert "get_common_filters(" in body, (
            "visibility must use get_common_filters — the same archived/hidden "
            "exclusion the /duplicates page applies (single source of truth)"
        )
