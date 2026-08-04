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


# A stand-in for what collect_stats() really reports: the release, the Python
# build, the host kernel build string and dependency versions. #1287 is that
# this map fingerprints the host for anyone who can reach /api/v1/about.
_SENSITIVE = {
    "Calibre-Web NextGen": "v4.1.29",
    "Python": "3.11.9 (main, Apr 10 2026, 12:00:00) [GCC 12.2.0]",
    "Platform": "Linux 6.8.0-51-generic #52-Ubuntu SMP x86_64 x86_64",
    "SQLite": "3.40.1",
}


def _about_payload(is_admin):
    """Call about_info() as an admin / non-admin with the DB + stats stubbed."""
    from cps.api import info as mod
    calibre = MagicMock()
    calibre.session.query.return_value.count.return_value = 7
    with _ctx("/api/v1/about", method="GET"):
        with patch.object(mod, "calibre_db", calibre), \
             patch.object(mod, "collect_stats", lambda: dict(_SENSITIVE)), \
             patch.object(mod, "current_user", SimpleNamespace(role_admin=lambda: is_admin)):
            resp = inspect.unwrap(mod.about_info)()
    return json.loads(resp.get_data(as_text=True))


@pytest.mark.unit
def test_about_withholds_versions_from_non_admin():
    """#1287: the version map is admin-only. Red before the fix -- the endpoint
    returned collect_stats() to every caller the decorator let through."""
    assert _about_payload(is_admin=False)["versions"] == {}


@pytest.mark.unit
def test_about_leaks_no_version_string_anywhere_to_non_admin():
    """Stronger than checking one key: no sensitive value may appear anywhere in
    a non-admin response, so moving the map under another key still fails."""
    blob = json.dumps(_about_payload(is_admin=False))
    for value in _SENSITIVE.values():
        assert value not in blob


@pytest.mark.unit
def test_about_still_gives_versions_to_admin():
    """The gate must not cost admins the page they actually use."""
    assert _about_payload(is_admin=True)["versions"] == _SENSITIVE


@pytest.mark.unit
def test_about_keeps_counts_for_non_admin():
    """Library counts are not sensitive -- only versions are withheld."""
    counts = _about_payload(is_admin=False)["counts"]
    assert counts == {"books": 7, "authors": 7, "categories": 7, "series": 7}


@pytest.mark.unit
def test_about_always_returns_a_versions_key():
    """AboutInfo.versions is non-optional in the SPA's types and the page maps
    over it, so the key must survive the gate rather than disappear."""
    for is_admin in (True, False):
        assert "versions" in _about_payload(is_admin=is_admin)


@pytest.mark.unit
def test_about_gate_is_server_side():
    """Pin the check in the handler: a client-only conditional would still ship
    the map over the wire, which is what #1287 is about."""
    src = inspect.getsource(__import__("cps.api.info", fromlist=["about_info"]).about_info)
    assert "role_admin" in src


@pytest.mark.unit
def test_anonymous_user_is_never_admin():
    """/about is login_required_if_no_ano, so guests reach the handler when
    anonymous browsing is on. role_admin() must answer False for them, not
    raise -- otherwise the gate 500s the page instead of hiding the versions."""
    from cps import ub
    assert ub.Anonymous.role_admin(None) is False


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
