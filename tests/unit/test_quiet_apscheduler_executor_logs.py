"""Routine APScheduler job-run lines stay out of the log; failures still show."""
import logging
import time

import pytest

pytestmark = pytest.mark.unit

EXECUTOR = 'apscheduler.executors.default'


@pytest.fixture
def wrapper(monkeypatch):
    from cps.services.background_scheduler import BackgroundScheduler
    monkeypatch.setattr(BackgroundScheduler, '_instance', None)
    instance = BackgroundScheduler()
    yield instance
    instance.scheduler.shutdown(wait=False)
    logging.getLogger(EXECUTOR).setLevel(logging.NOTSET)


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_successful_job_runs_are_not_logged_at_info(wrapper, caplog):
    ran = []
    caplog.set_level(logging.INFO)
    wrapper.scheduler.add_job(lambda: ran.append(1), name='quiet job')
    assert _wait_for(lambda: ran)
    time.sleep(0.2)  # let the executor emit its "executed successfully" line
    assert [r for r in caplog.records if r.name == EXECUTOR] == []


def test_failing_job_is_still_logged(wrapper, caplog):
    def boom():
        raise RuntimeError('job failed')
    caplog.set_level(logging.INFO)
    wrapper.scheduler.add_job(boom, name='failing job')
    assert _wait_for(lambda: any(r.name == EXECUTOR and r.levelno >= logging.ERROR for r in caplog.records))
