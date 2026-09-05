"""Keep production globals and live runtime threads out of the collecting process."""
import os
import json
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real_app_wire():
    root = Path(__file__).resolve().parents[2]
    suite = Path(__file__).with_name("real_app")
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(suite / "cases.py"),
         "--confcutdir", str(suite), "-v", "-s", "--tb=short"],
        cwd=root, env=env, text=True, capture_output=True, timeout=90,
    )
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr

    prefix = "REAL_APP_WIRE="
    return json.loads(next(line[len(prefix):] for line in result.stdout.splitlines()
                           if line.startswith(prefix)))


def test_real_application_in_fresh_process(real_app_wire):
    assert real_app_wire


def test_blueprint_wire_matches_docker(real_app_wire, request):
    # A CLI on PATH does not mean the daemon is usable. Probe before requesting
    # the existing lane fixture, which starts the lane's disposable container.
    try:
        probe = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("Docker daemon unavailable")
    if probe.returncode:
        pytest.skip("Docker daemon unavailable")
    request.getfixturevalue("cwa_container")
    import requests

    base = "http://localhost:" + os.environ.get("CWA_TEST_PORT", "8085")
    for row in real_app_wire:
        response = requests.request(row["method"], base + row["path"],
                                    allow_redirects=False, timeout=15)
        actual = {
            "status": response.status_code,
            "mimetype": response.headers.get("Content-Type", "").split(";")[0],
            "location": response.headers.get("Location"),
            "json": response.json() if "application/json" in
                    response.headers.get("Content-Type", "") else None,
        }
        expected = {key: row[key] for key in actual}
        assert actual == expected, (row["blueprint"], row["path"], actual, expected)
