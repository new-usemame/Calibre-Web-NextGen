# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for upstream CWA #1324 (fork PR #101) — KOReader sync
must push reading progress to Hardcover, the same way the Kobo path does.

Pre-fix: `push_reading_state_to_hardcover` was Kobo-only, signed
`(book, request_bookmark)` and used `current_user`. KOReader sync (which
runs without a `current_user` context) couldn't call it.

Post-fix: signature is `(user, book, progress_percentage)` — explicit
user passed in — and `kosync.update_progress` calls it after every
successful progress update where `book_id` resolved to a Calibre book.
"""

import inspect
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.mock_hardcover_client import MockHardcoverClient
from cps.services import hardcover_progress


def _kosync_module():
    """Fetch the kosync *module* (not the re-exported Blueprint) — the
    package `__init__.py` shadows the submodule attribute. See the
    matching helper in `test_kosync_book_id_keyed_lookup.py`."""
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    return sys.modules["cps.progress_syncing.protocols.kosync"]


@pytest.mark.unit
class TestPushReadingStateToHardcoverSignature:
    """Pin the new (user, book, progress_percentage) signature on
    push_reading_state_to_hardcover. The kosync path has no Flask
    `current_user`, so the function must accept an explicit user."""

    def test_function_accepts_explicit_user_first_arg(self):
        from cps.kobo import push_reading_state_to_hardcover
        sig = inspect.signature(push_reading_state_to_hardcover)
        params = list(sig.parameters.keys())
        assert params[0] == "user", (
            "first parameter must be `user` (was previously `book` + relied "
            "on Flask current_user); kosync caller has no current_user"
        )
        assert "book" in sig.parameters
        assert "progress_percentage" in sig.parameters, (
            "function must accept progress_percentage directly, not extract "
            "from a request_bookmark dict — kosync's caller doesn't have "
            "the request_bookmark shape Kobo uses"
        )


@pytest.mark.unit
class TestUpdateBookReadStatusSignature:
    """Pin the (user, book_id, percentage) signature on update_book_read_status —
    the call site that follows must hand off the same user object to
    push_reading_state_to_hardcover."""

    def test_function_accepts_user_object_not_just_user_id(self):
        kosync_mod = _kosync_module()
        sig = inspect.signature(kosync_mod.update_book_read_status)
        params = list(sig.parameters.keys())
        assert params[0] == "user", (
            "must take user object so caller can pass the same user into "
            "push_reading_state_to_hardcover without re-fetching"
        )


@pytest.mark.unit
class TestKosyncUpdateProgressCallsHardcover:
    """Pin the integration: when kosync.update_progress successfully writes
    a progress record AND a Calibre book_id resolves, it must call
    push_reading_state_to_hardcover. The Hardcover-sync path is the whole
    point of the backport — silently dropping it is the regression."""

    def test_update_progress_source_invokes_hardcover_push(self):
        """Source-level pin so the call is preserved across edits."""
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent.parent
        src = (repo_root / "cps" / "progress_syncing" / "protocols" /
               "kosync.py").read_text()
        # find the update_progress function body up to next top-level def
        import re
        match = re.search(
            r"def update_progress\(\).*?(?=\n(?:def |@)\w)",
            src, re.DOTALL,
        )
        assert match is not None, "update_progress not found"
        body = match.group(0)
        assert "push_reading_state_to_hardcover" in body, (
            "kosync.update_progress must call push_reading_state_to_hardcover "
            "after a successful update — that's the whole point of fork PR #101"
        )

    def test_hardcover_push_imported_at_module_load(self):
        kosync_mod = _kosync_module()
        assert hasattr(kosync_mod, "push_reading_state_to_hardcover"), (
            "import of push_reading_state_to_hardcover must be at module top "
            "so the kosync update path can call it without a runtime import"
        )

    def test_hardcover_push_gated_on_book_id(self):
        """No book_id resolution → no Hardcover push. Without this gate,
        every KOReader sync of an un-ingested document would log a noisy
        Hardcover-sync attempt."""
        from pathlib import Path
        import re
        repo_root = Path(__file__).resolve().parent.parent.parent
        src = (repo_root / "cps" / "progress_syncing" / "protocols" /
               "kosync.py").read_text()
        match = re.search(
            r"def update_progress\(\).*?(?=\n(?:def |@)\w)",
            src, re.DOTALL,
        )
        body = match.group(0)
        # Find the index of the if-book_id gate and the hardcover push call.
        # The push must appear AFTER an `if book_id` check.
        gate_pos = body.find("if book_id:")
        push_pos = body.find("push_reading_state_to_hardcover")
        assert gate_pos != -1, "missing `if book_id:` gate"
        assert push_pos != -1, "missing push call"
        assert push_pos > gate_pos, (
            "push_reading_state_to_hardcover must be inside the `if book_id:` "
            "block — calling it without a resolved book_id is a noisy regression"
        )


@pytest.mark.unit
class TestPushReadingStateToHardcoverNoActiveContext:
    """Behavioral pin: when push_reading_state_to_hardcover is called from
    a context with no Flask `current_user` (i.e. the kosync path), it
    must still resolve the user from the explicit argument, not crash."""

    def test_works_without_flask_current_user(self):
        """Hardcover client gets called with the explicit user (no
        Flask current_user reference). Mocks model the real guards:
        global config flag + per-book blacklist DB lookup."""
        from cps.kobo import push_reading_state_to_hardcover
        import cps.kobo as kobo_mod

        user = MagicMock()
        user.name = "alice"
        user.hardcover_token = "fake-token"
        user.id = 7

        book = MagicMock()
        book.id = 42
        book.identifiers = []

        # Mock the global config flag, the blacklist query, and the
        # Hardcover client.
        mock_config = MagicMock()
        mock_config.hardcover_sync_enabled.return_value = True

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None

        mock_ub = MagicMock(session=mock_session)
        mock_hardcover = MagicMock()
        mock_hardcover.__bool__ = lambda self: True

        with patch.object(kobo_mod, "config", mock_config), \
                patch.object(kobo_mod, "ub", mock_ub), \
                patch.object(kobo_mod, "hardcover", mock_hardcover):
            push_reading_state_to_hardcover(user, book, 42)
            # The Hardcover calls run on a worker after the push returns (#2775).
            assert hardcover_progress.wait_idle(5)
            mock_hardcover.HardcoverClient.assert_called_once_with("fake-token")
            mock_hardcover.HardcoverClient.return_value.update_reading_progress.\
                assert_called_once_with({}, 42)


@pytest.mark.unit
class TestPushReadingStateToHardcoverGuardsAndFailures:
    """Pin the local Hardcover boundary used by both Kobo and KOReader.

    The client is fully mocked: configuration must prevent *any* external
    client construction, and a remote failure must not break the device's
    already-persisted progress update.
    """

    @staticmethod
    def _user_and_book():
        user = SimpleNamespace(name="alice", hardcover_token="fake-token", id=7)
        book = SimpleNamespace(
            id=42,
            identifiers=[SimpleNamespace(type="hardcover-id", val="123"),
                         SimpleNamespace(type="hardcover-edition", val="456")],
        )
        return user, book

    def test_disabled_global_gate_does_not_construct_a_client(self):
        from cps.kobo import push_reading_state_to_hardcover
        import cps.kobo as kobo_mod

        user, book = self._user_and_book()
        constructed = []
        service = SimpleNamespace(HardcoverClient=lambda token: constructed.append(token))
        with patch.object(kobo_mod, "config", SimpleNamespace(hardcover_sync_enabled=lambda: False)), \
                patch.object(kobo_mod, "hardcover", service):
            push_reading_state_to_hardcover(user, book, 47)

        assert constructed == []

    def test_mapping_is_forwarded_unchanged_and_remote_failure_is_swallowed(self):
        from cps.kobo import push_reading_state_to_hardcover
        import cps.kobo as kobo_mod

        user, book = self._user_and_book()
        client = MockHardcoverClient(progress_raises=RuntimeError("Hardcover unavailable"))
        service = SimpleNamespace(HardcoverClient=lambda token: client)
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None

        with patch.object(kobo_mod, "config", SimpleNamespace(hardcover_sync_enabled=lambda: True)), \
                patch.object(kobo_mod, "ub", SimpleNamespace(session=session, HardcoverBookBlacklist=MagicMock())), \
                patch.object(kobo_mod, "hardcover", service):
            # The method intentionally returns normally: Kobo/KOReader must not
            # retry the protocol request merely because Hardcover is unavailable.
            assert push_reading_state_to_hardcover(user, book, 47) is None
            assert hardcover_progress.wait_idle(5)

        # The worker gets plain values read on the request thread, never the
        # ORM relationship (#2775); the client accepts this mapping as is.
        assert client.calls == [("progress", {"hardcover-id": "123", "hardcover-edition": "456"}, 47)]
