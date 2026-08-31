# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for fork issue #1110.

`_convert_ebook_format` used to stream the converter's stdout in a
`while p.poll() is None` loop and only read stderr *after* the child had
exited. Both are pipes. Once the child writes more to stderr than the
kernel pipe buffer holds (~64 KB on Linux), it blocks waiting for a
reader, while the parent is blocked reading a stdout that will never
produce another line. Neither side moves again and the convert task
hangs in the queue forever.

PDF input is the reliable trigger: calibre's PDF pipeline emits a
warning per malformed object, so an OCR'd scan produces tens of
thousands of stderr lines in seconds.

These tests drive real child processes that flood one pipe while the
other stays quiet, so a reintroduced single-pipe drain deadlocks the
test instead of passing quietly.
"""

import ast
import os
import subprocess
import sys
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from cps.subproc_wrapper import process_wait, stream_process_output  # noqa: E402


# Enough to overrun the pipe buffer on every platform we ship on.
_FLOOD_LINES = 20000
_TIMEOUT_SECONDS = 45


def _child(script):
    return subprocess.Popen(
        [sys.executable, '-c', script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=False,
    )


def _run_with_watchdog(fn):
    """Run `fn` on a thread and fail loudly instead of hanging the suite."""
    box = {}

    def target():
        try:
            box['value'] = fn()
        except BaseException as exc:  # pragma: no cover - surfaced below
            box['error'] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=_TIMEOUT_SECONDS)
    if thread.is_alive():
        raise AssertionError(
            'deadlocked: still blocked after %ss, which is the #1110 '
            'single-pipe drain regression' % _TIMEOUT_SECONDS)
    if 'error' in box:
        raise box['error']
    return box['value']


class TestStreamProcessOutput(unittest.TestCase):
    """The helper both convert call sites use must drain stdout and stderr."""

    def test_survives_a_stderr_flood_while_stdout_is_quiet(self):
        script = (
            'import sys\n'
            'for i in range(%d):\n'
            '    sys.stderr.write("pdf warning: malformed object %%d\\n" %% i)\n'
            'sys.stderr.flush()\n'
            'sys.stdout.write("50%% converting\\n")\n'
            'sys.stdout.write("100%% done\\n")\n'
        ) % _FLOOD_LINES

        seen = []
        p = _child(script)
        stderr_lines = _run_with_watchdog(
            lambda: stream_process_output(p, seen.append))

        self.assertEqual(p.returncode, 0)
        # stdout still arrives in full, so progress parsing keeps working.
        self.assertEqual(len(seen), 2)
        self.assertIn(b'100%', seen[1])
        # and the stderr the old code deadlocked on is captured, not dropped.
        self.assertEqual(len(stderr_lines), _FLOOD_LINES)

    def test_survives_a_stdout_flood_while_stderr_is_quiet(self):
        """The mirror case: never trade one deadlock for the other."""
        script = (
            'import sys\n'
            'for i in range(%d):\n'
            '    sys.stdout.write("%%d%%%% converting\\n" %% (i %% 100))\n'
            'sys.stdout.flush()\n'
            'sys.stderr.write("done\\n")\n'
        ) % _FLOOD_LINES

        seen = []
        p = _child(script)
        stderr_lines = _run_with_watchdog(
            lambda: stream_process_output(p, seen.append))

        self.assertEqual(p.returncode, 0)
        self.assertEqual(len(seen), _FLOOD_LINES)
        self.assertEqual(len(stderr_lines), 1)

    def test_reports_a_nonzero_exit_and_still_returns_stderr(self):
        script = (
            'import sys\n'
            'for i in range(%d):\n'
            '    sys.stderr.write("boom %%d\\n" %% i)\n'
            'sys.exit(3)\n'
        ) % _FLOOD_LINES

        p = _child(script)
        stderr_lines = _run_with_watchdog(lambda: stream_process_output(p, None))

        self.assertEqual(p.returncode, 3)
        self.assertEqual(len(stderr_lines), _FLOOD_LINES)

    def test_tail_of_stdout_written_just_before_exit_is_not_lost(self):
        """`while p.poll() is None` also dropped whatever raced the exit."""
        script = (
            'import sys\n'
            'sys.stdout.write("100% done\\n")\n'
            'sys.stdout.flush()\n'
        )
        seen = []
        p = _child(script)
        _run_with_watchdog(lambda: stream_process_output(p, seen.append))

        self.assertEqual(p.returncode, 0)
        self.assertEqual(seen, [b'100% done\n'])


class TestProcessWait(unittest.TestCase):
    """`process_wait` waited on the child before reading either pipe."""

    def test_survives_a_stdout_flood_before_the_match(self):
        script = (
            'import sys\n'
            'for i in range(%d):\n'
            '    sys.stdout.write("noise %%d\\n" %% i)\n'
            'sys.stdout.write("calibre 7.16.0 built\\n")\n'
        ) % _FLOOD_LINES

        command = [sys.executable, '-c', script]
        match = _run_with_watchdog(
            lambda: process_wait(command, pattern=r'calibre (\S+)'))

        self.assertTrue(match)
        self.assertEqual(match.group(1), '7.16.0')


class TestOpfProbeErrorReporting(unittest.TestCase):
    """The --as-opf probe runs with newlines=False, so its lines are bytes."""

    def test_failure_lines_are_decoded_before_being_matched(self):
        """`b'...'.startswith('Traceback')` raises TypeError, not False.

        The failure branch compared raw bytes against str literals, so a
        failed metadata export blew up on the line meant to explain why it
        failed. Pin the decode-then-compare order.
        """
        source = TestConvertSourceInvariants._convert_source()
        tree = ast.parse(source)

        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == '_convert_calibre':
                target = node
                break
        self.assertIsNotNone(target, '_convert_calibre not found')

        for node in ast.walk(target):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == 'startswith':
                # the receiver must be a plain name that was decoded first,
                # never a subscript/attribute straight off the byte stream
                self.assertIsInstance(
                    func.value, ast.Name,
                    'startswith called on a non-decoded expression')

        # and the decode must actually be present in that branch
        self.assertIn("ele.decode('utf-8', errors=\"ignore\")", source)

    def test_decoding_a_calibre_traceback_does_not_raise(self):
        """Behavioural mirror of the branch, on the shape it really sees."""
        calibre_traceback = [
            b'Traceback (most recent call last):\n',
            b'  File "site-packages/calibre/db.py", line 1, in <module>\n',
            b'DatabaseError: no such column: foo\n',
        ]
        error_message = ""
        for ele in calibre_traceback:
            if isinstance(ele, bytes):
                ele = ele.decode('utf-8', errors="ignore")
            ele = ele.strip('\r\n')
            if ele and not ele.startswith('Traceback') and not ele.startswith('  File'):
                error_message = ele
        self.assertEqual(error_message, 'DatabaseError: no such column: foo')


class TestConvertSourceInvariants(unittest.TestCase):
    """Source pins: this is exactly the shape that regressed once."""

    @staticmethod
    def _convert_source():
        path = os.path.join(
            os.path.dirname(__file__), '..', '..', 'cps', 'tasks', 'convert.py')
        with open(os.path.abspath(path), 'r', encoding='utf-8') as handle:
            return handle.read()

    def test_convert_never_reads_a_pipe_after_the_child_has_exited(self):
        """`p.stderr.readlines()` after the drain loop is the bug itself."""
        tree = ast.parse(self._convert_source())
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != 'readlines':
                continue
            owner = func.value
            if isinstance(owner, ast.Attribute) and owner.attr in ('stderr', 'stdout'):
                offenders.append(owner.attr)
        self.assertEqual(
            offenders, [],
            'cps/tasks/convert.py reads %s to EOF after the child exits; '
            'that is the #1110 deadlock. Use stream_process_output instead.'
            % ', '.join(sorted(set(offenders))))

    def test_convert_does_not_poll_loop_a_single_pipe(self):
        source = self._convert_source()
        self.assertNotIn(
            'while p.poll() is None', source,
            'a poll loop over one pipe leaves the other undrained (#1110)')

    def test_both_convert_call_sites_use_the_shared_helper(self):
        source = self._convert_source()
        self.assertIn('stream_process_output', source)
        # the calibredb --as-opf probe, the ebook-convert run, and kepubify
        self.assertGreaterEqual(source.count('stream_process_output('), 3)


if __name__ == '__main__':
    unittest.main()
