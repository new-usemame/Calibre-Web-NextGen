# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for the v4.0.147 → v4.0.151 transitional-state fix
on fork #359.

@recruiterguy verified v4.0.147 deployed cleanly on their Synology
docker setup but reported the magic-shelf books still didn't deliver to
their Kobo device. Diagnosis: their user has
``kobo_only_shelves_sync=False`` (sync-all-library mode). The v4.0.147
fix gated the magic-shelf helper calls + the magic-shelf arm on
``kobo_only_shelves_sync=True``, so the else branch never gained the
membership arm. Combined with the magic_shelf_cache only being
refreshed by the helper (which was never called for this user), the
cache stayed stale and the arm-fire condition `cache.created_at >
sync_token.books_last_modified` evaluated False even after upgrade.

The v4.0.151 fix:

1. **Computes magic-shelf state in BOTH modes.** `magic_shelf_book_ids`
   and `magic_shelf_membership_added_at` are computed at the top of
   HandleSyncRequest, outside the `if kobo_only_shelves_sync` block.
   This forces the magic_shelf_cache TTL re-evaluation on every sync
   (the helper calls `get_books_for_magic_shelf` which rebuilds the
   cache after 30 minutes), so the cache.created_at advances on the
   first post-deploy sync.
2. **The magic-shelf arm fires in the else branch too.** A new filter
   `inner_cursor_filter_sync_all` composes `composite_keyset_books_only`
   with the magic-shelf-membership arm (when active). The else branch
   uses this filter instead of the bare `composite_keyset_books_only`,
   so magic-shelf-only books with old `Books.last_modified` get through
   the inner cursor when `cache.created_at > cursor.lm`.
3. **Deletion detection stays gated.** Legacy sync-all users still want every
   global book, while shelf-only users and accounts with My Library enabled
   reconcile the device against their respective sets.
"""

import ast
import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
KOBO_PY = REPO_ROOT / "cps" / "kobo.py"


def _function_source(path: pathlib.Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == name),
        None,
    )
    assert fn is not None, f"{name} function not found in {path}"
    return ast.unparse(fn)


@pytest.mark.unit
class TestMagicShelfStateComputedInBothModes:
    def test_helper_call_is_NOT_inside_kobo_only_shelves_sync_block(self):
        """The v4.0.147 bug was that `get_magic_shelf_book_ids_for_kobo`
        was inside the `if current_user.kobo_only_shelves_sync:` block.
        For users in 'sync-all' mode the helper was never called, so the
        cache never refreshed and the arm never fired. Pin that the
        helper call is now OUTSIDE that gate."""
        src = _function_source(KOBO_PY, "HandleSyncRequest")
        tree = ast.parse(src)
        # Walk the function body. Find the `if current_user.kobo_only_shelves_sync`
        # node and check that `get_magic_shelf_book_ids_for_kobo(...)` is NOT
        # a Call inside its body. (It must be at the function-level scope.)
        for node in ast.walk(tree):
            if (isinstance(node, ast.If)
                    and isinstance(node.test, ast.Attribute)
                    and isinstance(node.test.value, ast.Name)
                    and node.test.value.id == "current_user"
                    and node.test.attr == "kobo_only_shelves_sync"):
                # Walk the body and assert no get_magic_shelf_book_ids_for_kobo call.
                for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                    if (isinstance(inner, ast.Call)
                            and isinstance(inner.func, ast.Name)
                            and inner.func.id == "get_magic_shelf_book_ids_for_kobo"):
                        pytest.fail(
                            "get_magic_shelf_book_ids_for_kobo is still "
                            "called inside the if kobo_only_shelves_sync "
                            "block. That's the v4.0.147 regression vector "
                            "for @recruiterguy. Move it outside."
                        )

    def test_helper_call_is_unconditional_at_function_level(self):
        """The helper must be called at the function body level
        (unconditionally for every sync)."""
        src = _function_source(KOBO_PY, "HandleSyncRequest")
        # Match the literal call in the function source. It must appear
        # NOT prefixed by `if current_user.kobo_only_shelves_sync:`.
        assert "get_magic_shelf_book_ids_for_kobo(current_user.id)" in src, (
            "HandleSyncRequest must call get_magic_shelf_book_ids_for_kobo "
            "unconditionally (not gated on kobo_only_shelves_sync) so the "
            "cache TTL re-evaluation runs on every sync — fork #359 "
            "v4.0.147 → v4.0.151 fix for the sync-all-mode case."
        )
        # #468: the call now also returns a reliability flag that gates the
        # two-way-sync deletion path (fail-safe — never archive on a failed
        # membership query).
        assert "magic_shelf_membership_reliable" in src, (
            "HandleSyncRequest must capture the membership-reliability flag "
            "from get_magic_shelf_book_ids_for_kobo (fork #468 fail-safe)."
        )
        assert "magic_shelf_membership_added_at = get_magic_shelf_membership_added_at(current_user.id)" in src, (
            "HandleSyncRequest must call get_magic_shelf_membership_added_at "
            "unconditionally to populate the cache-built-at timestamp the "
            "arm activation gates on."
        )


@pytest.mark.unit
class TestSyncAllBranchHasMagicShelfArm:
    def test_else_branch_uses_filter_that_includes_magic_shelf_arm(self):
        """The else branch (kobo_only_shelves_sync=False) must use a
        filter that composes the composite keyset WITH the magic-shelf
        membership arm — not the bare composite keyset that v4.0.147
        used."""
        src = _function_source(KOBO_PY, "HandleSyncRequest")
        # Pin a named filter for the sync-all branch that's distinct
        # from the bookshelf-joined filter (so the date_added arm isn't
        # accidentally added to a query that doesn't join BookShelf).
        assert "inner_cursor_filter_sync_all" in src, (
            "HandleSyncRequest must build a named filter "
            "'inner_cursor_filter_sync_all' for the kobo_only_shelves_sync="
            "False branch that includes the magic-shelf membership arm. "
            "Without it, magic-shelf-only books with old "
            "Books.last_modified don't deliver in sync-all mode "
            "(@recruiterguy's v4.0.147 verification regression)."
        )

    def test_sync_all_filter_excludes_date_added_arm(self):
        """The sync-all branch's SELECT doesn't join BookShelf, so the
        BookShelf.date_added arm must NOT appear in this branch's
        filter — that would produce a SQL error."""
        src = KOBO_PY.read_text(encoding="utf-8")
        # Pin the active arm of the assignment ONLY — stop at the next
        # closing `)`/`else:` so we don't span into the unrelated
        # `only_kobo_shelves` branch's query construction.
        idx = src.find("inner_cursor_filter_sync_all = or_(")
        assert idx != -1, "inner_cursor_filter_sync_all = or_(...) assignment not found"
        # Find the matching `)` of the or_( call. Use a depth counter.
        depth = 0
        end = idx
        for i, ch in enumerate(src[idx:], start=idx):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        assert end > idx, "could not find end of or_(...)"
        chunk = src[idx:end]
        assert "BookShelf.date_added" not in chunk, (
            "inner_cursor_filter_sync_all must NOT reference "
            "BookShelf.date_added — the else-branch query doesn't join "
            "BookShelf, so referencing it would SQL-error.\n"
            f"Filter assignment:\n{chunk}"
        )

    def test_only_kobo_shelves_filter_keeps_date_added_arm(self):
        """The only_kobo_shelves branch DOES join BookShelf, so the
        date_added arm stays there. Pin to prevent accidental
        removal."""
        src = KOBO_PY.read_text(encoding="utf-8")
        idx = src.find("inner_cursor_filter_with_bookshelf = or_(")
        assert idx != -1, "inner_cursor_filter_with_bookshelf assignment not found"
        chunk = src[idx:idx + 600]
        assert "BookShelf.date_added > cursor_lm" in chunk, (
            "inner_cursor_filter_with_bookshelf must keep the "
            "BookShelf.date_added > cursor_lm arm (fork #220 "
            "date_added re-match termination)."
        )


@pytest.mark.unit
class TestDeletionDetectionStaysGated:
    def test_deletion_detection_is_gated_by_shelves_or_my_library(self):
        """Reconciliation runs for shelf-only sync and for an enabled
        My Library, but remains absent from legacy sync-all accounts."""
        src = _function_source(KOBO_PY, "HandleSyncRequest")
        assert "membership_enabled = bool(getattr(current_user, 'has_own_library', False))" in src
        assert "if current_user.kobo_only_shelves_sync or membership_enabled:" in src
        gate = src.split(
            "if current_user.kobo_only_shelves_sync or membership_enabled:", 1
        )[1].split("only_kobo_shelves =", 1)[0]
        assert "synced_book_ids" in gate and "books_to_delete_ids" in gate
        assert "allowed_book_ids &= library_book_ids" in gate
