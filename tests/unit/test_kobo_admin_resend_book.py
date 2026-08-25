# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for A2 — per-book "Resend to Kobo" admin action.

Goal:
    Admin clicks "Resend" on user_edit.html, the (user_id, book_id)
    row in kobo_synced_books is cleared, and Books.last_modified is
    bumped to NOW. On the user's next Kobo sync, the device receives
    the book again (as NewEntitlement since the synced-row is gone)
    and re-downloads the file.

Why both:
    The row deletion alone isn't enough — if the cursor has advanced
    past Books.last_modified, the sync filter
    ``Books.last_modified > sync_token.books_last_modified`` excludes
    the book and the device never sees it again. The fix must do
    both: clear the per-user sync record AND bump last_modified.

These tests pin the implementation at the source-text level (route
shape, function shape) so a future refactor that drops one of the
two writes silently re-introduces a partial-fix bug.
"""

import inspect

import pytest


@pytest.mark.unit
class TestRouteRegistered:
    def test_kobo_resend_endpoint_is_admin_only(self):
        from cps import admin as admin_mod
        src = inspect.getsource(admin_mod)
        # The route must require admin (admin_required decorator).
        # Pinning the exact route + decorator stack catches refactors
        # that accidentally drop the admin gate, which would let any
        # logged-in user resend books to any other user's Kobo.
        assert "/ajax/kobo_resend/" in src, (
            "admin module must register /ajax/kobo_resend/<userid>/<bookid> "
            "route for the per-book resend action."
        )
        # Find the route registration and verify decorator stack
        idx = src.index("/ajax/kobo_resend/")
        # Look at the ~400 chars around the route for the decorator stack.
        window = src[idx:idx + 400]
        assert "@admin_required" in window, (
            "/ajax/kobo_resend/<userid>/<bookid> must be @admin_required. "
            "Without this gate any logged-in user could clear another "
            "user's Kobo sync state."
        )
        assert "methods=[\"POST\"]" in window, (
            "/ajax/kobo_resend/<userid>/<bookid> must be POST-only."
        )

    def test_endpoint_passes_userid_and_bookid(self):
        from cps.admin import ajax_kobo_resend
        # The handler signature must take both user and book parameters
        # so the SQL filter narrows to the (user, book) pair.
        sig = inspect.signature(ajax_kobo_resend)
        params = list(sig.parameters)
        assert params == ["userid", "bookid"], (
            "ajax_kobo_resend must accept (userid, bookid) so it can "
            "delete the exact (user_id, book_id) row from "
            "kobo_synced_books."
        )


@pytest.mark.unit
class TestDoKoboResendShape:
    """Source-pinned: the helper must perform two writes — clear the
    sync row AND bump last_modified. Either one alone is a partial fix
    that doesn't restore device-side delivery."""

    def test_clears_kobo_synced_books_row_for_pair(self):
        from cps.admin import do_kobo_resend
        src = inspect.getsource(do_kobo_resend)
        assert "ub.KoboSyncedBooks" in src, (
            "do_kobo_resend must touch the KoboSyncedBooks table. NOTE the "
            "claim this message used to make — that without the deletion the "
            "sync emits ChangedEntitlement rather than re-delivering — is "
            "FALSE as a general rule and this assertion never checked it "
            "(F-cc5efb). get_kobo_created_ts decides the entitlement type from "
            "Books.timestamp/date_added alone. The deletion matters only "
            "because emptying the user's kobo_synced_books resets the whole "
            "sync token; see test_the_entitlement_type_does_not_consult_the_"
            "synced_books_table below, which executes that instead of "
            "spelling it."
        )
        assert ".delete()" in src, (
            "do_kobo_resend must call .delete() on the filtered query "
            "so the (user_id, book_id) row goes away."
        )
        # Both filters must be present so we delete only the targeted
        # pair (not all rows for the user or all rows for the book).
        assert "user_id" in src and "book_id" in src, (
            "do_kobo_resend must filter the delete by both user_id "
            "AND book_id; deleting all rows for a user is "
            "do_full_kobo_sync's job, and deleting all rows for a "
            "book is remove_synced_book(all=True)."
        )

    def test_bumps_last_modified_with_aware_datetime(self):
        from cps.admin import do_kobo_resend
        src = inspect.getsource(do_kobo_resend)
        assert "last_modified" in src, (
            "do_kobo_resend must bump Books.last_modified so the sync "
            "filter `Books.last_modified > books_last_modified` picks "
            "up the book even when the cursor has advanced past the "
            "book's original mtime."
        )
        # Use timezone-aware UTC datetime to match the cps/editbooks
        # canonical writer pattern (datetime.now(timezone.utc)).
        assert "datetime.now(timezone.utc)" in src, (
            "do_kobo_resend must bump last_modified using "
            "datetime.now(timezone.utc) for parity with editbooks.py "
            "writers — naive timestamps drift across DST boundaries "
            "and can land in the past relative to the sync cursor."
        )

    def test_validates_book_exists_before_writing(self):
        from cps.admin import do_kobo_resend
        src = inspect.getsource(do_kobo_resend)
        # The book existence check must happen so an admin entering an
        # invalid book ID gets feedback rather than a silent no-op.
        assert "calibre_db.session.query(db.Books)" in src, (
            "do_kobo_resend must verify the book exists in the calibre "
            "library before bumping last_modified — otherwise an "
            "invalid book ID silently no-ops."
        )

    def test_commits_both_sessions(self):
        from cps.admin import do_kobo_resend
        src = inspect.getsource(do_kobo_resend)
        # Both writes go to different SQLAlchemy sessions — calibre_db
        # for Books.last_modified, ub for KoboSyncedBooks — so both
        # need explicit commits.
        assert "calibre_db.session.commit()" in src, (
            "do_kobo_resend must commit calibre_db.session — without "
            "the commit the Books.last_modified bump is lost on the "
            "next session expire/rollback."
        )
        assert "ub.session_commit" in src, (
            "do_kobo_resend must commit ub.session — without the "
            "commit the KoboSyncedBooks deletion is rolled back."
        )


@pytest.mark.unit
class TestSecurityShape:
    """Defensive: the route accepts integer IDs, requires admin auth,
    and doesn't expose either user or book IDs to non-admins."""

    def test_route_uses_int_converters(self):
        from cps import admin as admin_mod
        src = inspect.getsource(admin_mod)
        # The route signature must use <int:...> not <...> so Flask
        # rejects non-integer paths before reaching our handler. This
        # makes SQL injection on the path effectively impossible.
        idx = src.index("/ajax/kobo_resend/")
        window = src[idx:idx + 100]
        assert "/<int:userid>/<int:bookid>" in window, (
            "/ajax/kobo_resend route must use <int:userid>/<int:bookid> "
            "Flask converters to reject non-numeric paths at the "
            "routing layer."
        )

    def test_route_is_user_login_required_and_admin_required(self):
        from cps import admin as admin_mod
        src = inspect.getsource(admin_mod)
        idx = src.index("/ajax/kobo_resend/")
        window = src[idx:idx + 400]
        assert "@user_login_required" in window, (
            "/ajax/kobo_resend route must require login."
        )
        assert "@admin_required" in window, (
            "/ajax/kobo_resend route must require admin."
        )


@pytest.mark.unit
class TestWhatActuallyDecidesTheEntitlementType:
    """Executed, because the source pin above never could.

    The assertion that `do_kobo_resend` mentions `ub.KoboSyncedBooks` advertised
    a causal chain — delete the row, get a NewEntitlement — that this codebase
    does not implement. It passed forever because the identifier is spelled in
    the function, and the wrong belief it encoded was copied into a production
    comment (F-cc5efb, and F-3e383a for the consequence).

    These run the real decision function instead.
    """

    def test_the_entitlement_type_does_not_consult_the_synced_books_table(self):
        """`get_kobo_created_ts` is the whole decision, and it reads timestamps.

        Deleting a kobo_synced_books row cannot change what this returns, so it
        cannot by itself turn a ChangedEntitlement into a NewEntitlement.
        """
        from datetime import datetime
        from types import SimpleNamespace

        from cps.kobo import get_kobo_created_ts

        created = datetime(2020, 1, 1)
        book = SimpleNamespace(
            Books=SimpleNamespace(id=1, timestamp=created, last_modified=datetime(2026, 1, 1)),
            date_added=None,
        )
        assert get_kobo_created_ts(book) == created, (
            "the creation stamp moved when only last_modified changed; the "
            "entitlement type would then follow last_modified"
        )

    def test_bumping_last_modified_alone_does_not_move_the_creation_stamp(self):
        """This is what `do_kobo_resend` does to the book, and it is not enough.

        It bumps `Books.last_modified` only. The creation stamp the entitlement
        type is derived from is untouched, so a book the device has already seen
        still classifies as Changed.
        """
        from datetime import datetime
        from types import SimpleNamespace

        from cps.kobo import get_kobo_created_ts

        created = datetime(2020, 1, 1)
        before = get_kobo_created_ts(SimpleNamespace(
            Books=SimpleNamespace(id=1, timestamp=created, last_modified=datetime(2021, 1, 1)),
            date_added=None))
        after = get_kobo_created_ts(SimpleNamespace(
            Books=SimpleNamespace(id=1, timestamp=created, last_modified=datetime(2026, 6, 1)),
            date_added=None))
        assert before == after == created

    def test_a_genuinely_newer_creation_stamp_does_move_it(self):
        """Vacuity guard.

        The two assertions above would also hold if `get_kobo_created_ts`
        returned a constant. Pin that it does track the field it claims to.
        """
        from datetime import datetime
        from types import SimpleNamespace

        from cps.kobo import get_kobo_created_ts

        old = get_kobo_created_ts(SimpleNamespace(
            Books=SimpleNamespace(id=1, timestamp=datetime(2020, 1, 1), last_modified=None),
            date_added=None))
        new = get_kobo_created_ts(SimpleNamespace(
            Books=SimpleNamespace(id=1, timestamp=datetime(2026, 1, 1), last_modified=None),
            date_added=None))
        assert new > old

    def test_a_shelf_date_added_can_also_move_it(self):
        """The other input, so "timestamp only" is not overstated either."""
        from datetime import datetime
        from types import SimpleNamespace

        from cps.kobo import get_kobo_created_ts

        stamp = get_kobo_created_ts(SimpleNamespace(
            Books=SimpleNamespace(id=1, timestamp=datetime(2020, 1, 1), last_modified=None),
            date_added=datetime(2026, 1, 1)))
        assert stamp == datetime(2026, 1, 1)
