# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression test for fork issue #1076: conversion tasks reporting HTML
instead of showing the underlying text.

`ebook-convert`/kepubify failures land in `task.message`/`task.error` as raw
subprocess output, which sometimes contains stray HTML fragments (e.g. a
converter error that embeds part of the source markup). The `/tasks` page
renders `taskMessage` and `error` straight into a bootstrap-table cell with no
client-side escaping, and `render_task_status` already escaped `user` for the
same reason (see the `# prevent xss` comment) but not these two fields - so a
converter message containing `<...>` was interpreted as markup by the browser
instead of shown as text.
"""

from types import SimpleNamespace

from cps import tasks_status
from cps.services.worker import STAT_FAIL


def _task(name="Convert book", message=None, error=None, stat=STAT_FAIL):
    return SimpleNamespace(
        start_time=None,
        runtime=None,
        stat=stat,
        name=name,
        message=message,
        progress=1.0,
        id="task-id",
        is_cancellable=False,
        error=error,
    )


def test_task_message_with_html_is_escaped(monkeypatch):
    user = SimpleNamespace(name="alice", role_admin=lambda: False)
    monkeypatch.setattr(tasks_status, "current_user", user)

    task = _task(message="failed on <b>chapter1.html</b>")
    rendered = tasks_status.render_task_status([(1, "alice", None, task, False)])

    assert rendered[0]["taskMessage"] == "Convert book: failed on &lt;b&gt;chapter1.html&lt;/b&gt;"


def test_task_error_with_html_is_escaped(monkeypatch):
    user = SimpleNamespace(name="alice", role_admin=lambda: False)
    monkeypatch.setattr(tasks_status, "current_user", user)

    task = _task(error="Calibre failed with error: <div>parse error</div>")
    rendered = tasks_status.render_task_status([(1, "alice", None, task, False)])

    assert rendered[0]["error"] == "Calibre failed with error: &lt;div&gt;parse error&lt;/div&gt;"


def test_task_without_error_keeps_none(monkeypatch):
    user = SimpleNamespace(name="alice", role_admin=lambda: False)
    monkeypatch.setattr(tasks_status, "current_user", user)

    task = _task(error=None)
    rendered = tasks_status.render_task_status([(1, "alice", None, task, False)])

    assert rendered[0]["error"] is None
