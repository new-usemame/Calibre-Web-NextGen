# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A password handed to calibredb on stdin must not break the caller's reap.

With the content server running and credentials set, every routed calibredb
call passes ``--password <stdin>`` and ``process_open`` feeds the password
through the pipe. Its callers (the download-time metadata export, conversion)
then reap the child with ``communicate()``. Measured in review of #2210: a pipe
closed but left on the Popen object makes that ``communicate()`` raise
``ValueError: I/O operation on closed file`` -- every non-kepub download,
send-to-ereader and conversion returned 500 once auth was configured.
"""
import sys

import pytest

from cps.subproc_wrapper import process_open

pytestmark = pytest.mark.unit

# Stands in for calibredb: reads the password line the way --password <stdin>
# does, then answers on stdout so the caller can see what arrived.
_READS_PASSWORD = [sys.executable, "-c",
                   "import sys; print('got:' + sys.stdin.readline().strip())"]


@pytest.mark.parametrize("newlines", [True, False])
def test_a_child_fed_on_stdin_can_still_be_reaped_with_communicate(newlines):
    p = process_open(list(_READS_PASSWORD), stdin_payload="s3cret\n", newlines=newlines)

    out, err = p.communicate(timeout=30)

    out = out if newlines else out.decode("utf-8")
    assert p.returncode == 0, err
    assert out.strip() == "got:s3cret"


def test_a_child_with_no_payload_keeps_its_old_shape():
    p = process_open([sys.executable, "-c", "print('ok')"])

    out, _err = p.communicate(timeout=30)

    assert p.stdin is None
    assert out.strip() == "ok"
