# -*- coding: utf-8 -*-
# Calibre-Web Automated - fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert Library must own its temp directory and must not fake success.

Two defects that compound into "Convert Library reports every book converted
and the library is unchanged":

1. `ingest_processor.py` ends each run with `shutil.rmtree()` on the shared
   temp conversion directory, removing the directory itself rather than
   emptying it. Ingest recovers because it calls `mkdir(exist_ok=True)` on its
   next run. `convert_library.py` only ever wrote into that path, so after the
   first ingest `ebook-convert` was handed an output path inside a directory
   that no longer existed and produced no file. `cwa-init` recreates the
   directory at container start, which is what made this look intermittent.

2. Every conversion and import command was a `subprocess.Popen` wrapped in
   `except subprocess.CalledProcessError`. `Popen` never raises that, so the
   handler was unreachable and a non-zero exit fell through to the success
   message. A run that added nothing still printed "Conversion ... successful!"
   and "Import ... successfully completed!" for every book.

The second defect is why the first went unnoticed: there was no way to tell
from the log that nothing had happened.
"""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import convert_library  # noqa: E402


def _converter(tmp_conversion_dir):
    """A LibraryConverter with only the attributes these paths touch.

    __init__ reads cwa.db and the Calibre library, neither of which exists in
    a unit-test environment.
    """
    converter = convert_library.LibraryConverter.__new__(
        convert_library.LibraryConverter)
    converter.verbose = False
    converter.current_book = 1
    converter.to_convert = []
    converter.tmp_conversion_dir = str(tmp_conversion_dir) + "/"
    return converter


def test_ensure_tmp_conversion_dir_creates_a_missing_directory(tmp_path):
    target = tmp_path / "cwa_conversion_tmp"
    converter = _converter(target)
    assert not target.exists()

    converter.ensure_tmp_conversion_dir()

    assert target.is_dir(), (
        "Convert Library did not create its own temp directory, so every "
        "ebook-convert call writes into a path that does not exist")


def test_empty_tmp_con_dir_recreates_a_directory_removed_mid_run(tmp_path):
    target = tmp_path / "cwa_conversion_tmp"
    converter = _converter(target)
    converter.ensure_tmp_conversion_dir()
    (target / "leftover.epub").write_text("x", encoding="utf-8")

    # An ingest finishing mid-run takes the whole directory with it.
    import shutil
    shutil.rmtree(target)

    converter.empty_tmp_con_dir()

    assert target.is_dir(), (
        "an ingest finishing partway through a Convert Library run leaves the "
        "rest of that run writing into a missing directory")
    assert list(target.iterdir()) == []


def test_run_streaming_raises_on_a_failed_command(tmp_path):
    converter = _converter(tmp_path / "tmp")
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        converter._run_streaming(
            [sys.executable, "-c", "import sys; sys.stdout.write('boom\\n'); sys.exit(3)"])

    assert excinfo.value.returncode == 3
    assert "boom" in (excinfo.value.output or ""), (
        "the captured output must reach the handler; the kepub branch prints "
        "e.stderr when it reports a failed conversion")


def test_a_command_that_cannot_start_fails_the_book_not_the_run(tmp_path):
    converter = _converter(tmp_path / "tmp")
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        converter._run_streaming([str(tmp_path / "no-such-kepubify"), "--inplace"])

    assert excinfo.value.returncode == 127


def test_undecodable_command_output_does_not_abort_the_run(tmp_path):
    converter = _converter(tmp_path / "tmp")
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        converter._run_streaming(
            [sys.executable, "-c",
             "import sys; sys.stdout.buffer.write(b'Livre \\xe9crit\\n'); sys.exit(1)"])

    assert "Livre" in excinfo.value.output


def _write_tool(bin_dir, name, body):
    tool = bin_dir / name
    tool.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    tool.chmod(0o755)


def test_a_failed_book_is_reported_and_the_next_book_still_converts(tmp_path, monkeypatch):
    """Book 1's ebook-convert fails after an ingest removed the temp directory
    (and skips the per-book cleanup). Book 1 must be reported as failed, and
    book 2 must still find a directory, convert and import. Runs the real
    command path with fake tools on PATH."""
    target = tmp_path / "cwa_conversion_tmp"
    converter = _converter(target)
    library = tmp_path / "library"
    books = []
    for number, title in ((1, "First"), (2, "Second")):
        folder = library / "Author" / f"{title} ({number})"
        folder.mkdir(parents=True)
        (folder / f"{title}.mobi").write_text("x", encoding="utf-8")
        books.append(str(folder / f"{title}.mobi"))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_tool(bin_dir, "ebook-convert",
                'case "$1" in *First*) rm -rf "$(dirname "$2")"; echo "First.mobi is DRM locked"; exit 1;; esac\n'
                'echo converted > "$2" || exit 2\n')
    _write_tool(bin_dir, "calibredb", 'test -f "$3" || exit 3\n')

    converter.to_convert = books
    converter.target_format = "epub"
    converter.kindle_epub_fixer = False
    converter.library_dir = str(library) + "/"
    converter.calibre_env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    converter.cwa_settings = {"auto_backup_conversions": False, "auto_backup_imports": False}

    class _Db:
        def conversion_add_entry(self, *args):
            pass

        def import_add_entry(self, *args):
            pass

    converter.db = _Db()
    monkeypatch.setattr(converter, "set_library_permissions", lambda: None)
    log = []
    monkeypatch.setattr(convert_library, "print_and_log", lambda message, *a, **k: log.append(str(message)))
    converter.ensure_tmp_conversion_dir()

    converted = converter.convert_library()

    assert converted == 1, "the run summary must not count the failed book as converted"
    text = "\n".join(log)
    assert "Conversion of First.mobi was unsuccessful" in text
    assert "DRM locked" in text, "the tool's own reason must reach the log file"
    assert "Conversion of First.mobi to epub format successful" not in text
    assert "Import of Second.epub successfully completed" in text, text
