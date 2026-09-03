# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Production-image regression for the private Calibre ingest API boundary."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.docker_integration
REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = Path(__file__).with_name("calibre_ingest_runtime_probe.py")
FIXTURE = REPO_ROOT / "tests/fixtures/sample_books/test_minimal_valid.epub"


def test_real_calibre_identical_overwrite_and_rollback_recovery(
    cwa_container, container_name
):
    """Exercise inspect, validation, recovery, overwrite, marker, and restore.

    The Integration lane builds the PR image first. Copying only the probe and
    fixture into that running image ensures the helper and processor under test
    are the exact files baked into the image, not host-mounted substitutes.
    """
    remote_probe = "/tmp/cwng_calibre_ingest_runtime_probe.py"
    remote_fixture = "/tmp/cwng_calibre_ingest_fixture.epub"
    subprocess.run(
        ["docker", "cp", str(PROBE), f"{container_name}:{remote_probe}"],
        check=True,
    )
    subprocess.run(
        ["docker", "cp", str(FIXTURE), f"{container_name}:{remote_fixture}"],
        check=True,
    )
    completed = subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "cwa-as-abc",
            "python3",
            remote_probe,
            "--fixture",
            remote_fixture,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert completed.returncode == 0, (
        f"production-image ingest probe failed ({completed.returncode})\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    records = [
        line.split("=", 1)[1]
        for line in reversed(completed.stdout.splitlines())
        if line.startswith("CWNG_RUNTIME_PROBE=")
    ]
    assert records, f"probe emitted no result record:\n{completed.stdout}"
    result = json.loads(records[0])

    assert result["calibre_version"] == "calibre-debug (calibre 9.11)"
    assert result["success"]["actions"].count("inspect") >= 2
    assert result["success"]["actions"][-1] == "import"
    assert result["success"]["sanity"] == [True]
    assert result["success"]["recovery"] == [True]
    assert result["success"]["book_count"] == 1
    assert result["success"]["marker_count"] == 1
    assert result["success"]["library_digest"] == result["success"]["incoming_digest"]

    assert result["rollback"]["expected_error"] is True
    assert result["rollback"]["book_count"] == 1
    assert result["rollback"]["marker_count"] == 0
    assert result["rollback"]["restore"]
    assert (
        result["rollback"]["restore"][0]["replacement_digest_before_restore"]
        == result["rollback"]["incoming_digest"]
    )
    assert result["rollback"]["library_digest"] == result["rollback"]["existing_digest"]
