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
``CWA_RELEASE`` and ``KEPUBIFY_RELEASE``. It is written by the Dockerfile and
read back from three places; the cross-file test pins all four so a future
move can't update the writer and leave a reader behind.
"""

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

_CALIBRE_RELEASE_PATH = "/app/CALIBRE_RELEASE"


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


# --- cross-file: every CALIBRE_RELEASE reference agrees on one path ----------

def test_dockerfile_writes_calibre_release_under_app():
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f'echo "$CALIBRE_RELEASE" > {_CALIBRE_RELEASE_PATH}' in dockerfile


def test_every_calibre_release_consumer_uses_the_same_path():
    """The writer and all three readers must agree.

    This is the failure this move invites: update the Dockerfile, forget the
    s6 script, and the container logs 'unknown' forever without erroring.
    """
    consumers = {
        "cps/admin.py": r'open\("(/[^"]*CALIBRE_RELEASE)"',
        "root/etc/s6-overlay/s6-rc.d/calibre-binaries-setup/run": r"cat (/\S*CALIBRE_RELEASE)",
        "root/etc/s6-overlay/s6-rc.d/cwa-init/run": r'"(/\S*CALIBRE_RELEASE)"',
    }
    for relative_path, pattern in consumers.items():
        text = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        found = re.findall(pattern, text)
        assert found, f"{relative_path}: no CALIBRE_RELEASE reference matched"
        assert set(found) == {_CALIBRE_RELEASE_PATH}, f"{relative_path}: {found}"


def test_no_root_level_calibre_release_references_remain():
    """Nothing may still point at the old ``/CALIBRE_RELEASE`` location."""
    stale = []
    for relative_path in (
        "Dockerfile",
        "cps/admin.py",
        "root/etc/s6-overlay/s6-rc.d/calibre-binaries-setup/run",
        "root/etc/s6-overlay/s6-rc.d/cwa-init/run",
    ):
        text = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        # A bare /CALIBRE_RELEASE not preceded by "app"
        if re.search(r"(?<!app)/CALIBRE_RELEASE", text):
            stale.append(relative_path)
    assert stale == [], stale


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
