# SPDX-License-Identifier: GPL-3.0-or-later
"""F-65c93f: the SPA cover endpoint's two policy gaps.

``cps/api/edit.py::set_cover`` sits between two neighbours in its own module
that both pass ``allow_show_archived=True, allow_show_hidden=True`` (:421,
:439), and next to a cover *picker* that refuses to write while the per-book
cover lock is set (janeczku/calibre-web#2165).  It did neither, so:

1. replacing the cover of your OWN hidden or archived book answered 404, even
   though the edit page for that book opens fine; and
2. a cover you deliberately locked could be replaced from the new UI, while the
   same action through the picker or the classic editor was refused.

Both are asserted at source level on purpose.  The endpoint's body is a chain
of filesystem writes and metadata-change bookkeeping that a unit test cannot
drive without a real library on disk; what actually regressed here is which
lookup and which guard the handler chooses, and that is exactly what these
assertions pin.  See F-cc5efb for why a source-pinned test is a floor: the
runtime behaviour is covered by the integration lane.
"""

from __future__ import annotations

import inspect

import pytest

from cps.api import edit as edit_module


pytestmark = pytest.mark.unit


def _set_cover_source():
    return inspect.getsource(edit_module.set_cover)


def test_set_cover_resolves_the_book_the_way_its_neighbours_do():
    """A user's own hidden or archived book must not 404 on cover replace."""
    source = _set_cover_source()
    assert "get_filtered_book(" in source, "set_cover no longer resolves a book"
    assert "allow_show_archived=True" in source and "allow_show_hidden=True" in source, (
        "set_cover resolves the book with strict defaults while its siblings in "
        "this module pass allow_show_archived/allow_show_hidden, so the edit "
        "page opens and the cover write 404s (F-65c93f)"
    )


def test_the_lock_helper_is_actually_reachable_from_this_module():
    """Guard against the failure the source assertions below cannot see.

    ``cps/api/edit.py`` imports individual NAMES from ``..helper``; it never
    binds the module.  So a guard written as ``helper.book_cover_is_locked(...)``
    reads correctly, satisfies every source-level assertion in this file, and
    raises ``NameError`` on the first real request.  This test is the reason the
    others are safe to write at source level.
    """
    assert callable(getattr(edit_module, "book_cover_is_locked", None)), (
        "set_cover's lock guard must resolve to a real callable in this "
        "module's namespace, not a module attribute that was never imported"
    )


def test_set_cover_refuses_to_overwrite_a_locked_cover():
    """The lock is a user decision; every write path must honour it."""
    source = _set_cover_source()
    assert "book_cover_is_locked(" in source, (
        "set_cover writes a cover without consulting the per-book cover lock, "
        "so the new UI can overwrite a cover the picker and the classic editor "
        "both refuse to touch (F-65c93f)"
    )
    assert "409" in source, (
        "a locked cover must be refused with 409, matching "
        "cover_picker.cover_picker_apply"
    )


def test_the_lock_is_checked_before_any_bytes_are_written():
    """Order matters: refusing after the write has already replaced the file
    would leave the lock honoured in the response and violated on disk."""
    source = _set_cover_source()
    lock_at = source.index("book_cover_is_locked")
    writes = [source.find("save_cover("), source.find("save_cover_from_url(")]
    writes = [position for position in writes if position != -1]
    assert writes, "set_cover no longer calls the cover-writing helpers"
    assert lock_at < min(writes), (
        "the cover lock is consulted after a write path has already run"
    )
