# SPDX-License-Identifier: GPL-3.0-or-later
"""Acquisition correctness against the candidate image's actual Calibre runtime."""
import json
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.docker_integration
ROOT = Path(__file__).resolve().parents[2]


def test_real_acquisition_retention_reinspection_and_durable_receipt(
    cwa_container, container_name
):
    # Copy test inputs only. Product modules must be baked into the CI image.
    files = {
        ROOT
        / "tests/integration/acquisition_calibre_runtime_probe.py": "/tmp/acquisition-runtime-probe.py",
        ROOT
        / "tests/integration/calibre_ingest_runtime_probe.py": "/tmp/acquisition-base-probe.py",
        ROOT
        / "tests/fixtures/sample_books/test_minimal_valid.epub": "/tmp/acquisition-fixture.epub",
    }
    for source, target in files.items():
        subprocess.run(
            ["docker", "cp", str(source), container_name + ":" + target], check=True
        )
    result = subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "cwa-as-abc",
            "python3",
            "/tmp/acquisition-runtime-probe.py",
            "--fixture",
            "/tmp/acquisition-fixture.epub",
            "--setup-probe",
            "/tmp/acquisition-base-probe.py",
        ],
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    records = [
        line.split("=", 1)[1]
        for line in result.stdout.splitlines()
        if line.startswith("CWNG_ACQUISITION_RUNTIME=")
    ]
    assert len(records) == 1, result.stdout
    proof = json.loads(records[0])
    assert proof["calibre_version"].startswith("calibre-debug (calibre ")
    assert proof["helper"]["retained"]["disposition"] == "existing_retained"
    assert proof["helper"]["removed_reacquire"]["status"] == "imported"
    assert (
        proof["helper"]["replacement_reinspect"]["disposition"] == "existing_retained"
    )
    assert proof["helper"]["forged_identifier_ignored"]["book_ids"] != [1]
    assert proof["receipt"]["real_ub_schema"]
    assert proof["receipt"]["receipt_failure_rolled_back_membership"]
    assert proof["receipt"]["source_retained"]
    assert proof["receipt"]["acknowledged"]
    assert proof["receipt"]["membership_count"] == proof["receipt"]["book_count"] == 1
    assert proof["receipt"]["helper_calls"] == ["import"]
    assert proof["receipt"]["retry_reimports"] is False


def test_owned_opds_worker_full_processor_conversion_and_receipt(
    cwa_container, container_name
):
    files = {
        ROOT
        / "tests/integration/acquisition_full_runtime_probe.py": "/tmp/acquisition_full_runtime_probe.py",
        ROOT
        / "tests/integration/acquisition_calibre_runtime_probe.py": "/tmp/acquisition_calibre_runtime_probe.py",
        ROOT
        / "tests/fixtures/sample_books/test_minimal_valid.epub": "/tmp/acquisition-full-fixture.epub",
    }
    for source, target in files.items():
        subprocess.run(
            ["docker", "cp", str(source), container_name + ":" + target], check=True
        )
    result = subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "cwa-as-abc",
            "python3",
            "/tmp/acquisition_full_runtime_probe.py",
            "--fixture",
            "/tmp/acquisition-full-fixture.epub",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    records = [
        line.split("=", 1)[1]
        for line in result.stdout.splitlines()
        if line.startswith("CWNG_ACQUISITION_FULL_RUNTIME=")
    ]
    assert len(records) == 1, result.stdout
    proof = json.loads(records[0])
    assert proof["http_gets"] == [
        "/catalog",
        "/patch.epub",
        "/duplicate.epub",
        "/convert.epub",
        "/direct.pdf",
    ]
    assert [case["format"] for case in proof["results"]] == [
        "epub",
        "epub",
        "kepub",
        "pdf",
    ]
    assert proof["results"][0]["receipt_retry"]
    assert all(case["source_and_private_cleanup"] for case in proof["results"])
