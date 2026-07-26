# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the info endpoints (cps/api/info.py): about + task queue.
Verified live in the container; these pin wiring + the cancel ownership guard."""
import inspect
import json
import flask
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock


def _ctx(path, method="POST"):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_request_context(path, method=method)


@pytest.mark.unit
def test_about_reuses_collect_stats():
    src = inspect.getsource(__import__("cps.api.info", fromlist=["about_info"]).about_info)
    assert "collect_stats" in src
    assert "counts" in src


@pytest.mark.unit
def test_tasks_uses_render_task_status():
    src = inspect.getsource(__import__("cps.api.info", fromlist=["tasks_list"]).tasks_list)
    assert "render_task_status" in src


@pytest.mark.unit
def test_cancel_task_not_found_404():
    from cps.api import info as mod
    worker = SimpleNamespace(tasks=[])
    with _ctx("/api/v1/tasks/9/cancel"):
        with patch.object(mod.WorkerThread, "get_instance", staticmethod(lambda: worker)), \
             patch.object(mod, "current_user", SimpleNamespace(name="x", role_admin=lambda: False)):
            resp = inspect.unwrap(mod.cancel_task_api)("9")
    assert resp[1] == 404


@pytest.mark.unit
def test_cancel_task_forbidden_for_other_users_task():
    from cps.api import info as mod
    other_task = SimpleNamespace(id=9)
    # tasklist row shape: (num, user, added, task, hidden)
    worker = MagicMock()
    worker.tasks = [(0, "someone_else", 0, other_task, 0)]
    with _ctx("/api/v1/tasks/9/cancel"):
        with patch.object(mod.WorkerThread, "get_instance", staticmethod(lambda: worker)), \
             patch.object(mod, "current_user", SimpleNamespace(name="alice", role_admin=lambda: False)):
            resp = inspect.unwrap(mod.cancel_task_api)("9")
    assert resp[1] == 403
    worker.end_task.assert_not_called()


@pytest.mark.unit
def test_cancel_task_owner_ends_task():
    from cps.api import info as mod
    my_task = SimpleNamespace(id=9)
    worker = MagicMock()
    worker.tasks = [(0, "alice", 0, my_task, 0)]
    with _ctx("/api/v1/tasks/9/cancel"):
        with patch.object(mod.WorkerThread, "get_instance", staticmethod(lambda: worker)), \
             patch.object(mod, "current_user", SimpleNamespace(name="alice", role_admin=lambda: False)):
            resp = inspect.unwrap(mod.cancel_task_api)("9")
    assert resp[1] == 204
    worker.end_task.assert_called_once_with(9)
