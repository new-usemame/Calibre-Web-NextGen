# SPDX-License-Identifier: GPL-3.0-or-later
"""Witness cps package roots only; measured-target checks live in pytest_evidence."""
import importlib
import json
import os
from pathlib import Path
import runpy
import sys


def witness(shape, include_main=False):
    root = Path(os.environ['CWNG_PROVENANCE_ROOT']).resolve(strict=True)
    cps = importlib.import_module('cps')
    paths = [cps.__file__, cps.__spec__.origin, *cps.__path__]
    if include_main:
        paths.append(sys.modules['cps.main'].__file__)
    resolved = [Path(value).resolve(strict=True) for value in paths]
    inside = all(path.is_relative_to(root) for path in resolved)
    record = {'shape': shape, 'inside': inside,
              'paths': [path.relative_to(root).as_posix() for path in resolved] if inside else []}
    print('CWNG_PROVENANCE ' + json.dumps(record, sort_keys=True), flush=True)
    return inside


class PytestProbe:
    def pytest_collection_finish(self, session):
        if not witness('pytest'):
            import pytest
            pytest.exit('provenance REJECTED: cps resolved outside disposable root', returncode=86)


def main():
    shape = sys.argv[1]
    if shape == 'pytest':
        import pytest
        return pytest.main(['--collect-only', '-q', '-o', 'addopts=', '-p', 'no:cacheprovider',
                            '--tb=no', *json.loads(os.environ['CWNG_PROVENANCE_TARGETS'])],
                           plugins=[PytestProbe()])
    if shape == 'child':
        return 0 if witness(shape) else 86
    if shape == 'console':
        console = Path(os.environ['CWNG_PROVENANCE_CONSOLE'])
        # Match the script-directory search path of the installed launcher. The
        # launcher itself executes unchanged; stop before its application body.
        sys.path[0] = str(console.parent)
        sys.argv = [str(console)]
        def before_main(frame, event, arg):
            if (event == 'call' and frame.f_globals.get('__name__') == 'cps.main'
                    and frame.f_code.co_name == 'main'):
                sys.setprofile(None)
                raise SystemExit(0 if witness(shape, include_main=True) else 86)
        sys.setprofile(before_main)
        try:
            runpy.run_path(str(console), run_name='__main__')
        finally:
            sys.setprofile(None)
        return 87  # a launcher that never called the expected entry point
    return 87


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('PROVENANCE_FAILURE ' + type(exc).__name__, flush=True)
        raise SystemExit(87)
