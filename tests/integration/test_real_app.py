"""Keep production globals and live runtime threads out of the collecting process."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


def test_real_application_in_fresh_process():
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
