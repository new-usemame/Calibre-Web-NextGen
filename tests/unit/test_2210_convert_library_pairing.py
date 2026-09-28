# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert Library pauses the content server and must always give it back.

Review of #2210: the stop/restart pairing restarted only from a thread that
waited on the launched run, so a run that failed to launch left the server
stopped until the next settings save or container restart, and a server that
was never running was started afterwards anyway.
"""
import queue

import pytest

from cps import cwa_functions

pytestmark = pytest.mark.unit


class _Server:
    def __init__(self, running):
        self.running = running
        self.starts = 0

    def pause(self):
        was, self.running = self.running, False
        return was

    def start(self):
        self.starts += 1
        self.running = True


@pytest.fixture
def server(monkeypatch):
    def install(running):
        fake = _Server(running)
        monkeypatch.setattr(cwa_functions, "content_server", fake)
        return fake
    return install


def _no_threads(monkeypatch):
    started = []
    monkeypatch.setattr(cwa_functions, "Thread",
                        lambda target, args, daemon=None: type("T", (), {
                            "start": lambda self: started.append((target, args))})())
    return started


def test_a_run_that_cannot_launch_gives_the_server_back(server, monkeypatch):
    fake = server(running=True)
    _no_threads(monkeypatch)

    def fail(*_a, **_k):
        raise OSError("python3 not found")
    monkeypatch.setattr(cwa_functions.subprocess, "Popen", fail)

    with pytest.raises(OSError):
        cwa_functions.convert_library_start(queue.Queue())

    assert fake.running and fake.starts == 1


def test_a_launched_run_restarts_the_server_only_after_it_ends(server, monkeypatch):
    fake = server(running=True)
    started = _no_threads(monkeypatch)
    monkeypatch.setattr(cwa_functions.subprocess, "Popen", lambda *a, **k: "run")

    cwa_functions.convert_library_start(queue.Queue())

    assert not fake.running and fake.starts == 0
    assert started == [(cwa_functions._restart_content_server_when_done, ("run",))]


def test_a_server_that_was_not_running_is_not_started_by_a_run(server, monkeypatch):
    fake = server(running=False)
    started = _no_threads(monkeypatch)
    monkeypatch.setattr(cwa_functions.subprocess, "Popen", lambda *a, **k: "run")

    cwa_functions.convert_library_start(queue.Queue())

    assert fake.starts == 0 and started == []
