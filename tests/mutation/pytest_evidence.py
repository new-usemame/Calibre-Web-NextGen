# SPDX-License-Identifier: GPL-3.0-or-later
"""Record pytest's selected items and phase reports without their private values."""
import json
import os
from pathlib import Path
import sys

import pytest


class Evidence:
    def __init__(self):
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
        self.data.update(complete=True, exitstatus=int(session.exitstatus))
        Path(os.environ['CWNG_PYTEST_EVIDENCE']).write_text(json.dumps(self.data, sort_keys=True))


if __name__ == '__main__':
    raise SystemExit(pytest.main(sys.argv[1:], plugins=[Evidence()]))
