# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""A browser upload of a file with no embedded title got its internal staging
name as its title.

Both upload routes (``/api/v1/upload`` and the legacy ``/upload``) save the file
into the ingest folder as ``new_<user>_<timestamp>_<name>`` so that parallel
uploads never collide, and write the browser's filename into the ``.cwa.json``
sidecar. Ingest recorded that original name but handed the prefixed path to
``ebook-convert`` and ``calibredb add``. Calibre falls back to the file name
when a file carries no title, so a plain ``Rig Doc.docx`` was imported as
"new 1 20261113 112627 417824 Rig Doc" (OBSERVED on a :dev rig, 2026-11-13).

Pinned behaviour: when the sidecar names the original file, the paths the
Calibre tools see carry the user's name, not the staging prefix. A file
dropped in the watch folder keeps its own name, and the watched source itself
is never renamed (deletion and backup still find it).
"""

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import ingest_processor  # noqa: E402

STAGED = "new_1_20261113_112627_417824_Rig Doc.docx"


def _processor(tmp_path, filename, original_filename=None):
    """A real NewBookProcessor with only the state these two steps read.

    ``original_filename`` is set exactly as main() sets it from an ``import``
    sidecar; None means a watch-folder drop (no sidecar).
    """
    ingest = tmp_path / "ingest"
    ingest.mkdir()
    tmp_conv = tmp_path / "conv"
    tmp_conv.mkdir()
    staging = tmp_conv / "staging"
    staging.mkdir()
    nbp = object.__new__(ingest_processor.NewBookProcessor)
    nbp.filepath = str(ingest / filename)
    Path(nbp.filepath).write_bytes(b"document bytes")
    nbp.filename = filename
    nbp.original_filename = original_filename or filename
    nbp.input_format = Path(filename).suffix.lstrip(".").lower()
    nbp.target_format = "epub"
    nbp.tmp_conversion_dir = str(tmp_conv) + os.sep
    nbp.staging_dir = str(staging)
    nbp.calibre_env = os.environ.copy()
    nbp.cwa_settings = {"auto_backup_conversions": False}
    nbp.is_kindle_epub_fixer = False
    nbp.acquisition_intent = None
    nbp._folder_label_metadata = lambda: None
    nbp.backup = lambda *a, **k: None
    nbp.db = type("StatsDb", (), {"conversion_add_entry": lambda *a, **k: None})()
    return nbp


def _converter_argv(monkeypatch, nbp):
    seen = []

    def fake_convert(cmd, env=None, timeout=None, **kw):
        seen.append(list(cmd))
        Path(cmd[2]).write_bytes(b"epub bytes")

    monkeypatch.setattr(ingest_processor, "_run_converter_streaming", fake_convert)
    ok, out = nbp.convert_book()
    assert ok, out
    assert len(seen) == 1
    return seen[0], out


class _StopAtImport(BaseException):
    """Stops add_book_to_library at its first step after staging."""


def _staged_names(monkeypatch, nbp, book_path):
    names = []

    def stop():
        names.extend(sorted(os.listdir(nbp.staging_dir)))
        raise _StopAtImport()

    monkeypatch.setattr(ingest_processor, "mark_ingest_batch_active", stop)
    with pytest.raises(_StopAtImport):
        nbp.add_book_to_library(book_path)
    return names


def test_converter_sees_the_uploaded_name_not_the_staging_name(tmp_path, monkeypatch):
    nbp = _processor(tmp_path, STAGED, original_filename="Rig Doc.docx")
    argv, out = _converter_argv(monkeypatch, nbp)
    assert Path(argv[1]).name == "Rig Doc.docx"
    assert Path(argv[2]).name == "Rig Doc.epub"
    assert Path(out).name == "Rig Doc.epub"
    # The watched source is untouched: delete/backup still operate on it.
    assert Path(nbp.filepath).read_bytes() == b"document bytes"


def test_calibredb_stages_the_uploaded_name_when_not_converted(tmp_path, monkeypatch):
    staged = "new_1_20261113_112627_417824_Rig Doc.txt"
    nbp = _processor(tmp_path, staged, original_filename="Rig Doc.txt")
    names = _staged_names(monkeypatch, nbp, nbp.filepath)
    assert "Rig Doc.txt" in names
    assert not any(n.startswith("new_1_") for n in names)
    assert Path(nbp.filepath).exists()


def test_fb2_zip_upload_keeps_its_fbz_extension(tmp_path, monkeypatch):
    # The upload gate stages a Calibre .fb2.zip export as .fbz (#2597); the
    # presented name must keep the staged extension, not the browser's.
    staged = "new_1_20261113_112627_417824_Saga.fbz"
    nbp = _processor(tmp_path, staged, original_filename="Saga.fb2.zip")
    names = _staged_names(monkeypatch, nbp, nbp.filepath)
    assert "Saga.fbz" in names


def test_watch_folder_drop_keeps_its_own_name(tmp_path, monkeypatch):
    # No sidecar: a user who drops "new_2_x_Notes.docx" in the folder meant
    # that name, so nothing is stripped.
    nbp = _processor(tmp_path, "new_2_x_Notes.docx")
    argv, _ = _converter_argv(monkeypatch, nbp)
    assert Path(argv[1]).name == "new_2_x_Notes.docx"
    assert Path(argv[2]).name == "new_2_x_Notes.epub"
