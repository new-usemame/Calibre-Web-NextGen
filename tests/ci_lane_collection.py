# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Collect once and apply CI's path/marker selectors to actual pytest items.

A stdlib-only guardian observes a parent-owned pipe outside the collector group.
If pytest's thread timeout kills its worker, EOF terminates and reaps that group.
This does not contain deliberately detached processes created by test imports.
"""

import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import selectors
import time


def _terminate_collector(process):
    """Reap only the dedicated group created by this guardian."""
    for sig, grace in [(signal.SIGTERM, 1), (signal.SIGKILL, 5)]:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            process.wait(timeout=5)
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            process.poll()
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                process.wait(timeout=5)
                return
            time.sleep(0.05)
    raise RuntimeError("owned collector group remains: %s" % process.pid)


def _guardian(root, request, report, parent_pipe):
    """Stay outside pytest imports and the collector's dedicated group."""
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--collect",
         str(root), str(request), str(report)],
        cwd=root, stdin=subprocess.DEVNULL, start_new_session=True,
    )
    completion = {"collector_pid": process.pid, "cleanup_complete": False}
    try:
        # A busy worker can inherit descriptors beyond select's fixed bitmap.
        # Keep registration inside the cleanup boundary, including allocation faults.
        with selectors.DefaultSelector() as parent_events:
            parent_events.register(parent_pipe, selectors.EVENT_READ)
            while process.poll() is None and not stopping:
                if parent_events.select(0.1):
                    if not os.read(parent_pipe, 1):
                        stopping = True
        completion["parent_gone_or_interrupted"] = stopping
        completion["collector_exit"] = process.returncode
    finally:
        try:
            _terminate_collector(process)
            completion["cleanup_complete"] = True
        except BaseException as error:
            completion["cleanup_error"] = repr(error)
        report.with_suffix(".cleanup.json").write_text(json.dumps(completion), encoding="utf-8")
    return 0 if not stopping and completion["collector_exit"] == 0 and completion["cleanup_complete"] else 1


def collect_ci_coverage(root, invocations, timeout=900):
    """Return complete collection and each invocation's selected node IDs.

    A failed import or missing/partial report cannot become apparent coverage.
    The child evaluates markers with the installed pytest implementation after
    conftest has assigned inherited/directory markers.
    """
    root = Path(root).resolve()
    temporary = Path(tempfile.mkdtemp(prefix="ci-lane-collection-"))
    reader = writer = process = None
    cleanup_resolved = False
    try:
        request = temporary / "request.json"
        report = temporary / "report.json"
        request.write_text(json.dumps(invocations), encoding="utf-8")
        reader, writer = os.pipe()
        try:
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--guard", str(root),
                 str(request), str(report), str(reader)],
                cwd=root, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True, pass_fds=(reader,),
            )
            os.close(reader)
            reader = None
            stdout, stderr = process.communicate(timeout=timeout)
            assert process.returncode == 0, (
                "CI lane collection failed (exit %s); partial node IDs are not "
                "coverage:\n%s\n%s" % (process.returncode, stdout[-6000:], stderr[-6000:])
            )
            assert report.exists(), "collector exited without its completion report"
            data = json.loads(report.read_text(encoding="utf-8"))
            assert data["exit_status"] == 0 and not data["collection_errors"], data
            assert data["invocations"] == json.loads(request.read_text()), data
            assert len(data["selected"]) == len(invocations), data
            assert len(data["all"]) == len(set(data["all"])), "duplicate collected node IDs"
            assert set(data["markers"]) == set(data["all"]), "incomplete marker inventory"
            return data
        finally:
            # EOF also covers a hard worker exit, which bypasses this finally.
            if writer is not None:
                os.close(writer)
                writer = None
            if reader is not None:
                os.close(reader)
                reader = None
            if process is not None:
                try:
                    process.wait(timeout=15)
                    cleanup = json.loads(report.with_suffix(".cleanup.json").read_text())
                    if not cleanup["cleanup_complete"]:
                        raise RuntimeError(cleanup)
                except BaseException as error:
                    raise RuntimeError(
                        "collector cleanup unresolved; evidence retained at %s" % temporary
                    ) from error
            cleanup_resolved = True
    finally:
        if cleanup_resolved or process is None:
            shutil.rmtree(temporary)


def _path_matcher(root, paths):
    selectors = []
    for raw in paths or ["tests"]:
        filename, *nodes = raw.split("::")
        path = (root / filename).resolve()
        relative = path.relative_to(root).as_posix()
        if not path.exists():
            raise ValueError("CI selector path does not exist: " + raw)
        if path.is_dir():
            if nodes:
                raise ValueError("node selector requires a file: " + raw)
            selectors.append((relative + "/", True))
        else:
            selectors.append(("::".join([relative, *nodes]), False))

    def matches(nodeid):
        return any(
            nodeid.startswith(selector) if directory else
            nodeid == selector or nodeid.startswith(selector + "::")
            or nodeid.startswith(selector + "[")
            for selector, directory in selectors
        )
    matches.selectors = selectors
    return matches


def _child(root, invocations, report):
    if os.getpgrp() != os.getpid():
        raise RuntimeError("collector requires a dedicated process group")

    import pytest
    from _pytest.mark import MarkMatcher
    from _pytest.mark.expression import Expression

    selected_paths = [_path_matcher(root, paths) for paths, _ in invocations]
    expressions = [Expression.compile(marker) if marker else None
                   for _, marker in invocations]
    data = {"invocations": invocations, "all": [], "selected": [], "markers": {},
            "collection_errors": []}

    class Coverage:
        def pytest_collectreport(self, report):
            if report.failed:
                data["collection_errors"].append(str(report.longrepr))

        def pytest_collection_finish(self, session):
            data["all"] = [item.nodeid for item in session.items]
            data["markers"] = {item.nodeid: sorted({mark.name for mark in item.iter_markers()})
                               for item in session.items}
            for path_matches in selected_paths:
                for selector, directory in path_matches.selectors:
                    if not any(
                        node.startswith(selector) if directory else
                        node == selector or node.startswith(selector + "::")
                        or node.startswith(selector + "[")
                        for node in data["all"]
                    ):
                        raise ValueError("CI selector has no collected node: " + selector)
            data["selected"] = [
                [item.nodeid for item in session.items
                 if path_matches(item.nodeid) and (expression is None or
                    expression.evaluate(MarkMatcher.from_markers(item.iter_markers())))]
                for path_matches, expression in zip(selected_paths, expressions)
            ]

        def pytest_sessionfinish(self, session, exitstatus):
            data["exit_status"] = int(exitstatus)
            report.write_text(json.dumps(data), encoding="utf-8")

    sys.path.insert(0, str(root))
    return pytest.main(["tests", "--collect-only", "-q", "-o", "addopts=",
                        "-p", "no:cacheprovider"], plugins=[Coverage()])


if __name__ == "__main__":
    mode, root, request, report, *pipe = sys.argv[1:]
    if mode == "--guard":
        raise SystemExit(_guardian(Path(root), Path(request), Path(report), int(pipe[0])))
    if mode == "--collect":
        raise SystemExit(_child(Path(root), json.loads(Path(request).read_text()), Path(report)))
    raise SystemExit("unknown collector mode")
