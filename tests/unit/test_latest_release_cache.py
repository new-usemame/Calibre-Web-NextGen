# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The "update available" check must resolve the latest release on demand.

Fork issue #1108 (reported by @chloeroform): the check compared the installed
version against ``constants.STABLE_VERSION``, a snapshot written once by
``cwa-init`` into ``/app/CWA_STABLE_RELEASE`` and read at module import. Two
layers of staleness stacked — the file is never rewritten while the container
runs, and the constant is bound at import so even rewriting the file changes
nothing in the live process. Both were reproduced on a running container. A
Docker deployment that stayed up for a week therefore never learned about
anything released during that week, which is how most people run this.

The fix resolves the tag on demand and caches it. That introduces two hazards
these tests exist to pin:

* **Hub stalls.** ``cps/server.py`` runs gevent WITHOUT ``monkey.patch_all()``
  (see ``cps/services/parallel.py``), so a blocking socket read on a request
  greenlet freezes every other request. ``cwa_update_available()`` runs on the
  admin page render path, so an inline ``requests.get`` would let one admin
  page load stall the whole app for a GitHub timeout. The probe must go
  through ``parallel.fan_out``.
* **API rate limits.** GitHub allows 60 unauthenticated requests/hour/IP.
  Without caching, every admin page render would spend one.
"""

from __future__ import annotations

import ast
import importlib
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def latest_release():
    module = importlib.import_module("cps.services.latest_release")
    module.reset_cache()
    yield module
    module.reset_cache()


@pytest.mark.unit
class TestRootCauseIsGone:
    """The boot-time snapshot must not come back in any form."""

    def test_constants_no_longer_exposes_a_stable_version(self):
        from cps import constants
        assert not hasattr(constants, "STABLE_VERSION"), (
            "constants.STABLE_VERSION is an import-time binding of a file "
            "written once at container boot — it cannot be anything but "
            "stale on a long-running install (#1108)"
        )

    def test_update_check_does_not_read_a_boot_time_snapshot(self):
        from cps.render_template import cwa_update_available
        import inspect
        src = inspect.getsource(cwa_update_available)
        assert "STABLE_VERSION" not in src
        assert "get_latest_release_tag" in src

    def test_cwa_init_no_longer_persists_a_stable_release_file(self):
        run = (REPO_ROOT / "root/etc/s6-overlay/s6-rc.d/cwa-init/run").read_text()
        assert "CWA_STABLE_RELEASE" not in run

    def test_the_build_still_stamps_the_installed_version(self):
        """The stamp moved from cwa-init to a final-stage ENV, not away.

        cwa-init used to `cat /app/CWA_RELEASE` and export the result. The
        file and that block are both gone; the build now sets the env var
        directly, which reaches every s6 service the same way CALIBRE_DBPATH
        does. What must not change is that *something in the build* still
        stamps it — without that, cps.constants falls back to package
        metadata, which is frozen at the checked-in VERSION file and is
        therefore stale for every release after the one that touched it.
        """
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        assert "ENV CWA_INSTALLED_VERSION=${VERSION}" in dockerfile, (
            "the image must stamp the version it was built from; package "
            "metadata cannot, because it is fixed at pip-install time"
        )
        # And it has to be stamped from the build arg the workflows pass,
        # not hardcoded.
        assert "ARG VERSION" in dockerfile

    def test_dev_build_version_strings_are_not_forced_through_pep440(self):
        """A dev image reports DEV_BUILD-dev-<n>, which is not a PEP 440
        version. Routing the installed version through package metadata (i.e.
        through setuptools' VERSION file) would reject that string and fail
        the dev image build outright, so the env stamp must take precedence
        over the metadata fallback.
        """
        constants_src = (REPO_ROOT / "cps/constants.py").read_text()
        stamp_line = next(
            line for line in constants_src.splitlines()
            if line.startswith("_stamped_version")
        )
        env_pos = stamp_line.index("CWA_INSTALLED_VERSION")
        pkg_pos = stamp_line.index("_get_version")
        assert env_pos < pkg_pos, (
            "the env stamp must be the first operand of the `or` — package "
            "metadata cannot represent a DEV_BUILD-dev-<n> version at all"
        )


@pytest.mark.unit
class TestNoBlockingHttpOnTheRequestPath:
    """The regression that would freeze the app for every user."""

    def test_render_template_makes_no_direct_http_call(self):
        src = (REPO_ROOT / "cps/render_template.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert not (node.value.id == "requests"), (
                    "render_template runs on the page render path; a blocking "
                    "requests call there parks the single gevent hub thread "
                    "and freezes every other in-flight request"
                )

    def test_probe_runs_through_the_gevent_safe_fanout(self):
        src = (REPO_ROOT / "cps/services/latest_release.py").read_text()
        assert "from .parallel import fan_out" in src
        assert "fan_out(" in src
        tree = ast.parse(src)
        # The requests import must be inside the worker function, and the only
        # caller of that worker must be the fan_out job tuple.
        worker = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_http_get_tag"
        )
        assert any(isinstance(n, ast.Import) and n.names[0].name == "requests"
                   for n in ast.walk(worker))

    def test_no_lock_is_held_across_the_network_call(self):
        """Under unpatched gevent a threading.Lock held across a hub yield
        deadlocks: the waiter blocks the only OS thread, so the holder can
        never be scheduled to release it."""
        src = (REPO_ROOT / "cps/services/latest_release.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.With):
                assert False, "no `with <lock>` may wrap the fetch path"


@pytest.mark.unit
class TestCaching:
    def test_successful_lookup_is_cached(self, latest_release, mocker):
        probe = mocker.patch.object(
            latest_release, "_fetch_latest_release_tag", return_value="v4.1.23"
        )
        assert latest_release.get_latest_release_tag() == "v4.1.23"
        assert latest_release.get_latest_release_tag() == "v4.1.23"
        assert latest_release.get_latest_release_tag() == "v4.1.23"
        assert probe.call_count == 1, (
            "GitHub allows 60 unauthenticated requests/hour/IP; one per admin "
            "page render would exhaust that in a single sitting"
        )

    def test_cache_refreshes_after_the_success_ttl(self, latest_release, mocker):
        probe = mocker.patch.object(
            latest_release, "_fetch_latest_release_tag", side_effect=["v4.1.23", "v4.1.24"]
        )
        assert latest_release.get_latest_release_tag() == "v4.1.23"
        latest_release._cache_expires_at = 0.0  # TTL elapsed
        assert latest_release.get_latest_release_tag() == "v4.1.24"
        assert probe.call_count == 2

    def test_failure_is_negative_cached_and_returns_empty(self, latest_release, mocker):
        probe = mocker.patch.object(
            latest_release, "_fetch_latest_release_tag", return_value=""
        )
        assert latest_release.get_latest_release_tag() == ""
        assert latest_release.get_latest_release_tag() == ""
        assert probe.call_count == 1, (
            "an offline install must not retry the probe on every render"
        )

    def test_failure_keeps_serving_the_last_known_tag(self, latest_release, mocker):
        mocker.patch.object(
            latest_release, "_fetch_latest_release_tag", side_effect=["v4.1.23", ""]
        )
        assert latest_release.get_latest_release_tag() == "v4.1.23"
        latest_release._cache_expires_at = 0.0
        assert latest_release.get_latest_release_tag() == "v4.1.23", (
            "a transient network blip must not blank out a tag we already know"
        )


@pytest.mark.unit
class TestProbeBehaviour:
    def test_never_raises_when_the_network_fails(self, latest_release, mocker):
        mocker.patch.object(
            latest_release, "_http_get_tag", side_effect=OSError("network unreachable")
        )
        assert latest_release.get_latest_release_tag() == ""

    @pytest.mark.parametrize("tag", ["", "not-a-version", "<html>404</html>", "v4.1", "4"])
    def test_rejects_a_malformed_tag(self, latest_release, mocker, tag):
        response = mocker.MagicMock()
        response.json.return_value = {"tag_name": tag}
        mocker.patch("requests.get", return_value=response)
        assert latest_release._http_get_tag() == ""

    @pytest.mark.parametrize("tag", ["v4.1.23", "4.1.23", "v4.1.23-beta.1"])
    def test_accepts_a_real_release_tag(self, latest_release, mocker, tag):
        response = mocker.MagicMock()
        response.json.return_value = {"tag_name": tag}
        mocker.patch("requests.get", return_value=response)
        assert latest_release._http_get_tag() == tag

    def test_release_repo_override_is_honoured(self, latest_release, monkeypatch):
        assert latest_release.release_repo() == latest_release.DEFAULT_RELEASE_REPO
        monkeypatch.setenv("CWA_RELEASE_REPO", "someone/their-fork")
        assert latest_release.release_repo() == "someone/their-fork"

    def test_probe_identifies_itself(self, latest_release, mocker):
        """GitHub asks API clients to send a User-Agent, and ours carries the
        installed version so rate-limit questions are answerable."""
        response = mocker.MagicMock()
        response.json.return_value = {"tag_name": "v4.1.23"}
        get = mocker.patch("requests.get", return_value=response)
        latest_release._http_get_tag()
        headers = get.call_args.kwargs["headers"]
        assert headers["User-Agent"].startswith("Calibre-Web-NextGen/")
        assert get.call_args.kwargs["timeout"] == latest_release.HTTP_TIMEOUT


@pytest.mark.unit
class TestInstalledVersionReporting:
    """#1108 part 1: several call sites reported the newest *published* tag
    where they meant the version actually running."""

    def test_cli_reports_the_installed_version(self):
        import inspect
        from cps import cli
        src = inspect.getsource(cli.version_info)
        assert "INSTALLED_VERSION" in src
        assert "STABLE_VERSION" not in src

    def test_updater_stable_info_reports_the_installed_version(self):
        import inspect
        from cps.updater import Updater
        src = inspect.getsource(Updater._stable_version_info)
        assert "INSTALLED_VERSION" in src
        assert "STABLE_VERSION" not in src

    def test_packaging_version_reads_a_file_not_the_constant(self):
        """The dependency direction is now pyproject -> VERSION, not
        pyproject -> constants.

        constants.py resolves the version *from installed package metadata*,
        so a pyproject that derived its version from constants.py would be
        circular: setuptools would have to import the module whose value it is
        supposed to be producing. The VERSION file breaks the cycle and is the
        only definition setuptools reads.
        """
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        assert 'version = {file = ["VERSION"]}' in pyproject
        assert 'attr = "calibreweb.cps.constants.INSTALLED_VERSION"' not in pyproject, (
            "reading the version back out of constants.py reintroduces the "
            "cycle; constants.py now reads it from package metadata"
        )
        assert (REPO_ROOT / "VERSION").read_text().strip(), "VERSION must not be empty"

    def test_failed_version_lookup_falls_back_instead_of_blanking(self):
        """An unresolvable version used to yield an empty INSTALLED_VERSION,
        which silently disables the update indicator instead of reading as an
        unknown version.

        The mechanism changed (file read -> importlib.metadata) but the
        invariant did not: never return the empty string.
        """
        from cps.constants import _get_version
        from importlib import metadata
        import unittest.mock as mock

        with mock.patch.object(
            metadata, "version", side_effect=metadata.PackageNotFoundError
        ):
            assert _get_version("v0.0.0") == "v0.0.0"
            assert _get_version() == ""

        with mock.patch.object(metadata, "version", return_value="4.1.23"):
            assert _get_version("v0.0.0") == "v4.1.23"


@pytest.mark.unit
class TestConcurrentRefresh:
    """The AST tripwires above catch a wholesale reversion; they do not prove
    the slot-claim state machine holds when greenlets actually interleave.
    These drive two real callers through a probe that is parked mid-flight."""

    @staticmethod
    def _parked_probe(latest_release, mocker, result="v4.1.24"):
        gevent = pytest.importorskip("gevent")
        started = gevent.event.Event()
        release = gevent.event.Event()
        calls = []

        def slow_fetch():
            calls.append(1)
            started.set()
            release.wait(timeout=10)
            return result

        mocker.patch.object(latest_release, "_fetch_latest_release_tag", side_effect=slow_fetch)
        return gevent, started, release, calls

    def _seed(self, latest_release, mocker, tag="v4.1.23"):
        mocker.patch.object(latest_release, "_fetch_latest_release_tag", return_value=tag)
        assert latest_release.get_latest_release_tag() == tag
        latest_release._cache_expires_at = 0.0  # TTL elapsed

    def test_caller_arriving_mid_probe_gets_the_last_known_tag(self, latest_release, mocker):
        self._seed(latest_release, mocker)
        gevent, started, release, calls = self._parked_probe(latest_release, mocker)

        first = gevent.spawn(latest_release.get_latest_release_tag)
        assert started.wait(timeout=5), "the probe never started"

        assert latest_release.get_latest_release_tag() == "v4.1.23", (
            "a second render must be served the tag we already have, not wait "
            "on the in-flight probe"
        )
        assert len(calls) == 1

        release.set()
        assert first.get(timeout=5) == "v4.1.24"
        assert len(calls) == 1
        assert latest_release.get_latest_release_tag() == "v4.1.24"

    def test_a_wedged_probe_does_not_let_a_second_one_start(self, latest_release, mocker):
        """The failure TTL is only a lease. A worker stuck longer than it — a
        name resolution outliving the socket timeout, say — must not let every
        later caller open another connection."""
        self._seed(latest_release, mocker)
        gevent, started, release, calls = self._parked_probe(latest_release, mocker)

        first = gevent.spawn(latest_release.get_latest_release_tag)
        assert started.wait(timeout=5)

        latest_release._cache_expires_at = 0.0  # the 15-minute lease elapses

        assert latest_release.get_latest_release_tag() == "v4.1.23"
        assert latest_release.get_latest_release_tag() == "v4.1.23"
        assert len(calls) == 1, "a wedged probe must not be joined by others"

        release.set()
        assert first.get(timeout=5) == "v4.1.24"

    def test_in_flight_flag_is_cleared_when_the_probe_raises(self, latest_release, mocker):
        """A raising probe must not leave the module permanently convinced a
        refresh is running, which would freeze the tag until a restart."""
        mocker.patch.object(
            latest_release, "_fetch_latest_release_tag", side_effect=RuntimeError("boom")
        )
        with pytest.raises(RuntimeError):
            latest_release.get_latest_release_tag()
        assert latest_release._refresh_in_flight is False
