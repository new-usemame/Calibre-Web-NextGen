"""Regression tests for background worker task cleanup."""

import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import pytest

from cps.services.worker import (
    CalibreTask, ImprovedQueue, QueuedTask, STAT_CANCELLED, TASK_CLEANUP_TRIGGER, WorkerThread,
)

pytestmark = pytest.mark.unit


class WorkerCleanupTest(TestCase):
    def test_cancelled_task_without_end_time_does_not_crash_cleanup(self):
        worker = object.__new__(WorkerThread)
        worker.doLock = threading.Lock()
        worker.queue = ImprovedQueue()
        now = datetime.now()
        worker.dequeued = [
            QueuedTask(index, "test", now, SimpleNamespace(
                id=index, dead=True, end_time=now + timedelta(seconds=index)
            ), False)
            for index in range(TASK_CLEANUP_TRIGGER + 1)
        ]
        cancelled = CalibreTask("waiting")
        cancelled.is_cancellable = True
        worker.queue.put(QueuedTask(999, "test", now, cancelled, False))
        with patch.object(WorkerThread, "get_instance", return_value=worker):
            worker.end_task(cancelled.id)
        self.assertEqual(STAT_CANCELLED, cancelled.stat)
        self.assertIsNone(cancelled.end_time)
        worker.dequeued.append(worker.queue.get_nowait())

        worker.cleanup_tasks()

        self.assertEqual(TASK_CLEANUP_TRIGGER, len(worker.dequeued))
        self.assertTrue(all(item.task is not cancelled for item in worker.dequeued))
