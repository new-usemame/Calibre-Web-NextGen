"""cwa_db startup must not destroy settings it does not recognise (#2549).

A downgrade, or a column a newer version added, leaves a cwa_settings column
the running code has no schema entry for. Startup used to DROP it, so moving
back and forth between versions silently lost that user's setting.
"""

import sqlite3

import pytest

pytestmark = pytest.mark.unit


def _column_value(db_dir, column):
    con = sqlite3.connect(db_dir / "cwa.db")
    try:
        names = [row[1] for row in con.execute("PRAGMA table_info(cwa_settings)")]
        if column not in names:
            return None, names
        return con.execute(f"SELECT {column} FROM cwa_settings").fetchone()[0], names
    finally:
        con.close()


@pytest.mark.parametrize(
    "ddl, stored",
    [
        ("INTEGER DEFAULT 0", 7),
        # A TEXT value the generic quote/format normaliser would rewrite if it
        # treated the unknown column as one of its own.
        ("TEXT DEFAULT ''", '"EPUB, PDF"'),
    ],
)
def test_unknown_settings_column_and_value_survive_repeated_init(temp_cwa_db, tmp_path, ddl, stored):
    from cwa_db import CWA_DB

    column = "future_version_format_setting"
    temp_cwa_db.cur.execute(f"ALTER TABLE cwa_settings ADD {column} {ddl}")
    temp_cwa_db.cur.execute(f"UPDATE cwa_settings SET {column}=?", (stored,))
    temp_cwa_db.con.commit()
    temp_cwa_db.con.close()

    for _ in range(2):
        db = CWA_DB(verbose=False)
        db.con.close()
        value, names = _column_value(tmp_path, column)
        assert column in names
        assert value == stored


def test_known_settings_still_load_with_an_unknown_column_present(temp_cwa_db):
    from cwa_db import CWA_DB

    temp_cwa_db.cur.execute("ALTER TABLE cwa_settings ADD future_version_flag INTEGER DEFAULT 1")
    temp_cwa_db.cur.execute("UPDATE cwa_settings SET auto_convert=0")
    temp_cwa_db.con.commit()
    temp_cwa_db.con.close()

    db = CWA_DB(verbose=False)
    try:
        assert db.cwa_settings["auto_convert"] is False
        assert set(db.cwa_default_settings) <= set(db.cwa_settings)
        # Another version's setting is not handed to this version's readers.
        assert "future_version_flag" not in db.cwa_settings
    finally:
        db.con.close()


def test_unknown_column_does_not_decide_whether_settings_are_defaults(temp_cwa_db):
    db = temp_cwa_db
    db.cur.execute("ALTER TABLE cwa_settings ADD future_version_flag INTEGER DEFAULT 5")
    db.con.commit()
    db.cur.execute("SELECT * FROM cwa_settings")
    names = [header[0] for header in db.cur.description]
    stored = dict(zip(names, db.cur.fetchone()))
    # Every setting this version knows is at its default, so the check has to
    # walk all columns and reach the unknown one at the end of the table.
    db.cwa_default_settings = {name: stored[name] for name in db.cwa_default_settings}
    db.cur.execute("UPDATE cwa_settings SET default_settings=0")
    db.con.commit()

    db.set_default_settings()

    assert db.cur.execute("SELECT default_settings FROM cwa_settings").fetchone()[0] == 1
