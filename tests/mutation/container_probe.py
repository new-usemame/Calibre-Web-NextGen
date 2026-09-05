# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the leg7 import-hook examples inside Linux containers.

No package installation or network access: copy the installed pure-Python pytest
runtime into the disposable container. These examples demonstrate known
limitations; they are not checks run by the mutation CLI.
"""
import importlib.util
from pathlib import Path


def runtime_overlay():
    files = {}
    for name in ('pytest', '_pytest', 'pluggy', 'iniconfig', 'packaging', 'pygments', 'py'):
        spec = importlib.util.find_spec(name)
        if not spec.submodule_search_locations:
            files['_runtime/' + name + '.py'] = Path(spec.origin).read_bytes()
            continue
        root = Path(spec.origin).parent
        for source in root.rglob('*.py'):
            files['_runtime/' + name + '/' + source.relative_to(root).as_posix()] = source.read_bytes()
    files['_phase_evidence.py'] = Path(__file__).with_name('pytest_evidence.py').read_bytes()
    files['victim.py'] = b'VALUE = 2\n'
    return files


ENVIRONMENT = {
    'PYTHONPATH': '/work/_runtime:/work',
    'PYTHONDONTWRITEBYTECODE': '1',
    'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
    'PYTEST_ADDOPTS': '',
    'LOAD_WITNESS': '/out/loaded-hash',
    'CWNG_PYTEST_EVIDENCE': '/out/report.json',
    'CWNG_MEASURED_TARGET': '/work/victim.py',
    'CWNG_MEASURED_ROOT': '/work',
}

COMMAND = ['python', '-c',
    "import runpy,sys; sys.argv=['_phase_evidence.py', '-q', '-o', 'addopts=', "
    "'-p', 'no:cacheprovider', '--color=no', 'test_probe.py']; "
    "runpy.run_path('/work/_phase_evidence.py', run_name='__main__')"]
