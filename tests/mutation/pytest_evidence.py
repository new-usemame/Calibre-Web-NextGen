# SPDX-License-Identifier: GPL-3.0-or-later
"""Record pytest's selected items and phase reports without their private values."""
import json
import os
from pathlib import Path
import sys

import pytest


class TargetWitness:
    """Observe Python code actually called by measured pytest, without importing it."""
    def __init__(self, target):
        self.target = Path(target).resolve()
        self.relative = self.target.relative_to(Path(os.environ["CWNG_MEASURED_ROOT"]).resolve()).parts
        self.seen = False
        self.foreign = False

    def profile(self, frame, event, arg):
        if event != 'call':
            return
        filename = frame.f_code.co_filename
        if tuple(filename.split('/')[-len(self.relative):]) != self.relative:
            return
        if Path(filename).resolve() == self.target:
            self.seen = True
        else:
            # Conservatively reject matching relative module paths from any other location.
            self.foreign = True


class Evidence:
    def __init__(self):
        target = os.environ.get('CWNG_MEASURED_TARGET')
        self.witness = TargetWitness(target) if target else None
        self.profiler = self.witness.profile if self.witness else None
        if self.profiler:
            sys.setprofile(self.profiler)
        self.data = {'version': 1, 'complete': False, 'selected': [], 'selected_count': 0,
                     'deselected': [], 'collection_errors': [], 'reports': []}

    def pytest_deselected(self, items):
        self.data['deselected'].extend(item.nodeid for item in items)

    def pytest_collection_finish(self, session):
        self.data['selected'] = [item.nodeid for item in session.items]
        self.data['selected_count'] = len(session.items)

    def pytest_collectreport(self, report):
        if report.failed:
            self.data['collection_errors'].append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        self.data['reports'].append({'nodeid': report.nodeid, 'when': report.when,
                                     'outcome': report.outcome, 'wasxfail': hasattr(report, 'wasxfail')})

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session, exitstatus):
        yield
        if self.witness:
            self.data['target_provenance'] = {
                'seen': self.witness.seen, 'foreign': self.witness.foreign,
                'active': sys.getprofile() is self.profiler}
            sys.setprofile(None)
        self.data.update(complete=True, exitstatus=int(session.exitstatus))
        Path(os.environ['CWNG_PYTEST_EVIDENCE']).write_text(json.dumps(self.data, sort_keys=True))


if __name__ == '__main__':
    raise SystemExit(pytest.main(sys.argv[1:], plugins=[Evidence()]))
