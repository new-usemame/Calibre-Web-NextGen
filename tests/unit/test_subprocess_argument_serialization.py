# SPDX-License-Identifier: GPL-3.0-or-later
"""Preserve argument boundaries when a routed Calibre command reaches Windows."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.unit


def test_windows_calibre_arguments_keep_username_spaces_quotes_and_empty_values(monkeypatch):
    """The Windows wire command must preserve every value and leave caller argv untouched."""
    spec = importlib.util.spec_from_file_location('isolated_subproc', Path(__file__).resolve().parents[2] / 'cps/subproc_wrapper.py')
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    monkeypatch.setattr(wrapper, 'os', SimpleNamespace(name='nt'))
    spawn = Mock()
    monkeypatch.setattr(wrapper.subprocess, 'Popen', spawn)
    args = [r'C:\Program Files\Calibre\calibredb.exe', 'export', '--with-library',
            'http://127.0.0.1:7777/#Calibre_Library', '--username', 'Library Reader',
            '--password', '<stdin>', '--to-dir', r'C:\Temp\Book export',
            '--template', 'title "quoted"', '']
    original = list(args)
    password_pipe = spawn.return_value.stdin
    wrapper.process_open(args, quotes=[5], stdin_payload='owned-test-password\n')
    expected = ('"C:\\Program Files\\Calibre\\calibredb.exe" export --with-library '
                'http://127.0.0.1:7777/#Calibre_Library --username "Library Reader" '
                '--password <stdin> --to-dir "C:\\Temp\\Book export" '
                '--template "title \\"quoted\\"" ""')
    assert spawn.call_args.args[0] == expected
    assert args == original
    assert spawn.call_args.kwargs['shell'] is False
    assert password_pipe.write.call_args.args[0] == 'owned-test-password\n'
