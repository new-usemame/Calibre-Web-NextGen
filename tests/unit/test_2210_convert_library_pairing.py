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

    def hold_library(self):
        was, self.running = self.running, False
        server = self

        class Hold:
            released = False

            def release(self):
                if not self.released:
                    self.released = True
                    if was:
                        server.start()

        self.hold = Hold()
        return self.hold

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
    assert started == [(cwa_functions._restart_content_server_when_done, ("run", fake.hold))]


def test_a_server_that_was_not_running_is_not_started_by_a_run(server, monkeypatch):
    fake = server(running=False)
    started = _no_threads(monkeypatch)
    monkeypatch.setattr(cwa_functions.subprocess, "Popen", lambda *a, **k: "run")

    cwa_functions.convert_library_start(queue.Queue())

    assert fake.starts == 0
    assert started == [(cwa_functions._restart_content_server_when_done, ("run", fake.hold))]


def test_completion_failure_keeps_library_held_until_exit_is_confirmed(server):
    fake = server(running=True)
    hold = fake.hold_library()

    class Process:
        def wait(self):
            raise OSError("could not reap")

    with pytest.raises(OSError, match="could not reap"):
        cwa_functions._restart_content_server_when_done(Process(), hold)
    assert not fake.running and fake.starts == 0
    assert not hold.released


def test_reaper_thread_failure_keeps_conversion_cancellable_and_waits_before_release(server, monkeypatch):
    fake = server(running=True)
    process_queue = queue.Queue()
    observed = []
    class Process:
        def wait(self):
            observed.append((fake.running, process_queue.get_nowait() is self))
    process = Process()
    monkeypatch.setattr(cwa_functions.subprocess, 'Popen', lambda *a, **kw: process)
    class Thread:
        def __init__(self, **kw):
            pass
        def start(self):
            raise RuntimeError("can't start new thread")
    monkeypatch.setattr(cwa_functions, 'Thread', Thread)
    cwa_functions.convert_library_start(process_queue)
    assert observed == [(False, True)]
    assert fake.running and fake.starts == 1


def test_hold_failure_marks_conversion_terminal_before_any_child(monkeypatch, tmp_path):
    log_path = tmp_path / "convert-library.log"
    monkeypatch.setattr(cwa_functions, "_service_log_path", lambda _name: str(log_path))
    def busy():
        raise TimeoutError("restore owns metadata gate")
    monkeypatch.setattr(cwa_functions.content_server, "hold_library", busy)
    monkeypatch.setattr(cwa_functions.subprocess, "Popen",
                        lambda *_a, **_k: pytest.fail("unowned conversion launched"))
    try:
        cwa_functions.convert_library_start(queue.Queue())
    except TimeoutError:
        pass
    assert log_path.exists(), "the Tasks poller has no terminal conversion result"
    text = log_path.read_text()
    assert "NextGen Convert Library Service - Run Failed:" in text
    assert "NextGen Convert Library Service - Run Ended:" in text
