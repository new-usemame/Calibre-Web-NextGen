"""Independent review regressions, separate from the writer's fixed tests."""
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("suite", ["foreign_highlight_retries_test.lua", "foreign_highlight_downgrade_test.lua"])
def test_foreign_highlight_retry_and_native_collision(suite):
    lua = shutil.which("lua")
    assert lua, "Lua is required for the plugin contract"
    directory = Path(__file__).resolve().parents[2] / "koreader/plugins/cwngsync.koplugin/tests"
    result = subprocess.run([lua, suite], cwd=directory,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
