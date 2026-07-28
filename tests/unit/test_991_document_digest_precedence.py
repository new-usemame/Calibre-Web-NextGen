# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for #991 — "Book status stopped synching / not recognised".

The server resolves a KOReader sync by matching the digest the device sends
against `book_format_checksums`, which only ever holds digests of bytes the
server actually served (`cps/progress_syncing/checksums/koreader.py`).

The plugin used to answer that question from KOReader's per-document sidebar
cache (`partial_md5_checksum`) and only hash the file when the cache was empty.
The sidecar is keyed by file PATH, so it survives the file at that path being
replaced. Once the server's copy of a book changed and the reader re-downloaded
over the old file, the device kept reporting the digest of bytes it no longer
held — and every re-download registered one more digest the device would never
compute. The reporter re-downloaded over OPDS repeatedly and kept getting
`No book found for checksum`, which is exactly this shape.

The fix inverts the precedence: the bytes on disk are the authority and the
cache is a fallback for when hashing is impossible. The policy itself is pure
and lives in `sync_logic.lua`, behaviourally tested by
`koreader/plugins/cwasync.koplugin/tests/sync_logic_test.lua`. We don't run a
Lua runner in CI, so these tests pin the wiring: that `getDocumentDigest`
delegates to that policy, and that it passes the two sources in the order that
makes the computed digest win. Swapping the two arguments restores the bug and
trips `test_computed_digest_is_passed_as_the_winning_source`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# CI selects with -m "smoke or unit"; without this the whole file is deselected.
pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "koreader" / "plugins" / "cwasync.koplugin"
MAIN_LUA = PLUGIN_DIR / "main.lua"
SYNC_LOGIC_LUA = PLUGIN_DIR / "sync_logic.lua"
SYNC_LOGIC_TEST_LUA = PLUGIN_DIR / "tests" / "sync_logic_test.lua"

CACHED_SETTING = "partial_md5_checksum"


def _read(path: Path) -> str:
    assert path.exists(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


def _lua_function_body(source: str, header: str) -> str:
    """Slice one top-level Lua function out of `source`.

    Every nested block in this file is indented, so the first column-zero `end`
    after the header terminates the function.
    """
    start = source.find(header)
    assert start != -1, f"missing function: {header}"
    end = source.find("\nend\n", start)
    assert end != -1, f"unterminated function: {header}"
    return source[start : end + len("\nend\n")]


@pytest.fixture(scope="module")
def digest_fn() -> str:
    return _lua_function_body(
        _read(MAIN_LUA), "function CWASync:getDocumentDigest(file_path)"
    )


def test_document_digest_delegates_to_the_shared_policy(digest_fn: str):
    assert "SyncLogic.resolveDocumentDigest(" in digest_fn, (
        "getDocumentDigest must delegate precedence to "
        "SyncLogic.resolveDocumentDigest so the policy stays behaviourally "
        "tested in sync_logic_test.lua"
    )


def test_computed_digest_is_passed_as_the_winning_source(digest_fn: str):
    """The load-bearing assertion: hashing the file is the FIRST argument.

    `resolveDocumentDigest` takes the authoritative source first and the
    fallback second. Passing the cache first is precisely the #991 regression.
    """
    match = re.search(
        r"SyncLogic\.resolveDocumentDigest\(\s*([A-Za-z_][\w]*)\s*,\s*([A-Za-z_][\w]*)\s*\)",
        digest_fn,
    )
    assert match, "expected resolveDocumentDigest to be called with two named sources"
    first, second = match.group(1), match.group(2)
    assert first == "computeFromFile", (
        f"the file-hashing source must be passed first, got {first!r} — the "
        "cached sidecar digest is stale whenever the book file was replaced"
    )
    assert second == "readCachedDigest", (
        f"the cached sidecar source must be the fallback, got {second!r}"
    )


def test_cached_sidecar_is_only_read_inside_the_fallback(digest_fn: str):
    """No path may return the cached digest without first trying to hash."""
    fallback = _lua_function_body(
        digest_fn.replace("\n    end\n", "\nend\n"),
        "local function readCachedDigest()",
    )
    assert digest_fn.count(CACHED_SETTING) == fallback.count(CACHED_SETTING), (
        f"every {CACHED_SETTING!r} read in getDocumentDigest must live inside "
        "readCachedDigest; a read outside it can short-circuit the recompute"
    )
    assert fallback.count(CACHED_SETTING) == 2, (
        "readCachedDigest should read the sidecar for both the open document "
        "and an explicitly passed path"
    )


def test_file_hashing_lives_in_the_computed_source(digest_fn: str):
    computed = _lua_function_body(
        digest_fn.replace("\n    end\n", "\nend\n"),
        "local function computeFromFile()",
    )
    assert "util.partialMD5" in computed, (
        "computeFromFile must hash the file with KOReader's partialMD5, which "
        "is the algorithm the server registers digests with"
    )
    assert CACHED_SETTING not in computed, (
        "computeFromFile must not consult the cache — it is the authority"
    )


def test_policy_prefers_computed_over_cached():
    policy = _lua_function_body(
        _read(SYNC_LOGIC_LUA),
        "function SyncLogic.resolveDocumentDigest(computeFromFile, readCachedDigest)",
    )
    assert re.search(
        r"return\s+call\(computeFromFile\)\s+or\s+call\(readCachedDigest\)", policy
    ), (
        "resolveDocumentDigest must try the computed digest first and fall back "
        "to the cached one"
    )
    assert "pcall(source)" in policy, (
        "each source must be called defensively — an unreadable file or a "
        "missing sidecar must not take the sync down"
    )


def test_policy_has_behavioural_lua_coverage():
    """The Lua test is where the behaviour is actually proven; keep it wired."""
    body = _read(SYNC_LOGIC_TEST_LUA)
    assert "testResolveDocumentDigest" in body, (
        "sync_logic_test.lua must keep behavioural coverage for the digest policy"
    )
    assert re.search(r"^testResolveDocumentDigest\(\)", body, re.MULTILINE), (
        "the digest policy test must actually be invoked by the Lua runner, not "
        "just defined"
    )
    assert 'returns("fresh"), returns("stale")' in body, (
        "the stale-cache case (#991) must stay covered"
    )
