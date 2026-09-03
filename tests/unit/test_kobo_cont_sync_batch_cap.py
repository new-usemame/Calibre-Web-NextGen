# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Source pins for the Kobo local-continuation invariant.

PR #248 established the crucial wire behavior on a Kobo Forma running firmware
4.45.23684: ``x-kobo-sync: continue`` is a paging signal that makes firmware
keep its request cursor pinned, not a freshness signal.  It changed the books
writer from ``bool(book_count)`` to ``book_count > SYNC_ITEM_LIMIT``, fixing
the loop when the pending set fit in one page.

Fork #1634 exposed the remaining half of that contract.  A pending set larger
than the cap still emitted ``continue``; firmware therefore retained the old
token, and the server selected the same full page forever.  A local page can
advance only when the device persists the returned token, so no books,
reading-state, or deletion queue in ``HandleSyncRequest`` may set local
continuation.  The batch limits remain; each response ends the session and the
device starts its next session using the advanced cursor.

These source pins preserve #248's protocol finding while enforcing the
stronger invariant proved by #1634.  The real request/response behavior is
covered separately by ``test_kobo_cont_sync_firmware_cursor.py``.
"""

import ast
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


def _handle_sync_request_source():
    source = (Path(__file__).resolve().parents[2] / "cps" / "kobo.py").read_text()
    tree = ast.parse(source)
    handler = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "HandleSyncRequest"
    )
    return ast.get_source_segment(source, handler)


def _cont_sync_writers(source):
    tree = ast.parse(source)
    writers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "cont_sync"
            for target in node.targets
        ):
            writers.append(node)
        elif (
            isinstance(node, (ast.AugAssign, ast.AnnAssign))
            and isinstance(node.target, ast.Name)
            and node.target.id == "cont_sync"
        ):
            writers.append(node)
    return writers


def test_handle_sync_has_one_false_local_continuation_writer():
    """No local queue may pin the device cursor with ``continue``."""
    source = _handle_sync_request_source()
    writers = _cont_sync_writers(source)
    assert len(writers) == 1, (
        "HandleSyncRequest must initialize cont_sync exactly once and never "
        f"rewrite or aggregate it from a local queue; found {len(writers)} "
        f"writers: {[ast.dump(writer) for writer in writers]}"
    )
    writer = writers[0]
    assert isinstance(writer, ast.Assign)
    assert isinstance(writer.value, ast.Constant) and writer.value.value is False, (
        "The sole HandleSyncRequest cont_sync writer must be `False`; any "
        "local `True` value makes Kobo firmware pin the incoming cursor."
    )


def test_books_stay_page_capped_but_never_request_local_continuation():
    """#248's batch cap remains while #1634 removes its unsafe signal."""
    source = _handle_sync_request_source()
    snapshot = (
        "book_snapshot_ids = _capture_query_identities(\n"
        "        changed_entries, db.Books.id,\n"
        "    )"
    )
    page = (
        "_bounded_query_pages(\n"
        "            changed_entries,\n"
        "            book_snapshot_ids,"
    )
    terminal = "cont_sync = False"
    assert page in source
    assert snapshot in source
    assert source.index(snapshot) < source.index(page) < source.index(terminal)
    assert "cont_sync = bool(book_count" not in source


def test_reading_states_stay_page_capped_without_continuation_writer():
    """A full reading-state page must also let its returned cursor persist."""
    source = _handle_sync_request_source()
    assert (
        "reading_state_page = "
        "changed_reading_states.limit(SYNC_ITEM_LIMIT).all()"
    ) in source
    assert "for kobo_reading_state in reading_state_page:" in source
    assert "cont_sync |= bool(changed_reading_states" not in source
    assert "cont_sync = bool(changed_reading_states" not in source


def test_deletions_stay_page_capped_without_continuation_writer():
    """Deletion tombstones page via the persisted archive cursor, not a pin."""
    source = _handle_sync_request_source()
    pending_start = source.index("pending_deletions = (")
    pending_end = source.index("for deletion_page in _bounded_query_pages(")
    pending_query = source[pending_start:pending_end]
    assert "deletion_snapshot_ids = _capture_query_identities(" in pending_query
    assert (
        "pending_deletions,\n"
        "            deletion_snapshot_ids,"
        in source
    )
    assert "for tombstone in deletion_page:" in source
    assert "cont_sync = True" not in source
    assert "cont_sync |= " not in source
    assert "response = generate_sync_response(sync_token, sync_results)" in source
    assert source.index("response = generate_sync_response") < source.index(
        "if ub.session_commit() is False:"
    )
