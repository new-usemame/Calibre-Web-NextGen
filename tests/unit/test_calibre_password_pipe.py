# SPDX-License-Identifier: GPL-3.0-or-later
"""Credential input must use the actual pipe and preserve spaces, without argv secrets."""
import importlib.util
import io
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2] / "cps"


def load(name):
    spec = importlib.util.spec_from_file_location("pipe_test_" + name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_password_pipe_preserves_trailing_spaces_and_does_not_select_getpass(monkeypatch):
    reader = load("calibredb_pipe")
    monkeypatch.setattr(reader.sys, "stdin", io.StringIO("My password  \n"))
    assert reader.read_password("a console prompt") == "My password  "
    with pytest.raises(EOFError):
        reader.read_password()


@pytest.mark.parametrize("binary, debug", [
    ("/opt/calibre/calibredb", "/opt/calibre/calibre-debug"),
    ("C:/Calibre/calibredb.exe", "C:/Calibre/calibre-debug.exe"),
])
def test_authenticated_invocation_keeps_the_password_only_in_the_pipe(binary, debug):
    policy = load("calibre_library_target")
    target = policy.LibraryTarget(["--username", "user", "--password", "<stdin>"], "Secret with spaces  \n")
    original = [binary, "show_metadata", "--as-opf", "5", *target.args]
    command = policy.calibredb_command(original, target)
    assert command[:2] == [debug, "-e"]
    assert Path(command[2]).name == "calibredb_pipe.py"
    assert command[3:] == ["--", *original[1:]]
    assert original[0] == binary
    assert all("Secret" not in argument for argument in command)
    assert target.stdin == "Secret with spaces  \n"


def test_path_or_explicit_anonymous_invocation_keeps_native_calibredb():
    policy = load("calibre_library_target")
    command = ["calibredb", "list", "--library-path=/books"]
    assert policy.calibredb_command(command, policy.LibraryTarget([], None)) == command
