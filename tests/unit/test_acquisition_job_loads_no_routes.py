# SPDX-License-Identifier: GPL-3.0-or-later
"""The acquisition scheduler job must not load route modules (#2648).

``create_app()`` starts APScheduler and, when book sources are enabled, a
5-second acquisition job, before ``register_blueprints()`` imports the route
modules on the main thread. The job used to import ``cps.api.acquisition`` (and,
through it, ``cps.web`` and ``cps.cwa_functions``) from the scheduler thread.
When that import overlapped the main thread's, the ``api_v1`` blueprint was
registered before the acquisition routes were attached: every
``/api/v1/admin/acquisition`` request answered 404 and every later job run
raised Flask's "setup method 'route' can no longer be called" AssertionError.

The job runs in a fresh interpreter here so the modules it loads are its own.
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

PROBE = textwrap.dedent(r'''
    import json, sys, tempfile
    from pathlib import Path
    import cps
    from cps import ub, schedule
    from cps.services.acquisition import admission

    root = Path(tempfile.mkdtemp())
    ub.app_DB_path = str(root / 'app.db')
    # Enabled instance whose runtime is not ready: the job evaluates every
    # availability precondition, then returns before claiming any work.
    admission.instance_enabled = lambda *_a, **_k: True
    admission.instance_state = lambda *_a, **_k: {'migration_status': 'ready'}
    admission.configured_media_types = lambda *_a, **_k: ('epub',)
    schedule.acquisition_scheduler_available = lambda: True

    before = set(sys.modules)
    schedule._drain_acquisition_jobs()

    from flask import Blueprint
    loaded = []
    for name in sorted(set(sys.modules) - before):
        module = sys.modules.get(name)
        if name.startswith('cps') and module is not None and any(
                isinstance(value, Blueprint) for value in vars(module).values()):
            loaded.append(name)
    print(json.dumps(loaded))
''')


def test_acquisition_job_imports_no_blueprint_module():
    result = subprocess.run([sys.executable, '-c', PROBE], cwd=REPO, capture_output=True,
                            text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-3000:]
    loaded = json.loads(result.stdout.strip().splitlines()[-1])
    assert loaded == [], (
        'the acquisition scheduler job imported route modules from its own thread: %s' % loaded)
