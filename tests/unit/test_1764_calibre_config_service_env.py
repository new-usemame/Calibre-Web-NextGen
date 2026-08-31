# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression pins for fork #1764's mixed-root/abc Calibre config."""

from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
S6_ROOT = REPO_ROOT / "root/etc/s6-overlay/s6-rc.d"
ABC_CONFIG = "/config/.config/calibre-runtime"

# Direct command plus the Python service entry points whose reachable code
# shells out to calibre tools. Keep this list paired with the invocation audit
# in the test below: adding a new service without choosing a uid-safe config is
# the regression vector this file exists to catch.
ABC_SERVICES = (
    "cwa-ingest-service",
    "svc-calibre-web-automated",
)
ROOT_SERVICES = (
    "calibre-binaries-setup",
    "cwa-auto-library",
    "metadata-change-detector",
)


def _run_script(service):
    return (S6_ROOT / service / "run").read_text(encoding="utf-8")


def _live_lines(source):
    return [
        line.strip()
        for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _depends_on(service, target, seen=None):
    if service == target:
        return True
    seen = set() if seen is None else seen
    if service in seen:
        return False
    seen.add(service)
    dependency_dir = S6_ROOT / service / "dependencies.d"
    if not dependency_dir.is_dir():
        return False
    return any(
        _depends_on(entry.name, target, seen)
        for entry in dependency_dir.iterdir()
    )


@pytest.mark.parametrize("service", ABC_SERVICES)
def test_abc_calibre_services_export_the_prepared_plugin_free_config(service):
    source = _run_script(service)
    assert "export CALIBRE_CONFIG_DIRECTORY=" in source
    assert ABC_CONFIG in source
    assert _depends_on(service, "cwa-init"), (
        f"{service} can start before cwa-init creates {ABC_CONFIG}"
    )


@pytest.mark.parametrize("service", ROOT_SERVICES)
def test_root_calibre_services_use_a_uid_private_config(service):
    source = _run_script(service)
    assert "export CALIBRE_CONFIG_DIRECTORY=" in source
    assert "/tmp/cwa-calibre-config-$(id -u)" in source
    assert 'install -d -m 0700 "$CALIBRE_CONFIG_DIRECTORY"' in source


def test_init_prepares_abc_runtime_config_without_exposing_opt_in_plugins():
    source = _run_script("cwa-init")
    runtime_create = "install -d -o abc -g abc /config/.config/calibre-runtime"
    plugin_create = "install -d -o abc -g abc /config/.config/calibre/plugins"
    assert runtime_create in source
    assert plugin_create in source
    assert "/config/.config/calibre-runtime/plugins" not in source


def test_audit_names_every_s6_service_reaching_a_calibre_tool():
    # Observed call graph at origin/main:
    # - calibre-binaries-setup -> calibredb
    # - cwa-auto-library -> auto_library -> calibre-customize (opt-in)
    # - cwa-ingest-service -> ingest_processor -> calibredb/ebook-convert/kepubify
    # - metadata-change-detector -> dispatcher -> cover_enforcer ->
    #   calibredb/ebook-polish
    # - svc-calibre-web-automated -> cps -> TaskConvert/convert_library ->
    #   ebook-convert/calibredb/kepubify
    expected = set(ABC_SERVICES + ROOT_SERVICES)
    all_sources = {
        path.parent.name: path.read_text(encoding="utf-8")
        for path in S6_ROOT.glob("*/run")
    }
    reachability_markers = {
        "calibre-binaries-setup": "timeout 10 calibredb --version",
        "cwa-auto-library": "python3 /app/calibre-web-automated/scripts/auto_library.py",
        "cwa-ingest-service": "python3 /app/calibre-web-automated/scripts/ingest_processor.py",
        "metadata-change-detector": "metadata_change_dispatch.py",
        "svc-calibre-web-automated": "cwa-as-abc python3 -P -m cps",
    }
    assert set(reachability_markers) == expected
    for service, marker in reachability_markers.items():
        assert any(marker in line for line in _live_lines(all_sources[service]))

    configured = {
        service
        for service, source in all_sources.items()
        if any("export CALIBRE_CONFIG_DIRECTORY=" in line
               for line in _live_lines(source))
    }
    assert configured == expected


def test_no_service_relies_on_roots_implicit_calibre_config():
    for service in ABC_SERVICES + ROOT_SERVICES:
        assert not any(
            "/root/.config/calibre" in line
            for line in _live_lines(_run_script(service))
        )
