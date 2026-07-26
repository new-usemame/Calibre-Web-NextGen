# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Tests that are known-red and are skipped with a ticket attached.

This file exists to make "not running" cost something. Before #1105 a test
could stop running by *omission* — a file under ``tests/unit/`` without
``@pytest.mark.unit`` was collected and then deselected by the Fast Tests
gate's ``-m "smoke or unit"``, silently and permanently. 973 tests had drifted
out that way, and 37 of them had gone red without anyone seeing it.

Now the lane comes from the directory (``tests/conftest.py``), so the only way
out of the gate is this file: an explicit line, naming the test, the reason,
and the issue that tracks fixing it. A guard test in
``tests/unit/test_ci_test_lanes.py`` enforces the issue reference.

Entries are debt. Deleting one is the goal; adding one should feel worse than
fixing the test.
"""

#: Tracks every entry below.
TRIAGE_ISSUE = "#1106"

_SHIM_ROT = (
    "spec_from_file_location shim is stale: it registers a stub `cps` in "
    "sys.modules with no __path__, so relative imports the module has gained "
    "since the shim was written ('.metadata_constants', '.duplicates') fail to "
    "resolve. The app's own imports are fine — this is test-harness rot, and it "
    "went unseen for as long as the test was deselected. " + TRIAGE_ISSUE
)

_NEVER_RAN = (
    "asserts against oauth_bb.{github,google,generic}_logged_in as module "
    "attributes, but all three are nested inside init_oauth_blueprints() "
    "(cps/oauth_bb.py:922) and are only ever bound as oauth_authorized signal "
    "receivers. This assertion cannot have passed at any point — the test was "
    "written, never run, and never known to be wrong. " + TRIAGE_ISSUE
)

#: node id -> why it is skipped. Every reason must name an issue.
QUARANTINED = {
    # --- shim rot: the duplicate-detection module loaders (30) -------------
    "tests/unit/test_duplicate_delete_index_maintenance.py::test_auto_resolve_duplicates_deletes_duplicate_keys_and_refreshes_cache": _SHIM_ROT,
    "tests/unit/test_duplicate_delete_index_maintenance.py::test_delete_book_from_table_format_only_keeps_duplicate_keys_and_invalidates_cache": _SHIM_ROT,
    "tests/unit/test_duplicate_delete_index_maintenance.py::test_delete_book_from_table_whole_book_deletes_duplicate_keys_and_refreshes_cache": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_build_book_key_parts_matches_python_duplicate_fallbacks": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_cache_merge_deletes_key_rows_for_missing_candidate_ids": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_cache_merge_keeps_serialization_shape": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_cache_merge_preserves_retained_group_book_ids": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_effective_criteria_falls_back_to_title_author": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_fingerprint_changes_when_effective_criteria_change": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_grouped_index_queries_and_dismissed_filtering": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_has_valid_duplicate_index_baseline_allows_initial_incremental_when_candidates_cover_library": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_has_valid_duplicate_index_baseline_requires_candidate_to_cover_missing_current_book": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_has_valid_duplicate_index_baseline_states": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_initial_manual_full_scan_not_needed_during_dirty_ingest": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_manual_full_scan_needed_for_old_missing_book_even_during_dirty_ingest": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_manual_full_scan_not_needed_for_new_books_during_dirty_ingest": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_manual_full_scan_not_needed_for_new_books_during_running_ingest_follow_up": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_manual_full_scan_not_needed_when_pending_cache_has_complete_index": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_manual_full_scan_not_needed_while_ingest_active": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_mark_duplicate_index_pending_sets_cache_pending": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_rebuild_duplicate_index_replaces_active_fingerprint_and_removes_orphans": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_title_only_key_parts_still_strip_primary_author_prefix": _SHIM_ROT,
    "tests/unit/test_duplicate_index.py::test_upsert_and_delete_book_keys": _SHIM_ROT,
    "tests/unit/test_duplicate_scan_index_rewire.py::test_execute_resolution_blocks_while_ingest_pending": _SHIM_ROT,
    "tests/unit/test_duplicate_scan_index_rewire.py::test_manual_trigger_blocks_full_scan_while_ingest_pending": _SHIM_ROT,
    "tests/unit/test_duplicate_scan_queue_settings.py::test_cwa_settings_criteria_change_marks_duplicate_index_pending": _SHIM_ROT,
    "tests/unit/test_duplicate_scan_queue_settings.py::test_cwa_settings_unchanged_criteria_does_not_mark_pending": _SHIM_ROT,
    "tests/unit/test_duplicate_scan_queue_settings.py::test_direct_duplicate_queue_helper_defaults_to_settings": _SHIM_ROT,
    "tests/unit/test_duplicate_scan_queue_settings.py::test_internal_duplicate_queue_defaults_to_sixty_second_debounce": _SHIM_ROT,
    "tests/unit/test_duplicate_scan_queue_settings.py::test_internal_duplicate_queue_passes_coalesced_book_ids": _SHIM_ROT,

    # --- never ran, so never known to be wrong (4) -------------------------
    "tests/unit/test_oauth_session.py::TestOAuthLogic::test_generic_logged_in_aborts": _NEVER_RAN,
    "tests/unit/test_oauth_session.py::TestOAuthLogic::test_github_logged_in_aborts": _NEVER_RAN,
    "tests/unit/test_oauth_session.py::TestOAuthLogic::test_google_logged_in_aborts": _NEVER_RAN,
    "tests/unit/test_oauth_session.py::TestOAuthLogic::test_register_user_uses_manual_session": _NEVER_RAN,
}
