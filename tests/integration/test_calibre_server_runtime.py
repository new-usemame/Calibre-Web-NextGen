# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the managed-server protocol in the freshly built product image."""

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker_integration
PROBE = Path(__file__).with_name("calibre_server_runtime_probe.py")


def test_built_image_calibre_authentication_and_process_ownership(
    cwa_container, container_name
):
    remote = "/tmp/cwng_calibre_server_runtime_probe.py"
    subprocess.run(
        ["docker", "cp", str(PROBE), f"{container_name}:{remote}"], check=True
    )
    subprocess.run(
        ["docker", "cp", str(PROBE.with_name("calibre_runtime_probe_children.py")),
         f"{container_name}:/tmp/calibre_runtime_probe_children.py"], check=True
    )
    result = subprocess.run(
        ["docker", "exec", container_name, "cwa-as-abc", "python3", remote],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, (
        f"managed-server probe failed:\n{result.stdout}\n{result.stderr}"
    )
    records = [
        line.split("=", 1)[1]
        for line in result.stdout.splitlines()
        if line.startswith("CWNG_SERVER_RUNTIME=")
    ]
    assert records, result.stdout
    evidence = json.loads(records[-1])
    assert [record["event"] for record in evidence["results"]] == [
        "lifeline-eof",
        "supervisor-kill",
    ]
    for record in evidence["results"]:
        assert record["ready"]
        assert record["actual_launcher_owner_fd"]
        assert record["child_reaped"]
        assert record["owner_released"]
        assert isinstance(record["adopted_helpers_reaped"], int)
    assert evidence["results"][0]["authenticated_client"]
    assert evidence["results"][0]["wrong_password_rejected"]


@pytest.mark.parametrize("root_directory", [False, True])
def test_built_image_root_helpers_leave_service_owned_locks(cwa_container, container_name, root_directory):
    probe = PROBE.with_name("calibre_server_lock_owner_probe.py")
    remote = "/tmp/cwng_calibre_server_lock_owner_probe.py"
    subprocess.run(["docker", "cp", str(probe), f"{container_name}:{remote}"], check=True)
    result = subprocess.run(
        ["docker", "exec", "--user", "0", container_name, "python3", remote,
         *(["--root-directory"] if root_directory else [])],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, f"root/app lock probe failed:\n{result.stdout}\n{result.stderr}"
    records = [line.split("=", 1)[1] for line in result.stdout.splitlines()
               if line.startswith("CWNG_ROOT_LOCK_RUNTIME=")]
    assert records, result.stdout
    assert json.loads(records[-1]) == {
        "root_created_locks": 3,
        "service_owned_locks": 3,
        "app_acquired_all_locks": True,
        "root_owned_directory": root_directory,
    }
