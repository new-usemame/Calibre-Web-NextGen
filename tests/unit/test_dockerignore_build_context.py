# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Contract tests for the Docker build context (``.dockerignore``).

``Dockerfile`` copies the *entire* build context into the runtime image::

    COPY --chown=abc:abc . /app/calibre-web-automated/

so ``.dockerignore`` is the only thing standing between a developer's working
tree and the image. Two failure directions matter, and this module pins both.

**Direction 1 — local-only junk leaking in.** ``.venv``, ``.claude``,
``.coverage`` and ``calibre-web.log`` are all git-ignored, so a clean CI
checkout never has them and published images were never affected. A local
``docker build`` is a different story: measured with a ``FROM scratch`` +
``COPY . /`` probe, excluding just these four took the copied layer from
299,311,528 to 17,832,889 bytes — **281 MB**. This is the same class as the
``.cwa_migrations`` leak documented inline in ``.dockerignore`` (#1162), where a
developer's runtime state got baked into their image and silently changed how
the install behaved.

**Direction 2 — over-excluding something the image needs.** The far more
dangerous direction, and the reason this file exists rather than a bare
"these strings are present" check. ``.dockerignore`` already carries a bare
``app.db`` line. It is safe *only* because Docker patterns are root-anchored:
``app.db`` matches ``./app.db`` and not ``empty_library/app.db``. One well-meaning
edit to ``**/app.db`` would stop shipping the seed database that
``scripts/auto_library.py`` and ``root/etc/s6-overlay/s6-rc.d/cwa-init/run``
copy into ``/config`` on a fresh install — every new deployment would come up
without an ``app.db``, and CI would stay green the whole time. So the runtime
allowlist below is asserted as *not excluded*, path by path.

The matcher models Docker's root-anchored, non-recursive semantics. Because a
matcher that silently fails to understand a pattern would turn direction 2 into
a false negative, ``test_no_pattern_forms_beyond_the_matcher`` fails loudly the
moment ``.dockerignore`` grows a form this file does not model (``**``,
negation), rather than quietly passing.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
DOCKERFILE = REPO_ROOT / "Dockerfile"

# Git-ignored developer artifacts that must never reach the build context.
LOCAL_ONLY_ARTIFACTS = [
    ".venv",
    ".venv/lib/python3.12/site-packages/flask/__init__.py",
    ".claude",
    ".claude/settings.local.json",
    ".coverage",
    "calibre-web.log",
]

# Paths the built image genuinely needs. Excluding any of these ships a broken
# image while leaving every unit test green.
REQUIRED_IN_IMAGE = [
    "cps/__init__.py",
    "cps/templates/index.html",
    "cps/translations/ru/LC_MESSAGES/messages.po",
    # Compiled during the image build, not committed. Listed anyway: a root-level
    # `messages.mo` line widened to `**/messages.mo` would drop every compiled
    # locale and users would silently get the English fallback (the v4.0.47 shape).
    "cps/translations/ru/LC_MESSAGES/messages.mo",
    "scripts/setup-cwa.sh",
    "scripts/auto_library.py",
    "root/etc/s6-overlay/s6-rc.d/cwa-init/run",
    "koreader/plugins/cwasync.koplugin/main.lua",
    "empty_library/app.db",
    "empty_library/metadata.db",
    "requirements.txt",
    "optional-requirements.txt",
]


def _patterns() -> list[str]:
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def _matches(pattern: str, path: str) -> bool:
    """Docker semantics: root-anchored, matches the path or any parent of it.

    A pattern of N segments is compared against the first N segments of the
    candidate path, so ``docs`` excludes ``docs/api.md`` (parent match) while
    ``app.db`` does *not* touch ``empty_library/app.db`` (not root-anchored
    there). Matching is case-sensitive, as it is in BuildKit on Linux.
    """
    pattern_parts = pattern.strip("/").split("/")
    path_parts = path.strip("/").split("/")
    if len(pattern_parts) > len(path_parts):
        return False
    return all(
        fnmatch.fnmatchcase(actual, expected)
        for expected, actual in zip(pattern_parts, path_parts)
    )


def is_excluded(path: str) -> bool:
    return any(_matches(pattern, path) for pattern in _patterns())


def test_dockerfile_still_copies_the_whole_context():
    """The premise of this module. If the COPY narrows, revisit these tests."""
    assert "COPY --chown=abc:abc . /app/calibre-web-automated/" in DOCKERFILE.read_text(
        encoding="utf-8"
    ), "Dockerfile no longer copies the whole build context; .dockerignore contract changed"


def test_no_pattern_forms_beyond_the_matcher():
    """Guard against silent false negatives in the two tests below.

    ``_matches`` models plain names, directories and single-segment globs. It
    does not model ``**`` or ``!`` negation. If either appears, the allowlist
    test could start passing for the wrong reason, so fail here instead.
    """
    unsupported = [
        pattern
        for pattern in _patterns()
        if pattern.startswith("!") or "**" in pattern
    ]
    assert not unsupported, (
        f".dockerignore uses pattern forms this test cannot model: {unsupported}. "
        "Update _matches() to match Docker's semantics before relying on these tests."
    )


@pytest.mark.parametrize("path", LOCAL_ONLY_ARTIFACTS)
def test_local_only_artifacts_stay_out_of_the_build_context(path):
    """A local `docker build` must not bake a developer's tree into the image."""
    assert is_excluded(path), (
        f"{path!r} is git-ignored developer state but reaches the Docker build "
        "context, so a local build copies it into /app/calibre-web-automated/. "
        "Add it to .dockerignore."
    )


@pytest.mark.parametrize("path", REQUIRED_IN_IMAGE)
def test_runtime_required_paths_are_never_excluded(path):
    """Over-excluding ships a broken image that every other test calls green."""
    assert not is_excluded(path), (
        f"{path!r} is required inside the image but .dockerignore excludes it. "
        "Check which pattern matches — a bare name is root-anchored, so widening "
        "one to '**/name' can silently drop a nested file the runtime needs."
    )
