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

_EDITBOOKS_SHIM_ROT = (
    "spec_from_file_location editbooks shim is stale: its Flask stub does not "
    "provide copy_current_request_context, so cps/editbooks.py fails during "
    "module import before either test can execute. " + TRIAGE_ISSUE
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
    # --- shim rot: the duplicate-detection module loaders (29) -------------
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
