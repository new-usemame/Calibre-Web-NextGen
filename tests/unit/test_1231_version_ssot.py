# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork #1231 — one source of truth for the installed
version, and one path for the Calibre version file.

Credit: @chloeroform (#1231) for the consolidation.

Three call sites each opened ``/app/CWA_RELEASE`` with their own copy of a
try/except: ``about.collect_stats``, ``admin.cwa_get_package_versions``, and
``constants.INSTALLED_VERSION``. Only the last one strips whitespace, falls
back on a zero-byte file, and honours the ``CWA_INSTALLED_VERSION`` env var
that cwa-init exports — so the admin table could disagree with ``USER_AGENT``
on the same running image. The two hand-rolled readers now defer to
``constants.INSTALLED_VERSION``.

That consolidation changes the value seen when the build never stamped a
version: the hand-rolled readers said ``"Unknown"``, the constant says
``v0.0.0``. ``"Unknown"`` does not parse as a release tag, so
``release_url_for_version`` returned ``None`` and the template rendered plain
text; ``v0.0.0`` *does* parse, so without the sentinel case below the admin
version table would link to ``/releases/tag/v0.0.0`` — a tag that was never
published. That is the regression these tests pin.

Separately, ``CALIBRE_RELEASE`` moved from ``/`` to ``/app`` to sit alongside
``CWA_RELEASE`` and ``KEPUBIFY_RELEASE`` — and was then **retired outright** in
#1274, because a build-time stamp cannot describe a binary that was replaced
afterwards. The cross-file tests at the bottom moved with it: they used to pin
that the writer and all three readers agreed on one path, and now pin that
nothing writes or reads the stamp at all, that the build ARG which selects the
download survives, and that both UIs share one runtime source.
"""

import importlib.util
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

def _load(module_name: str, relative_path: str):
    """Load a leaf ``cps`` module without paying for ``cps/__init__.py``.

    Same hermetic snapshot/restore contract as test_533's ``_load_updater``.
    """
    touched = ["cps", module_name, "cps.logger", "cps.constants", "cps.file_helper"]
    saved = {k: sys.modules.get(k) for k in touched}
    try:
        if "cps" not in sys.modules:
            cps_pkg = types.ModuleType("cps")
            cps_pkg.__path__ = [str(_REPO_ROOT / "cps")]
            sys.modules["cps"] = cps_pkg

        if "cps.logger" not in sys.modules:
            logger_mod = types.ModuleType("cps.logger")

            class _DummyLog:
                def __getattr__(self, _name):
                    return lambda *a, **k: None

            logger_mod.create = lambda *a, **k: _DummyLog()
            sys.modules["cps.logger"] = logger_mod

        if "cps.constants" not in sys.modules:
            c_spec = importlib.util.spec_from_file_location(
                "cps.constants", _REPO_ROOT / "cps" / "constants.py"
            )
            c_mod = importlib.util.module_from_spec(c_spec)
            sys.modules["cps.constants"] = c_mod
            c_spec.loader.exec_module(c_mod)

        if "cps.file_helper" not in sys.modules:
            fh = types.ModuleType("cps.file_helper")
            fh.get_temp_dir = lambda *a, **k: "/tmp"
            sys.modules["cps.file_helper"] = fh

        spec = importlib.util.spec_from_file_location(
            module_name, _REPO_ROOT / relative_path
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


_updater = _load("cps.updater", "cps/updater.py")
_constants = _load("cps.constants", "cps/constants.py")
release_url_for_version = _updater.release_url_for_version
_BASE = "https://github.com/" + _updater._REPOSITORY_SLUG


# --- behavioural: the unknown-version sentinel must not become a dead link ---

def test_unknown_sentinel_returns_no_link():
    """The whole point of #1231's regression: v0.0.0 parses as a tag."""
    # The suite runs unstamped (no /app/CWA_RELEASE, no env override), which
    # is exactly the state this guards.
    assert _constants.VERSION_IS_STAMPED is False
    assert release_url_for_version(_constants.UNKNOWN_VERSION) is None


def test_a_genuinely_published_v0_0_0_still_links(monkeypatch):
    """The sentinel must not swallow a downstream fork's real v0.0.0.

    ``CWA_RELEASE_REPO`` repoints the slug, so a fork can legitimately publish
    a ``v0.0.0`` tag. Suppressing on the string alone would hide a link that
    resolves — the suppression is about *not having been stamped*, not about
    the digits.
    """
    monkeypatch.setattr(_updater.constants, "VERSION_IS_STAMPED", True)
    assert release_url_for_version("v0.0.0") == _BASE + "/releases/tag/v0.0.0"


def test_stamped_flag_is_false_exactly_when_nothing_was_stamped(monkeypatch):
    """VERSION_IS_STAMPED must track the source, not the resulting string."""
    monkeypatch.setenv("CWA_INSTALLED_VERSION", "v9.9.9")
    reloaded = _load("cps.constants", "cps/constants.py")
    assert reloaded.VERSION_IS_STAMPED is True
    assert reloaded.INSTALLED_VERSION == "v9.9.9"

    # An explicit env value that happens to equal the sentinel is still stamped.
    monkeypatch.setenv("CWA_INSTALLED_VERSION", "v0.0.0")
    reloaded = _load("cps.constants", "cps/constants.py")
    assert reloaded.VERSION_IS_STAMPED is True
    assert reloaded.INSTALLED_VERSION == "v0.0.0"

    monkeypatch.delenv("CWA_INSTALLED_VERSION", raising=False)
    reloaded = _load("cps.constants", "cps/constants.py")
    assert reloaded.VERSION_IS_STAMPED is False
    assert reloaded.INSTALLED_VERSION == reloaded.UNKNOWN_VERSION


def test_unknown_sentinel_is_the_literal_we_think_it_is():
    # If the sentinel ever changes, the test above would keep passing while
    # silently pinning nothing. Anchor the literal too.
    assert _constants.UNKNOWN_VERSION == "v0.0.0"


def test_unknown_sentinel_with_whitespace_returns_no_link():
    # INSTALLED_VERSION is stripped, but a caller may hand us a raw file read.
    assert release_url_for_version("  v0.0.0\n") is None


def test_real_releases_still_link_through():
    # The sentinel case must not swallow genuine versions.
    assert release_url_for_version("v4.1.24") == _BASE + "/releases/tag/v4.1.24"
    assert release_url_for_version("v0.0.1") == _BASE + "/releases/tag/v0.0.1"
    assert release_url_for_version("v1.0.0") == _BASE + "/releases/tag/v1.0.0"


def test_unknown_string_still_returns_none():
    # kepubify/calibre readers still produce "Unknown"; keep that contract.
    assert release_url_for_version("Unknown") is None


# --- source pins: the readers actually defer to the constant -----------------

def test_about_reports_the_installed_version_constant():
    src = (_REPO_ROOT / "cps" / "about.py").read_text(encoding="utf-8")
    assert "constants.INSTALLED_VERSION" in src
    assert "/app/CWA_RELEASE" not in src, (
        "about.py must not re-open the release file; that is what "
        "constants.INSTALLED_VERSION is for"
    )


def test_admin_reports_the_installed_version_constant():
    src = (_REPO_ROOT / "cps" / "admin.py").read_text(encoding="utf-8")
    assert "constants.INSTALLED_VERSION" in src
    assert "/app/CWA_RELEASE" not in src, (
        "admin.py must not re-open the release file; that is what "
        "constants.INSTALLED_VERSION is for"
    )


def test_only_constants_reads_the_release_file():
    """Exactly one module in cps/ may open /app/CWA_RELEASE."""
    readers = [
        path.relative_to(_REPO_ROOT).as_posix()
        for path in (_REPO_ROOT / "cps").rglob("*.py")
        if "/app/CWA_RELEASE" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert readers == ["cps/constants.py"], readers


# --- cross-file: the CALIBRE_RELEASE stamp file is gone entirely -------------
#
# #1274 (@chloeroform) retired the file. The old invariant was "the writer and
# all three readers agree on one path"; the new one is "nobody writes or reads
# it at all, and the version comes from the binary that is actually installed".
# The pair below replaces that pin — dropping it outright would let the file
# creep back in one file at a time.


def test_no_calibre_release_stamp_file_is_written_or_read_anywhere():
    """No build step writes the stamp file and no code reads it.

    Sweeps **every tracked runtime and build file** rather than a hand-listed
    few: the way this regresses is a consumer appearing somewhere nobody
    thought to list — a helper under ``scripts/``, a workflow, an entrypoint,
    a packaging file.

    Two categories are deliberately exempt because naming the retired path is
    their job: ``tests/`` (this module has to name it to assert on it) and
    Markdown (``CHANGELOG.md`` and ``CHANGES-vs-upstream.md`` are the historical
    record of the removal). Neither is executed, so neither can resurrect the
    dependency.

    Matches the *path* form only. ``ARG CALIBRE_RELEASE`` and ``$CALIBRE_RELEASE``
    in the Dockerfile are the build arg that pins which calibre gets downloaded —
    that is still the SSOT for the build and must survive.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_REPO_ROOT, capture_output=True, check=True,
    ).stdout.decode("utf-8", errors="ignore").split("\0")

    offenders = []
    for relative_path in tracked:
        if not relative_path:
            continue
        if relative_path.startswith("tests/") or relative_path.endswith(".md"):
            continue
        path = _REPO_ROOT / relative_path
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue  # binary asset or a symlink to nowhere
        if re.search(r"/CALIBRE_RELEASE\b", text):
            offenders.append(relative_path)

    assert offenders == [], (
        "the /app/CALIBRE_RELEASE stamp file was retired in #1274; these files "
        f"reference it again: {offenders}"
    )


def test_the_stamp_file_sweep_actually_searches_the_repository():
    """A sweep that silently matched nothing would pass forever.

    ``git ls-files`` returning an empty list (wrong cwd, no git) would make the
    test above vacuous, so pin that it really is walking the tree.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=_REPO_ROOT, capture_output=True, check=True,
    ).stdout.decode("utf-8", errors="ignore").split("\0")
    tracked = [p for p in tracked if p]

    assert len(tracked) > 500, len(tracked)
    for expected in ("Dockerfile", "cps/admin.py", "cps/converter.py"):
        assert expected in tracked, expected


def test_dockerfile_still_pins_the_calibre_build_arg():
    """Retiring the stamp file must not disturb the build arg it came from."""
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^ARG CALIBRE_RELEASE=\d", dockerfile, re.MULTILINE), (
        "ARG CALIBRE_RELEASE is the SSOT for which calibre the image downloads"
    )


def test_classic_admin_and_spa_share_one_calibre_version_source():
    """Both UIs must read the running binary, not a build-time stamp.

    That is the point of #1274: the stamp recorded what the *build* pinned, so
    an image whose calibre was replaced (or a custom converter path) reported a
    version it was not running.
    """
    admin_src = (_REPO_ROOT / "cps" / "admin.py").read_text(encoding="utf-8")
    about_src = (_REPO_ROOT / "cps" / "about.py").read_text(encoding="utf-8")
    for name, src in (("cps/admin.py", admin_src), ("cps/about.py", about_src)):
        assert "converter.get_calibre_version()" in src, (
            f"{name} must source the calibre version from the shared helper"
        )


# --- packaging: the SSOT the pyproject dynamic version points at -------------

def test_pyproject_still_resolves_version_from_the_constant():
    """#1231 proposed reading the file directly from pyproject instead.

    ``file =`` takes a path relative to the project root and rejects one that
    escapes it, so an absolute ``/app/CWA_RELEASE`` cannot build from a source
    checkout at all. The attr form keeps constants.py the one definition.
    """
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'attr = "calibreweb.cps.constants.INSTALLED_VERSION"' in pyproject
    assert "/app/CWA_RELEASE" not in pyproject
