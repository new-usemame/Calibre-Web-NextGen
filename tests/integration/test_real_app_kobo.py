"""Keep production globals and live runtime threads out of the collecting process.

The Kobo cases need an application booted with ``config_kobo_sync`` on, because
``cps/main.py:124`` registers the reading-services blueprints only when that
switch is true at registration time. ``cps`` owns process state and refuses a
second boot, so each case module runs in its own fresh interpreter exactly the
way test_real_app.py runs the shared fixture's cases.
"""
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration

# The count is pinned per module rather than merely "greater than zero". This
# slice exists because six earlier attempts to observe the sticky Kobo state
# reported a result that had asserted nothing: a module that collects fewer
# cases, or skips one, still exits 0. An expected count turns that into a
# failure. Update it in the same commit as any added or removed case.
EXPECTED_PASSES = {
    "kobo_authority_cases.py": 4,
    "kobo_containment_cases.py": 1,
}

_OUTCOME = re.compile(r"(\d+) (passed|failed|skipped|error|errors|xfailed|xpassed)")


def _outcomes(stdout):
    """Parse pytest's own final summary line into a counted mapping."""
    summary = [line for line in stdout.splitlines()
               if re.search(r"=+ .*(passed|failed|error|no tests ran).* =+", line)]
    assert summary, "the case run printed no pytest summary line"
    counts = {}
    for number, name in _OUTCOME.findall(summary[-1]):
        counts[name.rstrip("s")] = int(number)
    return counts


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
    return result.stdout


@pytest.mark.parametrize("module_name", sorted(EXPECTED_PASSES))
def test_kobo_cases_in_fresh_process(module_name, record_property):
    counts = _outcomes(_run_cases(module_name))
    record_property("kobo_case_outcomes", "%s %r" % (module_name, counts))
    assert counts == {"passed": EXPECTED_PASSES[module_name]}, (
        "%s reported %r; anything other than exactly %d passed -- a skip, an "
        "error, a case that stopped being collected -- means the observation "
        "this slice exists for did not happen"
        % (module_name, counts, EXPECTED_PASSES[module_name]))
