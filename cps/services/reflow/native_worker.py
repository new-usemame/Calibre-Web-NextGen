"""Fresh interpreter entry point: no web application, database or credentials."""
import importlib
import importlib.util
import os
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parent
    name = '_reflow_native_runtime'
    spec = importlib.util.spec_from_file_location(name, root / '__init__.py',
                                                 submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module; spec.loader.exec_module(module)
    runtime = importlib.import_module(name + '.native_worker_runtime')
    with os.fdopen(int(sys.argv[3]), 'w', buffering=1) as control:
        return runtime.serve(int(sys.argv[1]), sys.argv[2], control)


if __name__ == '__main__':
    raise SystemExit(main())
