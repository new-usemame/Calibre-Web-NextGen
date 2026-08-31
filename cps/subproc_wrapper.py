# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import sys
import os
import subprocess
import re
import threading

# How long to wait for the background stderr reader to finish after the
# child has exited. It is draining an already-closed pipe at that point,
# so this only guards against a wedged reader thread.
_DRAIN_JOIN_TIMEOUT = 30

def process_open(command, quotes=(), env=None, sout=subprocess.PIPE, serr=subprocess.PIPE, newlines=True):
    # Linux py2.7 encode as list without quotes no empty element for parameters
    # linux py3.x no encode and as list without quotes no empty element for parameters
    # windows py2.7 encode as string with quotes empty element for parameters is okay
    # windows py 3.x no encode and as string with quotes empty element for parameters is okay
    # separate handling for windows and linux
    if os.name == 'nt':
        for key, element in enumerate(command):
            if key in quotes:
                command[key] = '"' + element + '"'
        exc_command = " ".join(command)
    else:
        exc_command = [x for x in command]

    popen_kwargs = {}
    if os.name != 'nt':
        # Run the child in its own session/process group so a hung export tree
        # (calibredb -> calibre-parallel) can be killed as a group on timeout.
        popen_kwargs['start_new_session'] = True

    return subprocess.Popen(exc_command, shell=False, stdout=sout, stderr=serr, universal_newlines=newlines, env=env,
                            **popen_kwargs) # nosec


def _drain_into(stream, sink):
    """Consume a child pipe to EOF, collecting its lines into `sink`."""
    try:
        for line in stream:
            sink.append(line)
    except (OSError, ValueError):
        # The pipe was closed underneath us; nothing left to collect.
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def stream_process_output(p, on_stdout_line=None, join_timeout=_DRAIN_JOIN_TIMEOUT):
    """Read a child's stdout to EOF while its stderr drains concurrently.

    stdout and stderr are separate pipes with separate kernel buffers.
    Draining only one of them and reading the other after the child exits
    deadlocks the pair as soon as the undrained pipe fills (~64 KB on
    Linux): the child blocks on write, the parent blocks on read, and
    neither ever moves again. That is fork issue #1110, where converting
    an OCR'd PDF hung the task queue forever because calibre's PDF
    pipeline emits a warning per malformed object.

    `on_stdout_line` is called with each raw stdout line as it arrives,
    so live progress parsing keeps working. Returns the collected stderr
    lines; read `p.returncode` for the exit status.
    """
    stderr_lines = []
    reader = None
    if p.stderr is not None:
        reader = threading.Thread(target=_drain_into, args=(p.stderr, stderr_lines))
        reader.daemon = True
        reader.start()

    if p.stdout is not None:
        try:
            for line in p.stdout:
                if on_stdout_line is not None:
                    on_stdout_line(line)
        except (OSError, ValueError):
            pass
        finally:
            try:
                p.stdout.close()
            except (OSError, ValueError):
                pass

    p.wait()
    if reader is not None:
        reader.join(timeout=join_timeout)
    return stderr_lines


def process_wait(command, serr=subprocess.PIPE, pattern=""):
    # Run command, wait for process to terminate, and return the first match of
    # `pattern` in its output. Both pipes are drained together; waiting on the
    # child before reading them deadlocks on any chatty binary (#1110).
    newlines = os.name != 'nt'
    ret_val = ""
    p = process_open(command, serr=serr, newlines=newlines)
    stdout_lines = []
    stream_process_output(p, stdout_lines.append)
    for line in stdout_lines:
        if isinstance(line, bytes):
            line = line.decode('utf-8', errors="ignore")
        match = re.search(pattern, line, re.IGNORECASE)
        if match and ret_val == "":
            ret_val = match
            break
    return ret_val
