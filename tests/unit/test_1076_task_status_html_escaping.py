# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression test for fork issue #1076: conversion tasks reporting HTML
instead of showing the underlying text.

`ebook-convert`/kepubify failures land in `task.error` as raw subprocess
output, which sometimes contains stray HTML fragments (e.g. a converter error
that embeds part of the source markup). The `/tasks` page renders `error`
straight into a bootstrap-table cell with no client-side escaping, so it has to
be escaped server-side the same way `user` already is.

`task.message` is different: some tasks build it as HTML on purpose (e.g.
"File format EPUB added to <a href=...>Title</a>" from editbooks), with the
dynamic parts escaped where the message is built, so it must reach the table
unchanged.
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


def test_task_message_link_is_kept(monkeypatch):
    user = SimpleNamespace(name="alice", role_admin=lambda: False)
    monkeypatch.setattr(tasks_status, "current_user", user)

    # Same shape editbooks queues when a format is added to an existing book;
    # the title inside the link is already escaped there.
    message = 'File format EPUB added to <a href="/book/5">Tom &amp; Jerry</a>'
    task = _task(name="Upload", message=message)
    rendered = tasks_status.render_task_status([(1, "alice", None, task, False)])

    assert rendered[0]["taskMessage"] == "Upload: " + message


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


def test_registration_task_escapes_self_chosen_username(monkeypatch):
    # With public registration on, a stranger picks this name; the admin then
    # sees the queued "Registration Email for user: ..." row on /tasks, which
    # renders taskMessage as HTML.
    from flask import Flask
    from flask_babel import Babel
    from cps import helper

    queued = []
    monkeypatch.setattr(helper.WorkerThread, "add", staticmethod(lambda user, task, **kw: queued.append(task)))
    monkeypatch.setattr(helper.config, "get_mail_settings", lambda: {})

    helper.send_registration_mail("new@example.com", '<img src=x onerror="alert(1)">', "pw")

    app = Flask(__name__)
    Babel(app)
    with app.app_context():
        message = str(queued[0].message)
    assert "<img" not in message
    assert "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;" in message


def test_lazy_converter_error_is_escaped_on_the_wire(monkeypatch):
    # convert.py builds the error with N_(), a flask_babel LazyString. It has
    # __html__, so markupsafe would pass it through as trusted markup.
    from flask import Flask
    from flask_babel import Babel, lazy_gettext as N_

    user = SimpleNamespace(name="alice", role_admin=lambda: False)
    monkeypatch.setattr(tasks_status, "current_user", user)

    app = Flask(__name__)
    Babel(app)
    with app.app_context():
        task = _task(error=N_("Calibre failed with error: %(error)s", error="<img src=x onerror=alert(1)>"))
        rendered = tasks_status.render_task_status([(1, "alice", None, task, False)])
        wire = app.json.dumps(rendered)

    assert "<img" not in wire
    assert "Calibre failed with error: &lt;img src=x onerror=alert(1)&gt;" in wire
