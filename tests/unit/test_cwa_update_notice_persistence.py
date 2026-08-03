# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for where the "update available" banner keeps its throttle.

Background (community PR #1333, @chloeroform): the banner is meant to fire at
most once per calendar day. It remembers the last-shown date in a file. That
file used to live at ``/app/cwa_update_notice``.

``/app`` is not a volume — the Dockerfile declares ``VOLUME /config`` and
nothing else — so anything written under ``/app`` lives in the container's
writable layer and is destroyed when the container is recreated. Recreating the
container is exactly what a user does when they pull a new image, which is
exactly when the update banner is relevant. The throttle therefore reset at the
worst possible moment and admins got re-nagged on every recreate.

Moving the file to ``/config`` (a declared volume, alongside its siblings
``cwa_ingest_status``, ``cwa_ingest_retry_queue`` and the logs) makes the
once-per-day promise actually hold across upgrades.

Pins here:

1. The path is a single module-level constant, not re-hardcoded per call site
   (four copies of the same literal is how a future edit silently moves one
   reader and leaves the writer behind).
2. The path lives under the persistent ``/config`` volume and never under
   ``/app`` — this is the bug @chloeroform fixed and it must not regress.
3. First run with no file: the sentinel ``0001-01-01`` is returned so exactly
   one notification fires, and the file is created carrying today's date.
4. Subsequent run: the stored date is returned verbatim and not clobbered.
5. When the stored date is today, the notification path returns early without
   reaching out to the network for a version check.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts')))

import cps.render_template as rt

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
RENDER_PY = REPO_ROOT / "cps" / "render_template.py"


def test_notice_path_is_a_single_module_constant():
    """SSOT: one constant, and no stray hardcoded copies of the filename."""
    assert hasattr(rt, "CWA_UPDATE_NOTICE_PATH"), (
        "the update-notice path must be a module-level constant so readers and "
        "writers cannot drift apart (cf. LOG_ARCHIVE in cps/cwa_functions.py)"
    )

    source = RENDER_PY.read_text()
    # The filename should appear exactly once as a path literal: in the constant.
    literals = re.findall(r"""["'][^"']*cwa_update_notice["']""", source)
    assert len(literals) == 1, (
        f"expected the cwa_update_notice path literal exactly once (the constant), "
        f"found {len(literals)}: {literals}"
    )


def test_notice_lives_on_the_persistent_config_volume_not_app():
    """The throttle file must survive container recreation.

    ``/app`` is baked into the image and is wiped on recreate; ``/config`` is a
    declared VOLUME. Writing the throttle to ``/app`` made it reset on every
    image pull, which is precisely when the update banner matters.
    """
    path = rt.CWA_UPDATE_NOTICE_PATH

    assert path.startswith("/config/"), (
        f"update notice must live on the persistent /config volume, got {path!r}"
    )
    assert not path.startswith("/app"), (
        "regression: the update notice is back on the ephemeral /app layer, so "
        "the once-per-day throttle resets every time the container is recreated"
    )


def test_first_run_returns_sentinel_and_creates_the_file(tmp_path, monkeypatch):
    """No file yet: fire exactly one notification, and record today."""
    notice = tmp_path / "cwa_update_notice"
    monkeypatch.setattr(rt, "CWA_UPDATE_NOTICE_PATH", str(notice))

    assert not notice.exists()

    result = rt.get_cwa_last_notification()

    # The sentinel is deliberately an impossible date so the caller's
    # "already notified today?" comparison always fails on a fresh install.
    assert result == "0001-01-01"
    assert notice.exists(), "the first call must create the throttle file"
    assert notice.read_text() == datetime.now().strftime("%Y-%m-%d")


def test_existing_file_is_read_back_verbatim_and_not_clobbered(tmp_path, monkeypatch):
    """A stored date is returned as-is; reading must not rewrite the file."""
    notice = tmp_path / "cwa_update_notice"
    notice.write_text("2026-01-15")
    monkeypatch.setattr(rt, "CWA_UPDATE_NOTICE_PATH", str(notice))

    result = rt.get_cwa_last_notification()

    assert result == "2026-01-15"
    assert notice.read_text() == "2026-01-15", (
        "reading the throttle must not overwrite it — otherwise every page "
        "render resets the once-per-day window"
    )


def test_same_day_notification_short_circuits_before_the_version_check(tmp_path, monkeypatch):
    """Already notified today: no network round-trip, no second banner."""
    notice = tmp_path / "cwa_update_notice"
    notice.write_text(datetime.now().strftime("%Y-%m-%d"))
    monkeypatch.setattr(rt, "CWA_UPDATE_NOTICE_PATH", str(notice))

    called = {"update_available": 0, "flash": 0}

    class _FakeDB:
        cwa_settings = {"cwa_update_notifications": True}

    def _fake_update_available():
        called["update_available"] += 1
        return True, "v1.0.0", "v9.9.9"

    monkeypatch.setattr(rt, "CWA_DB", lambda: _FakeDB())
    monkeypatch.setattr(rt, "cwa_update_available", _fake_update_available)
    monkeypatch.setattr(rt, "flash", lambda *a, **k: called.__setitem__("flash", called["flash"] + 1))

    rt.cwa_update_notification()

    assert called["update_available"] == 0, (
        "the daily throttle must short-circuit before the upstream version check"
    )
    assert called["flash"] == 0, "no second banner on the same calendar day"
