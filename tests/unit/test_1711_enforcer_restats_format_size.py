# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Metadata enforcement must re-stat data.uncompressed_size (#1711).

`cps/kobo.py` advertises that column to a paired device verbatim as "Size", and
the device stores it in `content.___FileSize`. `cover_enforcer.py` rewrites format
files in place (ebook-polish / ebook-meta), so leaving the column alone advertises
a byte count the server will not serve. Measured on a live library: 8 of 435
format rows stale, and the three stale KEPUB rows were exactly the three books
enforced in one pass.

These tests EXECUTE the real method body against a real SQLite database rather
than asserting on its source text, so they fail if the SQL is wrong, not merely
if the wording changes.
"""
import ast
import os
import sqlite3
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cover_enforcer.py"
METHOD = "_restat_format_size_after_modification"


def _method_source(name):
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    pytest.fail("%s not found in cover_enforcer.py" % name)


def _enforcer(library):
    """Build a stub carrying just the attributes the method touches."""
    src = _method_source(METHOD)
    ns = {"os": os, "sqlite3": sqlite3, "print": lambda *a, **k: None}
    exec("class _Stub:\n" + textwrap.indent(src, "    "), ns)          # noqa: S102
    obj = ns["_Stub"]()
    obj.split_library = None
    obj.calibre_library = str(library)
    return obj


def _library(tmp_path, recorded_size):
    lib = tmp_path / "lib"
    lib.mkdir()
    con = sqlite3.connect(str(lib / "metadata.db"))
    con.execute("CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, "
                "uncompressed_size INTEGER, name TEXT)")
    con.execute("INSERT INTO data (book, format, uncompressed_size, name) VALUES (?,?,?,?)",
                (1, "KEPUB", recorded_size, "book"))
    con.commit(); con.close()
    book = lib / "book.kepub"
    book.write_bytes(b"x" * 4096)          # the real, post-rewrite size
    return lib, book


def _recorded(lib):
    con = sqlite3.connect(str(lib / "metadata.db"))
    try:
        return con.execute("SELECT uncompressed_size FROM data WHERE book=1 AND format='KEPUB'").fetchone()[0]
    finally:
        con.close()


def test_stale_size_is_corrected_to_the_bytes_on_disk(tmp_path):
    lib, book = _library(tmp_path, recorded_size=608670)      # the #1711 delta
    assert _recorded(lib) == 608670

    _enforcer(lib)._restat_format_size_after_modification("1", "kepub", str(book))

    assert _recorded(lib) == 4096 == os.path.getsize(str(book))


def test_format_is_matched_case_insensitively(tmp_path):
    """The enforcer passes book.file_format, which is lower case."""
    lib, book = _library(tmp_path, recorded_size=1)
    _enforcer(lib)._restat_format_size_after_modification("1", "kepub", str(book))
    assert _recorded(lib) == 4096


def test_a_missing_file_leaves_the_row_alone(tmp_path):
    lib, _book = _library(tmp_path, recorded_size=1234)
    _enforcer(lib)._restat_format_size_after_modification("1", "kepub", str(tmp_path / "gone.kepub"))
    assert _recorded(lib) == 1234, "a stat failure must not clear or corrupt the row"


def test_a_broken_database_does_not_raise(tmp_path):
    """An enforcement pass that already wrote the file must not fail here."""
    lib, book = _library(tmp_path, recorded_size=1)
    (lib / "metadata.db").write_bytes(b"not a database")
    _enforcer(lib)._restat_format_size_after_modification("1", "kepub", str(book))  # must not raise


def test_other_books_are_untouched(tmp_path):
    lib, book = _library(tmp_path, recorded_size=1)
    con = sqlite3.connect(str(lib / "metadata.db"))
    con.execute("INSERT INTO data (book, format, uncompressed_size, name) VALUES (2,'KEPUB',999,'other')")
    con.commit(); con.close()

    _enforcer(lib)._restat_format_size_after_modification("1", "kepub", str(book))

    con = sqlite3.connect(str(lib / "metadata.db"))
    try:
        assert con.execute("SELECT uncompressed_size FROM data WHERE book=2").fetchone()[0] == 999
    finally:
        con.close()


def test_the_enforcer_actually_calls_it_after_a_write():
    """Guards the wiring: the helper is useless if nothing invokes it."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "self.%s(book.book_id, book.file_format, file)" % METHOD in source
