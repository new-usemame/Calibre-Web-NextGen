# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pin the Kobo per-callsite ``common_filters`` policy decisions
(audit 2026-05-11, item B4).

Background: ``calibre_db.get_book_by_uuid`` bypasses ``common_filters``.
On the Kobo blueprint that means we can hand back books the user
shouldn't see (hidden, denied-tag, language-filtered, custom-column-
restricted) — which is wrong for some endpoints and right for others
because the Kobo is a device-trailing surface for already-synced books.

The audit decided per-callsite, documented in
``notes/KOBO-B4-COMMON-FILTERS-POLICY.md``:

| Line | Endpoint | Enforce filters? |
|------|----------|-----------------|
| 469  | metadata GET | YES |
| 750  | shelf-add | YES |
| 822  | shelf-remove | NO |
| 949  | state GET/PUT | NO |
| 1244 | book DELETE | NO |

These tests pin both:

1. The helper ``get_book_by_uuid_for_kobo(book_uuid, *, enforce_policy)``
   exists, requires ``enforce_policy`` as a keyword arg, and applies
   ``common_filters(allow_show_archived=True)`` when ``enforce_policy=True``.
2. Each callsite in cps/kobo.py uses the helper with the policy-doc
   decision baked in.
"""

import inspect

import pytest


@pytest.mark.unit
class TestHelperContract:
    def test_helper_exists_on_calibre_db(self):
        from cps.db import CalibreDB
        assert hasattr(CalibreDB, "get_book_by_uuid_for_kobo"), (
            "Missing helper. cps/db.py must expose "
            "get_book_by_uuid_for_kobo for the Kobo blueprint."
        )

    def test_enforce_policy_is_keyword_only(self):
        from cps.db import CalibreDB
        sig = inspect.signature(CalibreDB.get_book_by_uuid_for_kobo)
        param = sig.parameters.get("enforce_policy")
        assert param is not None
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            "enforce_policy must be keyword-only so callers can't "
            "accidentally pass the wrong polarity positionally — the "
            "kwarg name is the policy documentation."
        )

    def test_helper_applies_common_filters_when_enforced(self):
        """Source-level: when enforce_policy=True we add a
        ``self.common_filters(allow_show_archived=True)`` filter."""
        from cps.db import CalibreDB
        src = inspect.getsource(CalibreDB.get_book_by_uuid_for_kobo)
        assert "common_filters" in src
        assert "allow_show_archived=True" in src, (
            "Kobo path must pass allow_show_archived=True — the "
            "ArchivedBook table doubles as the Kobo's device-deletion "
            "track. Filtering on it would 404 the device's own "
            "deletions, breaking sync."
        )

    def test_helper_skips_filters_when_not_enforced(self):
        from cps.db import CalibreDB
        src = inspect.getsource(CalibreDB.get_book_by_uuid_for_kobo)
        # Logic should branch on the flag — naive query with no filter
        # when enforce_policy is False.
        assert "if enforce_policy" in src, (
            "Helper must branch on enforce_policy; otherwise it's not "
            "actually implementing the policy."
        )


@pytest.mark.unit
class TestPerCallsitePolicy:
    """Each Kobo callsite must use the helper with the exact policy
    documented in notes/KOBO-B4-COMMON-FILTERS-POLICY.md. These tests
    inspect the source of the relevant handler so a refactor can't
    silently flip the policy."""

    def test_handle_metadata_request_enforces(self):
        from cps.kobo import HandleMetadataRequest
        src = inspect.getsource(HandleMetadataRequest)
        assert "get_book_by_uuid_for_kobo" in src
        assert "enforce_policy=True" in src, (
            "HandleMetadataRequest must ENFORCE filters — metadata is "
            "a policy boundary symmetric with web/OPDS. Books the user "
            "can't see shouldn't expose metadata via Kobo."
        )

    def test_add_items_to_shelf_enforces(self):
        from cps.kobo import add_items_to_shelf
        src = inspect.getsource(add_items_to_shelf)
        assert "get_book_by_uuid_for_kobo" in src
        assert "enforce_policy=True" in src, (
            "add_items_to_shelf must ENFORCE filters — adding a "
            "denied/hidden book to a Kobo-synced shelf would leak it "
            "to every Kobo synced from the account."
        )

    def test_handle_tag_remove_item_does_not_enforce(self):
        from cps.kobo import HandleTagRemoveItem
        src = inspect.getsource(HandleTagRemoveItem)
        assert "get_book_by_uuid_for_kobo" in src
        assert "enforce_policy=False" in src, (
            "HandleTagRemoveItem must NOT enforce filters — destructive "
            "user-initiated cleanup. Blocking on policy would leave the "
            "book on the shelf forever."
        )

    def test_handle_state_request_does_not_enforce(self):
        from cps.kobo import HandleStateRequest
        src = inspect.getsource(HandleStateRequest)
        assert "get_book_by_uuid_for_kobo" in src
        assert "enforce_policy=False" in src, (
            "HandleStateRequest must NOT enforce filters — device-"
            "trailing surface for already-synced books. Filtering would "
            "loop the device on 4xx retries."
        )

    def test_handle_book_deletion_does_not_enforce(self):
        from cps.kobo import HandleBookDeletionRequest
        src = inspect.getsource(HandleBookDeletionRequest)
        assert "get_book_by_uuid_for_kobo" in src
        assert "enforce_policy=False" in src, (
            "HandleBookDeletionRequest must NOT enforce filters — "
            "destructive user-initiated cleanup."
        )


@pytest.mark.unit
class TestNoLegacyBypass:
    """Audit invariant: no Kobo callsite is still using
    get_book_by_uuid (the unfiltered helper). All five known sites
    must go through get_book_by_uuid_for_kobo with an explicit policy."""

    def test_the_two_kobo_cover_helpers_enforce_policy(self):
        """F-277165: both UUID cover helpers must stay policy-aware.

        These live in ``cps/helper.py``, not ``cps/kobo.py``, so the audit
        below never looked at them -- and that is exactly how the finding
        happened: a numbered ``/cover/<id>`` request went through
        ``common_filters`` while the Kobo UUID path resolved the book with the
        raw lookup, so a request naming a restricted book's UUID was answered
        from that book instead of refused.

        #1947 fixed it by routing both through
        ``get_book_by_uuid_for_kobo(..., enforce_policy=True)``, which now also
        carries the per-user library predicate.  Nothing pinned that, so
        reverting either line reintroduced a security bug with a green suite.
        """
        import cps.helper as helper_module

        for name in ("get_book_cover_with_uuid", "get_kobo_cover_source_path"):
            src = inspect.getsource(getattr(helper_module, name))
            assert "calibre_db.get_book_by_uuid_for_kobo(" in src, (
                "{}() must resolve the book through the policy-aware "
                "lookup, not the raw one (F-277165)".format(name)
            )
            assert "enforce_policy=True" in src, (
                "{}() must pass enforce_policy=True: a cover is a policy "
                "boundary, not a device-trailing state endpoint "
                "(F-277165)".format(name)
            )
            unfiltered = [
                ln for ln in src.splitlines()
                if "calibre_db.get_book_by_uuid(" in ln
                and "get_book_by_uuid_for_kobo" not in ln
            ]
            assert not unfiltered, (
                "{}() still calls the unfiltered lookup:\n  {}".format(
                    name, "\n  ".join(unfiltered))
            )

    def test_no_unfiltered_get_book_by_uuid_in_kobo(self):
        import cps.kobo as kobo_module
        src = inspect.getsource(kobo_module)
        # The unfiltered helper itself is a sibling of the new one;
        # we want to ensure no caller in kobo.py uses it.
        lines = [
            ln for ln in src.splitlines()
            if "calibre_db.get_book_by_uuid(" in ln
            and "get_book_by_uuid_for_kobo" not in ln
        ]
        assert not lines, (
            "Found Kobo callsite(s) still using the unfiltered "
            "calibre_db.get_book_by_uuid:\n  "
            + "\n  ".join(lines)
            + "\nAll five known callsites must route through "
            "get_book_by_uuid_for_kobo with an explicit "
            "enforce_policy= kwarg."
        )


@pytest.mark.unit
class TestHelperBehaviorIntegration:
    """Light integration: bypass Flask request context by mocking the
    pieces ``common_filters`` reaches for (current_user, ub.session)."""

    def test_unenforced_path_skips_common_filters(self, mocker):
        """When enforce_policy=False, we must NOT call common_filters
        at all — proven by mocking it and asserting it's never invoked.
        """
        from cps.db import CalibreDB

        mock_filter = mocker.MagicMock()
        mocker.patch.object(CalibreDB, "common_filters", mock_filter)

        mock_query_chain = mocker.MagicMock()
        mock_query_chain.filter.return_value = mock_query_chain
        mock_query_chain.first.return_value = None

        instance = mocker.MagicMock()
        instance.ensure_session = mocker.MagicMock()
        instance.session.query.return_value = mock_query_chain
        instance.common_filters = mock_filter

        CalibreDB.get_book_by_uuid_for_kobo(
            instance, "abc-uuid", enforce_policy=False,
        )

        mock_filter.assert_not_called()

    def test_enforced_path_invokes_common_filters_with_archive_visible(
            self, mocker):
        from cps.db import CalibreDB

        mock_filter = mocker.MagicMock()
        mock_filter.return_value = "sentinel-filter-expr"

        mock_query_chain = mocker.MagicMock()
        mock_query_chain.filter.return_value = mock_query_chain
        mock_query_chain.first.return_value = None

        instance = mocker.MagicMock()
        instance.ensure_session = mocker.MagicMock()
        instance.session.query.return_value = mock_query_chain
        instance.common_filters = mock_filter

        CalibreDB.get_book_by_uuid_for_kobo(
            instance, "abc-uuid", enforce_policy=True,
        )

        mock_filter.assert_called_once_with(allow_show_archived=True)
