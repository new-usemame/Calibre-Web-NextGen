# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ingest staging filenames built from upload names (#2701).

The ingest service picks the format from the staged file's last suffix. Werkzeug's
secure_filename() reduces "龙蛋.epub" to "epub", so the staged file had no suffix
and ingest deleted it after the upload API had already answered "queued".

Exception since #2598: a trailing ".fb2.zip" is normalized to ".fbz" before
the stem/suffix split — "zip" is not a format the ingest supports, so that
one case must not preserve its original last suffix.
"""
import os
from pathlib import Path

import pytest
from werkzeug.utils import secure_filename

from cps import editbooks


@pytest.fixture
def stage(monkeypatch, tmp_path):
    monkeypatch.setattr(editbooks, "get_ingest_dir", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks, "network_share_mode_enabled", lambda: True)
    monkeypatch.setattr(editbooks, "_ensure_ingest_dir_writable", lambda *a, **k: None)

    def _stage(filename):
        class Uploaded:
            pass
        Uploaded.filename = filename
        path = editbooks._get_ingest_path(Uploaded())
        assert os.path.dirname(path) == str(tmp_path)
        return Path(path)
    return _stage


@pytest.mark.unit
@pytest.mark.parametrize("filename", [
    "龙蛋.epub", "中文 书名.epub", "файл.epub", "龙蛋.EPUB", "1. 龙蛋.pdf",
    "第一卷.龙蛋.mobi", "../../龙蛋.epub",
])
def test_non_ascii_upload_keeps_the_suffix_ingest_reads(stage, filename):
    staged = stage(filename)
    assert staged.suffix == Path(filename).suffix
    assert staged.stem  # never a bare ".epub" that ingest would treat as hidden


@pytest.mark.unit
@pytest.mark.parametrize("filename", [
    "book.epub", "My Book.epub", "Dr. Who.epub", "My.Book.v2.epub",
])
def test_ascii_upload_names_stage_as_before(stage, filename):
    assert stage(filename).name.endswith("_" + secure_filename(filename))


@pytest.mark.unit
@pytest.mark.parametrize("filename", [
    "书.fb2.zip", "файл.fb2.zip", "x.fb2.zip", "My Book.fb2.zip",
])
def test_fb2_zip_uploads_stage_as_fbz(stage, filename):
    staged = stage(filename)
    assert staged.suffix == ".fbz"
    assert ".fb2.zip" not in staged.name
    assert staged.stem  # never a bare ".fbz" that ingest would treat as hidden
