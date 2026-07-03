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


def process_wait(command, serr=subprocess.PIPE, pattern=""):
    # Run command, wait for process to terminate, and return an iterator over lines of its output.
    newlines = os.name != 'nt'
    ret_val = ""
    p = process_open(command, serr=serr, newlines=newlines)
    p.wait()
    for line in p.stdout.readlines():
        if isinstance(line, bytes):
            line = line.decode('utf-8', errors="ignore")
        match = re.search(pattern, line, re.IGNORECASE)
        if match and ret_val == "":
            ret_val = match
            break
    p.stdout.close()
    p.stderr.close()
    return ret_val
