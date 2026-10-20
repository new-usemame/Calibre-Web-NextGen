"""Routine APScheduler job-run lines stay out of the log; failures still show (#2673).

The acquisition import queue runs every 5 seconds, and APScheduler's executor
logs "Running job" and "executed successfully" at INFO on every run. At the
default log level those two lines arrive every 5 seconds forever. At DEBUG an
administrator is diagnosing something, so the per-run lines come back.

Adapted from @sgreadly's tests in #2674.
"""
import logging
import time

import pytest

import cps.logger as cwa_logger

pytestmark = pytest.mark.unit

EXECUTOR = 'apscheduler.executors.default'


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def scheduler(monkeypatch):
    """A real APScheduler behind the app wrapper, plus a sink on the executor logger.

    The sink sits on the executor logger itself, so what it receives depends only
    on that logger's level, not on the root handlers logger.setup() swaps.
    """
    root = logging.root
    saved_handlers, saved_level = list(root.handlers), root.level
    package = logging.getLogger('cps')
    saved_package_level = package.level
    executor = logging.getLogger(EXECUTOR)
    saved_executor_level = executor.level

    from cps.services.background_scheduler import BackgroundScheduler
    # __new__ stores the scheduler, log and lock on the class, so restore all of
    # them or a singleton built earlier would point at the stopped scheduler.
    for attr in ('_instance', 'scheduler', 'log', '_schedule_lock'):
        monkeypatch.setattr(BackgroundScheduler, attr, None, raising=False)
    saved_logger_class = logging.getLoggerClass()
    wrapper = BackgroundScheduler()
    sink = _Collect()
    executor.addHandler(sink)
    try:
        yield wrapper, sink
    finally:
        executor.removeHandler(sink)
        wrapper.scheduler.shutdown(wait=False)
        executor.setLevel(saved_executor_level)
        logging.setLoggerClass(saved_logger_class)
        logging.captureWarnings(False)
        package.setLevel(saved_package_level)
        for h in list(root.handlers):
            if h not in saved_handlers:
                root.removeHandler(h)
                h.close()
        root.handlers = saved_handlers
        root.setLevel(saved_level)


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _run_job(wrapper, func, name):
    done = []

    def job():
        try:
            func()
        finally:
            done.append(1)
    wrapper.scheduler.add_job(job, name=name)
    assert _wait_for(lambda: done)
    time.sleep(0.2)  # the executor logs "executed successfully" after the job returns


def test_successful_runs_are_not_logged_at_the_default_level(scheduler):
    wrapper, sink = scheduler
    cwa_logger.setup(cwa_logger.LOG_TO_STDERR, logging.INFO)
    _run_job(wrapper, lambda: None, 'quiet job')
    assert [r.getMessage() for r in sink.records] == []


def test_failing_run_is_still_logged_at_the_default_level(scheduler):
    wrapper, sink = scheduler
    cwa_logger.setup(cwa_logger.LOG_TO_STDERR, logging.INFO)

    def boom():
        raise RuntimeError('job failed')
    _run_job(wrapper, boom, 'failing job')
    assert _wait_for(lambda: any(r.levelno >= logging.ERROR for r in sink.records))


def test_debug_level_brings_the_per_run_lines_back(scheduler):
    wrapper, sink = scheduler
    # Booting straight into DEBUG leaves the root at Python's WARNING default.
    logging.root.setLevel(logging.WARNING)
    cwa_logger.setup(cwa_logger.LOG_TO_STDERR, logging.DEBUG)
    _run_job(wrapper, lambda: None, 'debug job')
    assert _wait_for(lambda: any('executed successfully' in r.getMessage() for r in sink.records))


def test_a_stricter_admin_level_is_not_loosened(scheduler):
    _, sink = scheduler
    cwa_logger.setup(cwa_logger.LOG_TO_STDERR, logging.ERROR)
    logging.getLogger(EXECUTOR).warning('Run time of job "x" was missed by 0:00:01')
    assert sink.records == []
