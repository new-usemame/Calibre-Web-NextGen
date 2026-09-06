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


def test_backend_blueprint_wire_matches_docker(real_app_wire):
    # A CLI on PATH does not mean the daemon is usable. Probe before requesting
    # a uniquely owned disposable container.
    try:
        probe = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("Docker daemon unavailable")
    if probe.returncode:
        pytest.skip("Docker daemon unavailable")
    import time
    import requests

    image = os.environ.get("CWA_TEST_IMAGE", "crocodilestick/calibre-web-automated:dev")
    inspect = subprocess.run(["docker", "image", "inspect", image],
                             capture_output=True, timeout=10)
    if inspect.returncode:
        pytest.skip("Build the integration image first (CWA_TEST_IMAGE)")
    import inspect as source_inspect

    root = Path(__file__).resolve().parents[2]
    script = source_inspect.getsource(_source_digest) + "\nimport sys; print(_source_digest(sys.argv[1]))"
    digest = subprocess.run(
        ["docker", "run", "--rm", "--network=none", "--entrypoint", "python3", image,
         "-c", script, "/app/calibre-web-automated"],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout.strip()
    if digest != _source_digest(root):
        message = "Docker image backend differs from the tested source; rebuild CWA_TEST_IMAGE"
        if "CWA_TEST_IMAGE" in os.environ:
            pytest.fail(message)
        pytest.skip(message)
    # The Vite bundle under cps/static/app/ is gitignored and no lane builds it
    # before this job, so a fresh checkout answers /app/ with 404 while the image
    # serves the compiled shell. That difference is the frontend build, not the
    # backend wire this fixture claims parity for -- asserting the bundle exists
    # would redden the integration lane on every run and prove nothing about the
    # application. Compare the bundle-served rows when the checkout actually has
    # the bundle; otherwise exclude them and hold the exclusion to exactly the
    # routes the bundle governs, so a future blueprint cannot join it silently.
    compared, excluded = _backend_parity_rows(
        real_app_wire, bundle_built=(root / "cps/static/app/index.html").is_file(),
    )
    if excluded:
        print("DOCKER PARITY: backend rows only; SPA bundle absent, excluded "
              + ", ".join(row["path"] for row in excluded))
    created = subprocess.run(
        ["docker", "run", "--detach", "--pull=never",
         "--publish", "127.0.0.1::8083", "--env", "NETWORK_SHARE_MODE=false",
         "--volume", "/config", "--volume", "/calibre-library",
         "--volume", "/cwa-book-ingest", image],
        capture_output=True, text=True, timeout=30, check=True,
    )
    container = created.stdout.strip()
    try:
        port = subprocess.run(["docker", "port", container, "8083/tcp"],
                              capture_output=True, text=True, timeout=10, check=True)
        base = "http://" + port.stdout.strip()
        deadline = time.monotonic() + 120
        while True:
            try:
                ready = requests.get(base + "/login", timeout=2)
                if ready.status_code == 200 and "csrf_token" in ready.text:
                    break
            except requests.RequestException:
                pass
            if time.monotonic() >= deadline:
                pytest.fail("Disposable Docker application did not become ready in 120s")
            time.sleep(0.5)
        for row in compared:
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
    finally:
        subprocess.run(["docker", "rm", "--force", "--volumes", container],
                       capture_output=True, timeout=30, check=True)


BUNDLE_SERVED_BLUEPRINTS = ("spa",)


def _backend_parity_rows(wire, *, bundle_built):
    """Split the probe wire into rows Docker parity can claim, and rows it cannot.

    Returns ``(compared, excluded)``. With the Vite bundle present both the
    checkout and the image serve the compiled shell, so nothing is excluded.
    Without it the checkout answers 404 where the image answers 200, and that
    gap is the frontend build rather than the backend. Excluding it silently
    would let a future bundle-served blueprint drop out of the comparison
    unnoticed, so the exclusion is held to the blueprints the bundle governs.
    """
    if bundle_built:
        return list(wire), []
    excluded = [row for row in wire if row["path"].startswith("/app")]
    unclaimed = [row for row in excluded
                 if row["blueprint"] not in BUNDLE_SERVED_BLUEPRINTS]
    assert not unclaimed, unclaimed
    compared = [row for row in wire if not row["path"].startswith("/app")]
    return compared, excluded


def _source_digest(root):
    """Compare actual source, without depending on optional image labels or git history."""
    from pathlib import Path
    import hashlib

    root = Path(root)
    digest = hashlib.sha256()
    paths = list((root / "cps").rglob("*.py"))
    paths += list((root / "scripts").rglob("*.py"))
    paths += list((root / "scripts").glob("*.sql"))
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()
