# SPDX-License-Identifier: GPL-3.0-or-later
"""Copy the host's pure-Python pytest runtime for a disposable Linux phase."""
import importlib.util
from pathlib import Path


def runtime_overlay():
    files = {}
    for name in ('pytest', '_pytest', 'pluggy', 'iniconfig', 'packaging', 'pygments', 'py'):
        spec = importlib.util.find_spec(name)
        # Newer pytest versions do not need the optional legacy py shim.
        if spec is None:
            if name == 'py':
                continue
            raise RuntimeError('Install pytest in the interpreter running the mutation CLI.')
        if not spec.submodule_search_locations:
            files['_runtime/' + name + '.py'] = Path(spec.origin).read_bytes()
            continue
        root = Path(spec.origin).parent
        for source in root.rglob('*.py'):
            files['_runtime/' + name + '/' + source.relative_to(root).as_posix()] = source.read_bytes()
    files['_phase_evidence.py'] = Path(__file__).with_name('pytest_evidence.py').read_bytes()
    return files
