# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Service bodies in scripts/services run outside s6 (#2094).

The s6 run files keep only container-only setup and exec a script under
scripts/services. These tests pin that contract so systemd or a plain shell
can run the same scripts.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

from tests.fixtures.service_sources import S6_ROOT, SERVICES_DIR

pytestmark = pytest.mark.unit

EXEC_LINE = re.compile(
    r"^exec /app/calibre-web-automated/scripts/services/([\w-]+\.sh)\s*$", re.MULTILINE
)


def _thin_run_files():
    found = {}
    for run in sorted(S6_ROOT.glob("*/run")):
        match = EXEC_LINE.search(run.read_text(encoding="utf-8"))
        if match:
            found[run.parent.name] = SERVICES_DIR / match.group(1)
    return found


def test_some_services_have_moved():
    assert _thin_run_files(), "no s6 run file execs a scripts/services body"


@pytest.mark.parametrize("service", sorted(_thin_run_files()))
def test_run_file_execs_an_executable_script_of_its_own_name(service):
    script = _thin_run_files()[service]
    assert script.name == f"{service}.sh"
    assert script.is_file(), f"{script} is missing"
    assert os.access(script, os.X_OK), f"{script} is not executable"
    assert script.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\n")


@pytest.mark.parametrize("script", sorted(SERVICES_DIR.glob("[!_]*.sh")), ids=lambda p: p.name)
def test_service_script_is_free_of_container_paths(script):
    body = script.read_text(encoding="utf-8")
    live = [line for line in body.splitlines() if not line.lstrip().startswith("#")]
    assert '. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"' in live
    offenders = [line for line in live if "/app/" in line or "cwa-as-abc" in line]
    assert not offenders, f"{script.name} still depends on the image layout: {offenders}"


@pytest.mark.parametrize("script", sorted(SERVICES_DIR.glob("[!_]*.sh")), ids=lambda p: p.name)
def test_external_commands_never_wrap_the_cwa_run_as_function(script):
    """timeout, exec and friends run programs, not shell functions; they take
    "${CWA_RUN_AS_ARGV[@]}" instead."""
    wrapped = re.compile(r"\b(?:timeout|exec|xargs|nohup|env|setsid|nice|flock)\b[^|;&#]*\bcwa_run_as\b")
    offenders = [
        line.strip()
        for line in script.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#") and wrapped.search(line)
    ]
    assert not offenders, f"{script.name}: {offenders}"


def test_cwa_run_as_argv_works_under_timeout(tmp_path):
    marker = tmp_path / "prefix-ran"
    prefix = tmp_path / "as-user"
    prefix.write_text(f'#!/bin/sh\necho "$@" > "{marker}"\n')
    prefix.chmod(0o755)
    snippet = f'. "{SERVICES_DIR / "_common.sh"}"; timeout 5 "${{CWA_RUN_AS_ARGV[@]}}" echo direct'

    bare = subprocess.run(
        ["bash", "-c", snippet], env={**os.environ, "CWA_RUN_AS": ""},
        capture_output=True, text=True, check=True,
    )
    assert bare.stdout.strip() == "direct"

    subprocess.run(
        ["bash", "-c", snippet], env={**os.environ, "CWA_RUN_AS": str(prefix)},
        capture_output=True, text=True, check=True,
    )
    assert marker.read_text().strip() == "echo direct"


def test_cwa_run_as_runs_the_prefix_or_the_bare_command(tmp_path):
    marker = tmp_path / "prefix-ran"
    prefix = tmp_path / "as-user"
    prefix.write_text(f'#!/bin/sh\necho "$@" > "{marker}"\n')
    prefix.chmod(0o755)
    snippet = f'. "{SERVICES_DIR / "_common.sh"}"; cwa_run_as echo direct'

    bare = subprocess.run(
        ["bash", "-c", snippet], env={**os.environ, "CWA_RUN_AS": ""},
        capture_output=True, text=True, check=True,
    )
    assert bare.stdout.strip() == "direct"

    subprocess.run(
        ["bash", "-c", snippet], env={**os.environ, "CWA_RUN_AS": str(prefix)},
        capture_output=True, text=True, check=True,
    )
    assert marker.read_text().strip() == "echo direct"


def test_common_locates_the_app_root_from_its_own_path(tmp_path):
    result = subprocess.run(
        ["bash", "-c", f'unset CWA_APP_ROOT; . "{SERVICES_DIR / "_common.sh"}"; echo "$CWA_SCRIPTS"'],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == str(SERVICES_DIR.parent)
