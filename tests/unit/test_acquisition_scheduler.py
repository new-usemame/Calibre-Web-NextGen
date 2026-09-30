# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from apscheduler.schedulers.background import BackgroundScheduler as APS

from tests.unit.test_acquisition_api import api


@pytest.fixture
def scheduler(api,monkeypatch):
    from cps import schedule
    from cps.services.background_scheduler import BackgroundScheduler
    underlying=APS();underlying.start()
    wrapper=object.__new__(BackgroundScheduler);wrapper.scheduler=underlying
    monkeypatch.setattr(BackgroundScheduler,'_instance',wrapper)
    monkeypatch.setattr(schedule,'config',SimpleNamespace(schedule_start_time=0,schedule_duration=1))
    monkeypatch.setattr(schedule,'reconcile_hardcover_configuration',lambda:(False,None))
    monkeypatch.setattr(schedule,'get_scheduled_tasks',lambda *args:[])
    monkeypatch.setattr(schedule,'_schedule_duplicate_scan',lambda *args:None)
    monkeypatch.setattr(schedule,'_schedule_hardcover_auto_fetch',lambda *args,**kwargs:None)
    monkeypatch.setattr(schedule,'_schedule_archived_book_cleanup',lambda *args:None)
    monkeypatch.setattr(schedule,'should_task_be_running',lambda *args:False)
    yield schedule,wrapper,underlying
    underlying.shutdown(wait=True)


def test_full_reregistration_restores_one_direct_acquisition_callback(api,scheduler,monkeypatch):
    # Bind the module the way the scheduler binds it. `cps/schedule.py`'s
    # `_drain_acquisition_jobs` does `from .services.acquisition.runtime import
    # drain_acquisition_jobs` at call time, which reads
    # sys.modules['cps.services.acquisition.runtime']. `from
    # cps.services.acquisition import runtime` reads the *package attribute*
    # instead, and the two are not always the same object: the attribute is only
    # written when the submodule is first imported, so once another test file
    # re-imports it under a stubbed `cps`, sys.modules holds the newer copy
    # while the attribute still points at the older one. Patching the older copy
    # leaves the scheduler calling the real drain.
    runtime=importlib.import_module('cps.services.acquisition.runtime')
    from cps.services.worker import WorkerThread
    schedule,wrapper,underlying=scheduler
    calls=[]
    # No raising=False: drain_acquisition_jobs is a real attribute of that
    # module, so a miss means we patched the wrong object. Let monkeypatch say
    # that here instead of letting it surface as an unexplained empty `calls`.
    monkeypatch.setattr(runtime,'drain_acquisition_jobs',lambda:calls.append('drain'))
    monkeypatch.setattr(WorkerThread,'add',lambda *args,**kwargs:pytest.fail('download entered serial email worker'))
    for _ in range(3): schedule.register_scheduled_tasks()
    jobs=[job for job in underlying.get_jobs() if job.id==schedule.ACQUISITION_JOB_ID]
    assert len(jobs)==1 and schedule.acquisition_scheduler_available()
    assert jobs[0].max_instances==1 and jobs[0].coalesce
    jobs[0].func();assert calls==['drain']


def test_disabling_or_hybrid_migration_removes_scheduled_downloads(api,scheduler):
    from sqlalchemy import text
    _,repo,*_=api; schedule,wrapper,underlying=scheduler
    schedule.register_acquisition_task();assert schedule.acquisition_scheduler_available()
    with repo.engine.begin() as con:con.execute(text('UPDATE settings SET config_acquisition_enabled=0'))
    schedule.register_acquisition_task();assert not schedule.acquisition_scheduler_available()
    with repo.engine.begin() as con:
        con.execute(text('UPDATE settings SET config_acquisition_enabled=1'))
        con.execute(text("UPDATE acquisition_schema_migration SET status='needs_review'"))
    schedule.register_acquisition_task();assert not schedule.acquisition_scheduler_available()


def test_no_apscheduler_is_explicitly_unavailable_without_creating_one(api,monkeypatch):
    from cps import schedule
    from cps.services.background_scheduler import BackgroundScheduler
    monkeypatch.setattr(schedule,'use_APScheduler',False)
    monkeypatch.setattr(BackgroundScheduler,'_instance',None)
    schedule.register_acquisition_task()
    assert not schedule.acquisition_scheduler_available() and BackgroundScheduler._instance is None


def test_optional_scheduler_module_imports_cleanly_when_dependency_absent(monkeypatch):
    import sys
    from cps.services import background_scheduler
    monkeypatch.setitem(sys.modules,'apscheduler.schedulers.background',None)
    spec=importlib.util.spec_from_file_location('cps.services._acquisition_no_aps_test',background_scheduler.__file__)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert not module.use_APScheduler and module.BackgroundScheduler() is False
    assert module.CronTrigger is None and module.IntervalTrigger is None


def test_paused_scheduler_or_job_is_not_an_available_queue(api,scheduler):
    schedule,wrapper,underlying=scheduler
    schedule.register_acquisition_task();assert schedule.acquisition_scheduler_available()
    underlying.pause();assert not schedule.acquisition_scheduler_available()
    underlying.resume();underlying.pause_job(schedule.ACQUISITION_JOB_ID)
    assert not schedule.acquisition_scheduler_available()
