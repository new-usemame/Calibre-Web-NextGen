# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pin the admin Version Information table's "update available" indicator
(fork issue #125 follow-up).

PR #136 fixed the "Check for Update" button's Python flow, but Docker
builds set ``feature_support['updater'] = False`` (constants.UPDATER_AVAILABLE
is False in containers because the updater can't replace files at runtime
inside an image). The button never renders for Docker users — i.e. the
operator's deployment + every fork user pulling
``ghcr.io/new-usemame/calibre-web-nextgen``. Those users had no way to learn
from the admin UI that a newer release existed.

These tests pin:

1. ``cwa_get_update_indicator()`` returns ``(True, "v4.0.46")`` when stable
   is strictly newer than installed.
2. It returns ``(False, ...)`` when installed equals or exceeds stable.
3. It returns ``(False, "")`` gracefully on any exception from
   ``cwa_update_available`` (never breaks the admin page render).
4. The admin route passes the indicator into the template under the names
   ``cwa_is_outdated`` and ``cwa_latest_tag``.
5. The template renders "Update available: <tag>" with the docker-pull
   command when outdated, otherwise the original "Current Version" label.
"""

import inspect

import pytest


@pytest.mark.unit
class TestUpdateIndicatorHelper:
    def test_returns_true_when_stable_is_newer(self, mocker):
        from cps import admin
        mocker.patch(
            "cps.render_template.cwa_update_available",
            return_value=(True, "v4.0.34", "v4.0.46"),
        )
        is_outdated, latest = admin.cwa_get_update_indicator()
        assert is_outdated is True
        assert latest == "v4.0.46"

    def test_returns_false_when_installed_equals_stable(self, mocker):
        from cps import admin
        mocker.patch(
            "cps.render_template.cwa_update_available",
            return_value=(False, "v4.0.46", "v4.0.46"),
        )
        is_outdated, latest = admin.cwa_get_update_indicator()
        assert is_outdated is False

    def test_swallow_exceptions_returns_safe_default(self, mocker):
        """The admin page must never fail to render because of a version
        probe glitch (network, missing file, parsing error, etc.). The
        indicator helper has to return a clean default on any throw."""
        from cps import admin
        mocker.patch(
            "cps.render_template.cwa_update_available",
            side_effect=RuntimeError("boom"),
        )
        is_outdated, latest = admin.cwa_get_update_indicator()
        assert is_outdated is False
        assert latest == ""


@pytest.mark.unit
class TestUpdateAvailableDevBuild:
    """A :dev / unversioned build must NOT show a false "update available".

    The :dev image stamps INSTALLED_VERSION as e.g. "DEV_BUILD-dev-247", which
    parses to a ``(0,)`` version tuple — so a naive compare against the latest
    stable tag always reads as "outdated", nagging the canary/dev box about an
    update it is actually *ahead* of. Real releases must still be compared."""

    """ TODO: UPDATE TEST"""
    def test_dev_build_does_not_flag_update(self, mocker):
        from cps.render_template import cwa_update_available
        mocker.patch("cps.constants.INSTALLED_VERSION", "DEV_BUILD-dev-247")
        mocker.patch("cps.constants.STABLE_VERSION", "v4.0.170")
        is_newer, _current, _latest = cwa_update_available()
        assert is_newer is False

    """ TODO: UPDATE TEST"""
    def test_real_release_still_flags_update(self, mocker):
        from cps.render_template import cwa_update_available
        mocker.patch("cps.constants.INSTALLED_VERSION", "v4.0.100")
        mocker.patch("cps.constants.STABLE_VERSION", "v4.0.170")
        is_newer, _current, _latest = cwa_update_available()
        assert is_newer is True

    """ TODO: UPDATE TEST"""
    def test_up_to_date_release_does_not_flag(self, mocker):
        from cps.render_template import cwa_update_available
        mocker.patch("cps.constants.INSTALLED_VERSION", "v4.0.170")
        mocker.patch("cps.constants.STABLE_VERSION", "v4.0.170")
        is_newer, _current, _latest = cwa_update_available()
        assert is_newer is False


@pytest.mark.unit
class TestAdminRoutePassesIndicator:
    def test_admin_route_source_passes_indicator_kwargs(self):
        """Source-pin: ``admin()`` must include cwa_is_outdated and
        cwa_latest_tag in the render_title_template kwargs. A refactor
        that drops these would silently regress @SpookyUSAF's symptom."""
        from cps import admin
        src = inspect.getsource(admin.admin)
        assert "cwa_get_update_indicator()" in src, (
            "admin() must call cwa_get_update_indicator() to populate the "
            "outdated flag for the Version Information table"
        )
        assert "cwa_is_outdated=" in src
        assert "cwa_latest_tag=" in src


@pytest.mark.unit
class TestAdminTemplateRendersIndicator:
    """Template-source pin: when outdated, the admin.html Version
    Information row renders 'Update available' with the latest tag and an
    'Update now' button that opens the guided update modal; otherwise the
    original 'Current Version' label. We assert against the template source
    rather than full Jinja rendering so this test stays independent of the
    Flask app context."""

    def test_template_has_outdated_branch(self):
        import os
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        admin_html = os.path.join(repo_root, "cps", "templates", "admin.html")
        with open(admin_html, encoding="utf-8") as fh:
            template = fh.read()
        # The new branch must reference both variables.
        assert "cwa_is_outdated" in template
        assert "cwa_latest_tag" in template
        # And it must surface a way to act on the update: the "Update now"
        # button that opens the guided update modal. This replaced the old
        # inline `docker pull` command, which was incomplete on its own
        # (pulling an image does not update a running container) — the modal
        # now carries the full, setup-aware pull+recreate instructions.
        # See cps/templates/update_now_modal.html.
        assert "#updateNowDialog" in template
        assert "Update now" in template

    def test_template_preserves_current_version_fallback(self):
        import os
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        admin_html = os.path.join(repo_root, "cps", "templates", "admin.html")
        with open(admin_html, encoding="utf-8") as fh:
            template = fh.read()
        # The pre-existing label must remain in the else-branch so up-to-date
        # installs see the original wording.
        assert "{{_('Current Version')}}" in template
