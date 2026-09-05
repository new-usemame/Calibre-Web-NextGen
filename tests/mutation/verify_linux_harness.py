# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the harness unit selector in Linux without a Docker CLI inside the container.

Uses the local python:3.12 image and copies the host's pure-Python pytest runtime.
Application conftest fixtures are excluded: these harness tests are self-contained.
Invoke this file with the project's venv Python. No image is pulled.
"""
from pathlib import Path
import tempfile

from container_backend import ContainerSweep, default_scratch_root
from pytest_runtime import runtime_overlay


def main():
    repo = Path(__file__).resolve().parents[2]
    files = runtime_overlay()
    for path in (repo / 'tests/mutation').glob('*.py'):
        files[path.relative_to(repo).as_posix()] = path.read_bytes()
    test_path = 'tests/unit/test_mutation_harness.py'
    files[test_path] = (repo / test_path).read_bytes()
    command = """
import shutil, sys, pytest
assert sys.platform == 'linux'
assert shutil.which('docker') is None
print('OBSERVED: native Linux; Docker CLI absent', flush=True)
sys.exit(pytest.main(['tests/unit/test_mutation_harness.py', '-m', 'smoke or unit',
    '--maxfail=3', '--noconftest', '-q', '-o', 'addopts=', '--tb=short']))
"""
    sweep = ContainerSweep(repo, 'HEAD')
    with tempfile.TemporaryDirectory(prefix='mutation-linux-check-', dir=default_scratch_root()) as directory:
        result = sweep.run_phase(['python', '-c', command], output=Path(directory),
                                 files=files, timeout=180,
                                 environment={'PYTHONPATH': '/work/_runtime',
                                              'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})
    print(result.stdout, end='')
    print(result.stderr, end='')
    return 2 if result.timed_out else result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
