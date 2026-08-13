# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""``scripts/metadata.db.sql`` must replay on every SQLite we support.

#1482 replaced the seed ``metadata.db`` binary with this dump. The dump as first
generated declared the two FTS5 virtual tables by writing rows straight into the
schema table under ``PRAGMA writable_schema=ON``, which is how ``sqlite3
.dump`` emits a virtual table.

That construction is version-dependent. On SQLite 3.53 the pragma takes effect
and the script replays; on SQLite 3.51 it is accepted, silently reads back ``0``,
and the next ``INSERT INTO sqlite_schema`` dies with ``table sqlite_master may
not be modified``. Measured on the same tree: Python 3.13/SQLite 3.53 gave 93
passed, Python 3.12.7/SQLite 3.51.0 gave 13 failed, 80 passed.

The behavioural half of this is already covered — the fixtures in
``test_auto_library_default_path.py`` are built from this dump, so a dump that
cannot replay fails them. What that cannot catch is a *regression*: CI pins
Python 3.13, where the broken form works fine. A regenerated dump would
reintroduce the pragma, pass every check here, and only break for contributors
and bare-metal installs on an older SQLite.

So the source-pin below is the part that has to exist. It fails on any stack.
"""

import sqlite3
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DUMP = REPO_ROOT / "scripts" / "metadata.db.sql"

EXPECTED_TABLE_COUNT = 37
FTS_TABLES = ("annotations_fts", "annotations_fts_stemmed")


@pytest.fixture(scope="module")
def dump_sql():
    assert DUMP.is_file(), f"{DUMP} is missing — #1482 seeds metadata.db from it"
    return DUMP.read_text()


def test_dump_does_not_write_to_the_schema_table(dump_sql):
    """The regression pin: no ``writable_schema`` round-trip in the dump.

    ``sqlite3 .dump`` emits virtual tables this way, so a regenerated dump
    brings it straight back.
    """
    offenders = [
        (n, line.strip())
        for n, line in enumerate(dump_sql.splitlines(), 1)
        if "writable_schema" in line or "sqlite_schema" in line or "sqlite_master" in line
    ]
    assert not offenders, (
        "metadata.db.sql writes to the schema table, which does not replay on "
        "SQLite < 3.53:\n"
        + "\n".join(f"  line {n}: {text[:110]}" for n, text in offenders)
        + "\n\nRegenerate with plain CREATE VIRTUAL TABLE statements instead — "
        "sqlite3 .dump emits the writable_schema form and it is not portable."
    )


def test_dump_declares_the_fts_tables_the_portable_way(dump_sql):
    for table in FTS_TABLES:
        assert f"CREATE VIRTUAL TABLE {table} USING fts5(" in dump_sql, (
            f"{table} is no longer declared with CREATE VIRTUAL TABLE"
        )


def test_dump_replays_and_the_fts_tables_work(tmp_path):
    """Behavioural half — build the database and exercise both tokenizers.

    Schema presence is not enough: the porter tokenizer is configured in the
    ``CREATE VIRTUAL TABLE`` text, so a wrong replay can leave a queryable table
    that has quietly lost its stemming.
    """
    db = tmp_path / "metadata.db"
    conn = sqlite3.connect(db)
    # Calibre registers both of these at runtime; the seed triggers call them.
    conn.create_function("title_sort", 1, lambda s: s)
    conn.create_function("uuid4", 0, lambda: str(uuid.uuid4()))
    conn.executescript(DUMP.read_text())

    tables = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    assert tables == EXPECTED_TABLE_COUNT

    conn.execute(
        "INSERT INTO books (id,title,sort,timestamp,pubdate,series_index,author_sort,path,flags)"
        " VALUES (99,'T','T',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1.0,'A','p',1)"
    )
    conn.execute(
        "INSERT INTO annotations"
        " (book,format,user_type,user,timestamp,annot_id,annot_type,annot_data,searchable_text)"
        " VALUES (99,'EPUB','local','me',1.0,'a1','highlight','{}','the quick brown foxes jumped')"
    )
    conn.commit()

    # Plain tokenizer: literal term.
    assert conn.execute(
        "SELECT rowid FROM annotations_fts WHERE annotations_fts MATCH 'brown'"
    ).fetchall() == [(1,)]

    # Porter tokenizer: 'jumping' only matches 'jumped' if stemming survived.
    assert conn.execute(
        "SELECT rowid FROM annotations_fts_stemmed"
        " WHERE annotations_fts_stemmed MATCH 'jumping'"
    ).fetchall() == [(1,)]

    # The delete trigger keeps the index in step.
    conn.execute("DELETE FROM annotations WHERE annot_id='a1'")
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM annotations_fts WHERE annotations_fts MATCH 'brown'"
    ).fetchone()[0] == 0
