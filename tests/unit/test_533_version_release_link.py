# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork issue #533 — the Calibre-Web NextGen version
string in the admin version table links to the release notes.

@chloeroform asked for the version number at the bottom of ``/admin/view`` to
link somewhere a user can read what changed. ``cps.updater.release_url_for_version``
turns the installed-version string (read verbatim from ``/app/CWA_RELEASE``)
into a GitHub release URL, and ``admin.html`` wraps the version in an anchor
when that URL is present.

The behavioural tests pin the URL builder. The source-pin tests pin the
template + view wiring so a future template edit can't silently drop the link
while the unit test stays green.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_updater():
    """Load ``cps/updater.py`` in isolation, bypassing ``cps/__init__.py``.

    Mirrors the stub-installation pattern in conftest.py / test_calibre_init.py
    so we don't pay the Flask / SQLAlchemy / scheduler startup cost just to
    exercise a pure URL helper.

    Crucially this is *hermetic*: we snapshot every ``sys.modules`` key we
    touch and restore it in a ``finally`` block. ``updater.py`` uses relative
    imports (``from . import constants, logger``) so it has to load under the
    ``cps.updater`` name with a ``cps`` parent in ``sys.modules`` — but if we
    left that stub-loaded module behind, a later suite importing the real
    ``cps.updater`` would pick up our bypassed copy. We hold a reference to the
    returned module, then put ``sys.modules`` back exactly as we found it.
    """
    updater_path = _REPO_ROOT / "cps" / "updater.py"
    touched = ["cps", "cps.updater", "cps.logger", "cps.constants", "cps.file_helper"]
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
            # Load the *real* constants module rather than an empty stub.
            # updater.py reads constants.UNKNOWN_VERSION, and an empty stub
            # only survived by import-order luck — conftest happened to have
            # loaded the real module first, so this file passed standalone
            # while the same shim in a pristine interpreter raised
            # AttributeError. constants.py is a leaf (os/sys/collections +
            # flask_babel) whose only I/O is a guarded read of
            # /app/CWA_RELEASE, so loading it for real stays cheap and the
            # stub can never drift from the contract it stands in for.
            constants_spec = importlib.util.spec_from_file_location(
                "cps.constants", _REPO_ROOT / "cps" / "constants.py"
            )
            constants_mod = importlib.util.module_from_spec(constants_spec)
            sys.modules["cps.constants"] = constants_mod
            constants_spec.loader.exec_module(constants_mod)

        if "cps.file_helper" not in sys.modules:
            file_helper_mod = types.ModuleType("cps.file_helper")
            file_helper_mod.get_temp_dir = lambda *a, **k: "/tmp"
            sys.modules["cps.file_helper"] = file_helper_mod

        spec = importlib.util.spec_from_file_location("cps.updater", updater_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["cps.updater"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


_updater = _load_updater()
release_url_for_version = _updater.release_url_for_version
_SLUG = _updater._REPOSITORY_SLUG
_BASE = "https://github.com/" + _SLUG


# --- behavioural: the URL builder --------------------------------------------

def test_clean_fork_tag_points_at_exact_release():
    # The release image writes the GitHub tag verbatim (Dockerfile VERSION=<tag>).
    assert release_url_for_version("v4.0.172") == _BASE + "/releases/tag/v4.0.172"


def test_trailing_newline_is_stripped():
    # The CWNG version now arrives pre-stripped via constants.INSTALLED_VERSION
    # (#1231), but the kepubify/calibre readers beside it still do a raw
    # f.read(), and callers may hand us a file read directly — so the builder
    # keeps stripping rather than trusting its input.
    assert release_url_for_version("v4.0.172\n") == _BASE + "/releases/tag/v4.0.172"


def test_upstream_format_tag_without_v_prefix():
    assert release_url_for_version("4.0.172") == _BASE + "/releases/tag/4.0.172"


def test_dev_build_marker_falls_back_to_releases_listing():
    # Dev/canary images set VERSION=DEV_BUILD-dev-N-<sha>; that isn't a real
    # release tag, so the /releases/tag/<x> page would 404. Fall back to the
    # always-resolving releases listing instead.
    assert release_url_for_version("DEV_BUILD-dev-12-abc1234") == _BASE + "/releases"


def test_non_semver_marker_falls_back_to_releases_listing():
    assert release_url_for_version("nightly") == _BASE + "/releases"


def test_unknown_returns_none():
    # The kepubify/calibre readers still return "Unknown" when their file is
    # missing. Since #1231 the CWNG version instead falls back to
    # constants.UNKNOWN_VERSION; that sentinel is pinned in
    # test_1231_version_ssot.py because it *does* parse as a release tag.
    assert release_url_for_version("Unknown") is None
    assert release_url_for_version("unknown") is None


def test_empty_and_none_return_none():
    assert release_url_for_version("") is None
    assert release_url_for_version("   ") is None
    assert release_url_for_version(None) is None


def test_url_follows_repository_slug_override():
    # The link must share the slug with _REPOSITORY_API_URL so a CWA_RELEASE_REPO
    # override (downstream fork/pin) retargets both the updater and this link.
    assert release_url_for_version("v4.0.172").startswith(
        "https://github.com/" + _updater._REPOSITORY_SLUG + "/"
    )


# --- source-pin: the admin template + view wiring ----------------------------

def test_admin_template_wraps_version_in_release_link():
    html = (_REPO_ROOT / "cps" / "templates" / "admin.html").read_text(encoding="utf-8")
    # The version cell renders an <a href="{{cwa_release_url}}"> ... gated on the
    # url being present, opened safely in a new tab.
    assert "cwa_release_url" in html
    assert 'href="{{cwa_release_url}}"' in html
    assert 'rel="noopener noreferrer"' in html
    assert "{{cwa_version}}" in html  # plain-text fallback still present


def test_admin_view_passes_release_url_to_template():
    src = (_REPO_ROOT / "cps" / "admin.py").read_text(encoding="utf-8")
    assert "release_url_for_version" in src
    assert "cwa_release_url=cwa_release_url" in src
