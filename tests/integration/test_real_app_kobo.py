"""Keep production globals and live runtime threads out of the collecting process.

The Kobo cases need an application booted with ``config_kobo_sync`` on, because
``cps/main.py:124`` registers the reading-services blueprints only when that
switch is true at registration time. ``cps`` owns process state and refuses a
second boot, so each case module runs in its own fresh interpreter exactly the
way test_real_app.py runs the shared fixture's cases.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration

CASE_MODULES = ("kobo_authority_cases.py", "kobo_containment_cases.py")


def _run_cases(module_name):
    root = Path(__file__).resolve().parents[2]
    suite = Path(__file__).with_name("real_app")
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1")
    budget = int(os.environ.get("CWA_TEST_KOBO_CASE_TIMEOUT", "300"))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(suite / module_name),
         "--confcutdir", str(suite), "-v", "-s", "--tb=long", "-rs"],
        cwd=root, env=env, text=True, capture_output=True, timeout=budget,
    )
    print(result.stdout)
    print(result.stderr[-4000:])
    assert result.returncode == 0, result.stdout + result.stderr
    # A case module that collected nothing exits 5, not 0, but a module whose
    # cases were all skipped exits 0 with nothing observed. This slice exists
    # because six earlier attempts reported a result that had asserted nothing,
    # so an empty or skipped run is a failure here rather than a pass.
    assert " skipped" not in result.stdout.splitlines()[-1], result.stdout
    return result.stdout


@pytest.mark.parametrize("module_name", CASE_MODULES)
def test_kobo_authority_cases_in_fresh_process(module_name, record_property):
    stdout = _run_cases(module_name)
    passed = [line for line in stdout.splitlines() if " PASSED" in line]
    record_property("kobo_cases_passed", len(passed))
    assert passed, stdout
