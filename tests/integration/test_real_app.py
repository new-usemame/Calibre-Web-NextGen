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
    for row, reason in excluded:
        print("DOCKER PARITY: not compared -- %s (%s)" % (row["path"], reason))
    print("DOCKER PARITY: comparing %d of %d rows" % (len(compared), len(real_app_wire)))
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
        # tests/conftest.py:938 records why /login is not a readiness signal: it can
        # answer while the configured database and critical services are still
        # unavailable. Comparing a byte-exact wire against a half-started app would
        # produce differences that are timing, not behaviour. Use the same endpoint
        # and the same budget as the shared Docker fixture.
        budget = int(os.environ.get("CWA_TEST_START_TIMEOUT", "600"))
        deadline = time.monotonic() + budget
        while True:
            try:
                if requests.get(base + "/health", timeout=2).status_code == 200:
                    break
            except requests.RequestException:
                pass
            if time.monotonic() >= deadline:
                pytest.fail(
                    "Disposable Docker application did not become ready in %ds "
                    "(raise CWA_TEST_START_TIMEOUT on a slow runner)" % budget)
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

# The exclusions below are each justified, but an exclusion rule that can grow
# without limit is a comparison that can quietly stop comparing: flag every row
# and the loop runs zero times while the test still passes. Two exclusions are
# expected today -- the bundle row and one address-sensitive route. A third is
# tolerated for churn; beyond that, look at why coverage is draining rather than
# raising this number. In the CI shape -- bundle absent, so the SPA row and the
# one address-sensitive route are both excluded -- the real wire excludes 2 of
# 33, so the headroom here is exactly ONE. A second address-sensitive route
# turns the gating lane red. That is the intended direction, but it is a live
# tripwire rather than slack: read the message, do not raise the number.
MAX_EXCLUDED_ROWS = 3


def _backend_parity_rows(wire, *, bundle_built):
    """Split the probe wire into rows Docker parity can claim, and rows it cannot.

    Returns ``(compared, excluded)``, where each excluded row carries the reason
    it cannot be compared. Two things are not the application and must not be
    reported as parity failures:

    * The Vite bundle under ``cps/static/app/``, which is gitignored and which no
      lane builds before this job. Without it the checkout answers 404 where the
      image serves the compiled shell -- a difference in the frontend build.
    * A route whose answer depends on the client's source address. Werkzeug's
      test client presents ``REMOTE_ADDR`` 127.0.0.1; a request through a
      container's published port arrives from the bridge gateway. ``cases.py``
      asks each route directly whether it answers a stranger differently, so this
      set is measured rather than maintained by hand.

    Excluding silently would let coverage drain away unnoticed, so a bundle-served
    row must belong to a blueprint this module claims, and every other exclusion
    must carry the measured flag that justifies it.
    """
    compared, excluded = [], []
    for row in wire:
        if row.get("client_address_sensitive"):
            excluded.append((row, "answers a non-loopback client differently"))
        elif not bundle_built and row["path"].startswith("/app"):
            excluded.append((row, "SPA bundle absent from the checkout"))
        else:
            compared.append(row)
    unclaimed = [row for row, reason in excluded
                 if reason == "SPA bundle absent from the checkout"
                 and row["blueprint"] not in BUNDLE_SERVED_BLUEPRINTS]
    assert not unclaimed, unclaimed
    assert len(excluded) <= MAX_EXCLUDED_ROWS, (
        "%d of %d rows excluded from the Docker comparison; the comparison is "
        "draining. Excluded: %s" % (len(excluded), len(wire),
                                    [(row["path"], reason) for row, reason in excluded]))
    assert compared, "every row was excluded; nothing would be compared"
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
