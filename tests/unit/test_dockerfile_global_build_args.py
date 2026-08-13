# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pin the version-defining build ARGs to GLOBAL scope (before the first FROM).

A Dockerfile `ARG FOO` re-declared inside a build stage only inherits a
default value when FOO is a *global* arg — one declared before the first
`FROM`. If the defaulted declaration sits inside an earlier stage, the
re-declaration in a later stage resolves to the empty string.

That is exactly what broke every :dev image build on 2026-06-28: commit
c1cd7b462 prepended a `FROM node:22-slim AS frontend-build` stage above the
ARG block, which had been global until then. CALIBRE_RELEASE / KEPUBIFY_RELEASE
/ PYTHON_VERSION / PYTHON_BUILD_STANDALONE_RELEASE became scoped to
frontend-build, so the `dependencies` stage re-declared them empty. The
download steps then built malformed URLs such as

    https://github.com/pgaskin/kepubify/releases/download//kepubify-linux-64bit

(note the `download//kepubify` — empty release), which 404s. Bumping the pin
(#544), adding retries (#545/#550) and authenticating the download (#546) all
chased the wrong layer because the URL itself was malformed. #549 hoisted the
PYTHON_* pins (and moved Python to a GHCR mirror) but left CALIBRE_RELEASE /
KEPUBIFY_RELEASE trapped in the frontend-build stage, so the build advanced
past Python and died at the kepubify download instead.

The fix moved both binaries to GHCR mirror images (Python in #549, kepubify in
#552), and #552 also hoisted CALIBRE_RELEASE / KEPUBIFY_RELEASE to the global
ARG block. kepubify is now `COPY`-d from a `kepubify-${KEPUBIFY_RELEASE}` mirror
stage rather than downloaded, so the guard below checks that the mirror FROM
interpolates the pin; calibre is still fetched from its own CDN by URL.

These tests fail on the broken layout (defaults trapped after the first FROM)
and pass once the defaults are hoisted above every FROM. They also guard
against a future stage being prepended above the ARG block again.
"""

import re
from pathlib import Path

import pytest


# These are pure file-parsing assertions (no Docker, no network): mark them
# `unit` so CI's `pytest -m "smoke or unit"` selector actually collects them.
# Without a marker, --strict-markers + that selector silently deselect the
# whole module, leaving the regression guard dead — which is how a stale
# assertion sat green in CI while failing on a direct run.
pytestmark = pytest.mark.unit


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"

# ARGs whose value feeds a download URL (or a FROM mirror tag) in a later
# stage. Each MUST carry its default in global scope so every stage that
# re-declares it inherits a value.
VERSION_ARGS = (
    "CALIBRE_RELEASE",
    "KEPUBIFY_RELEASE",
    "PYTHON_BUILD_STANDALONE_RELEASE",
    "PYTHON_VERSION",
)


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    return DOCKERFILE.read_text()


@pytest.fixture(scope="module")
def first_from_offset(dockerfile_text: str) -> int:
    """Character offset of the first `FROM` instruction."""
    match = re.search(r"^FROM\b", dockerfile_text, re.MULTILINE)
    assert match, "Dockerfile must contain at least one FROM"
    return match.start()


@pytest.mark.parametrize("arg", VERSION_ARGS)
def test_version_arg_default_is_global(arg: str, dockerfile_text: str, first_from_offset: int) -> None:
    """The defaulted declaration (`ARG NAME=value`) must appear before the
    first FROM, i.e. in global scope, so later-stage re-declarations inherit it."""
    match = re.search(rf"^ARG {re.escape(arg)}=\S", dockerfile_text, re.MULTILINE)
    assert match, f"Dockerfile must declare a default for ARG {arg} (e.g. `ARG {arg}=...`)"
    assert match.start() < first_from_offset, (
        f"ARG {arg}=... carries its default AFTER the first FROM, so it is scoped "
        f"to that stage and the `dependencies` stage re-declares it empty — which "
        f"produces malformed download URLs (404). Move the defaulted declaration "
        f"above the first FROM (global scope)."
    )


def test_no_defaulted_version_arg_trapped_inside_a_stage(dockerfile_text: str, first_from_offset: int) -> None:
    """Defense in depth: NO defaulted version-arg declaration may live after the
    first FROM. A duplicate `ARG NAME=value` inside a stage re-introduces the
    two-places-to-bump trap that hid the original regression (#544 bumped the
    trapped copy and had no effect)."""
    post_from = dockerfile_text[first_from_offset:]
    for arg in VERSION_ARGS:
        assert not re.search(rf"^ARG {re.escape(arg)}=\S", post_from, re.MULTILINE), (
            f"A defaulted `ARG {arg}=...` appears after the first FROM. Keep the "
            f"single source of truth in the global block; stages should re-declare "
            f"with a bare `ARG {arg}` (no value) so they inherit the global default."
        )


def test_binary_source_lines_interpolate_their_release_args(dockerfile_text: str) -> None:
    """The binary source lines must reference their release vars, and the global
    defaults must be non-empty — together these guarantee the rendered ref
    carries a real version segment and never the empty-var form that 404s.

    Since #552 kepubify comes from a GHCR mirror stage tagged
    `kepubify-${KEPUBIFY_RELEASE}` (not a GitHub-release download), so the guard
    checks the mirror FROM. calibre is still fetched from its own CDN by URL."""
    # kepubify: FROM ghcr.io/.../...:kepubify-${KEPUBIFY_RELEASE} AS kepubify_mirror
    assert re.search(
        r"^FROM\s+ghcr\.io/[^\s:]+:kepubify-\$\{KEPUBIFY_RELEASE\}",
        dockerfile_text,
        re.MULTILINE,
    ), "kepubify mirror FROM must interpolate KEPUBIFY_RELEASE"
    # calibre: https://download.calibre-ebook.com/linux-installer.sh...
    assert re.search(
        r"https://download\.calibre-ebook\.com/linux-installer\.sh | sh /dev/stdin version=\$\{CALIBRE_RELEASE\}",
        dockerfile_text,
    ), "calibre download URL must interpolate CALIBRE_RELEASE"
    for arg in ("CALIBRE_RELEASE", "KEPUBIFY_RELEASE"):
        match = re.search(rf"^ARG {re.escape(arg)}=(\S+)", dockerfile_text, re.MULTILINE)
        assert match and match.group(1), f"Global default for {arg} must be non-empty"


def test_python_mirror_from_uses_global_pins(dockerfile_text: str) -> None:
    """Python is sourced from our GHCR mirror image whose tag embeds both
    PYTHON_VERSION and PYTHON_BUILD_STANDALONE_RELEASE (introduced in #549). A
    FROM line can only interpolate a *global* ARG, so this doubles as a guard
    that those two pins stay global."""
    assert re.search(
        r"^FROM\s+ghcr\.io/[^\s:]+:cpython-\$\{PYTHON_VERSION\}-"
        r"\$\{PYTHON_BUILD_STANDALONE_RELEASE\}",
        dockerfile_text,
        re.MULTILINE,
    ), "Python mirror FROM must interpolate PYTHON_VERSION and PYTHON_BUILD_STANDALONE_RELEASE"
    for arg in ("PYTHON_VERSION", "PYTHON_BUILD_STANDALONE_RELEASE"):
        match = re.search(rf"^ARG {re.escape(arg)}=(\S+)", dockerfile_text, re.MULTILINE)
        assert match and match.group(1), f"Global default for {arg} must be non-empty"
