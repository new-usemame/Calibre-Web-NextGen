# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for ingest staging filenames built from upload names.

Werkzeug's secure_filename() drops a basename that is entirely non-ASCII and
then strips the dot before the extension. The ingest service detects formats
from the staged filename, so "龙蛋.epub" used to be staged as "..._epub" and
silently deleted. _safe_upload_basename() must keep the extension.
"""
import os

import pytest

from cps import editbooks


@pytest.mark.unit
@pytest.mark.parametrize("filename,expected", [
    ("龙蛋.epub", "book.epub"),
    ("中文 书名.epub", "book.epub"),
    ("日本語.epub", "book.epub"),
    ("файл.epub", "book.epub"),
    ("龙蛋 Long Dan.epub", "Long_Dan.epub"),
    ("book.epub", "book.epub"),
    ("My Book.epub", "My_Book.epub"),
    ("书.fb2.zip", "book.fb2.zip"),
    ("../../龙蛋.epub", "book.epub"),
    (".epub", "book.epub"),
    ("龙蛋", "book"),
    ("book.", "book"),
])
def test_safe_upload_basename_keeps_extension(filename, expected):
    assert editbooks._safe_upload_basename(filename) == expected


@pytest.mark.unit
def test_get_ingest_path_keeps_extension_for_non_ascii_upload(monkeypatch, tmp_path):
    monkeypatch.setattr(editbooks, "get_ingest_dir", lambda: str(tmp_path))
    monkeypatch.setattr(editbooks, "network_share_mode_enabled", lambda: True)
    monkeypatch.setattr(editbooks, "_ensure_ingest_dir_writable", lambda *a, **k: None)
    monkeypatch.setattr(editbooks, "_get_ingest_owner_id", lambda *a: 1000)
    monkeypatch.setattr(editbooks.os, "chown", lambda *a: None)

    class Uploaded:
        filename = "龙蛋.epub"

    path = editbooks._get_ingest_path(Uploaded())
    assert os.path.dirname(path) == str(tmp_path)
    assert os.path.basename(path).endswith("_book.epub")
    assert path.endswith(".epub")
