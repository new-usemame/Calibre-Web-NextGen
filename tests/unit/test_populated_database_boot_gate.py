# SPDX-License-Identifier: GPL-3.0-or-later
"""General startup-migration gate against a populated historical app.db.

The compressed fixture is a frozen database produced by the real models and
the shipped pre-#1939 rollback, not a hand-written approximation.  Keeping the
starting schema frozen is what makes this test cover future migrations: every
new mapped table or column is absent until the real startup path creates it.
"""

from __future__ import annotations

import gzip
import logging
from pathlib import Path
import re
import sqlite3

from cryptography.fernet import Fernet
import pytest

from cps import config_sql, constants, ub
from cps.progress_syncing import models as _progress_models  # noqa: F401


pytestmark = pytest.mark.unit

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "app_db"
    / "populated_pre_1939.sqlite.gz"
)
FORBIDDEN_MIGRATION_OUTPUT = re.compile(
    r"\boperationalerror\b|no such (?:column|table)", re.IGNORECASE
)


def _restore_fixture(destination: Path) -> None:
    with gzip.open(FIXTURE, "rb") as source:
        destination.write_bytes(source.read())


def _current_model_schema() -> dict[str, set[str]]:
    tables = {
        **ub.Base.metadata.tables,
        **config_sql._Base.metadata.tables,
    }
    return {
        table.name: {column.name for column in table.columns}
        for table in tables.values()
    }


def _actual_schema(db_path: Path) -> dict[str, set[str]]:
    with sqlite3.connect(db_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        return {
            table_name: {
                row[1]
                for row in connection.execute(
                    'PRAGMA table_info("{}")'.format(
                        table_name.replace('"', '""')
                    )
                )
            }
            for table_name in table_names
        }


def _formatted_migration_output(caplog, captured) -> str:
    formatter = logging.Formatter()
    return "\n".join(
        [
            captured.out,
            captured.err,
            *(formatter.format(record) for record in caplog.records),
        ]
    )


def test_populated_historical_database_reaches_current_schema_in_one_quiet_boot(
        tmp_path, monkeypatch, caplog, capsys):
    db_path = tmp_path / "app.db"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _restore_fixture(db_path)

    before = _actual_schema(db_path)
    assert "user" in before
    assert "user_library_book" not in before
    assert {
        "has_own_library",
        "user_library_seeded",
        "my_library_intro_dismissed",
    }.isdisjoint(before["user"])
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM user").fetchone()[0] == 9

    previous_session = ub.session
    previous_app_db_path = ub.app_DB_path
    monkeypatch.setattr(constants, "CONFIG_DIR", str(config_dir))
    try:
        with caplog.at_level(logging.INFO):
            ub.init_db(str(db_path))
            # This is the next app.db migration phase in the real create_app
            # boot path.  It auto-adds mapped configuration columns.
            config_sql.load_configuration(ub.session, Fernet.generate_key())
    finally:
        if ub.session is not previous_session:
            ub.session.close()
            ub.session.bind.dispose()
        ub.session = previous_session
        ub.app_DB_path = previous_app_db_path

    migration_output = _formatted_migration_output(
        caplog, capsys.readouterr()
    )
    forbidden = FORBIDDEN_MIGRATION_OUTPUT.search(migration_output)
    assert forbidden is None, (
        "startup swallowed a schema failure but continued:\n{}".format(
            migration_output
        )
    )

    actual_schema = _actual_schema(db_path)
    missing_schema = {
        table_name: sorted(expected_columns - actual_schema.get(table_name, set()))
        for table_name, expected_columns in _current_model_schema().items()
        if expected_columns - actual_schema.get(table_name, set())
    }
    assert not missing_schema, (
        "startup returned successfully without applying current model schema: "
        "{}".format(missing_schema)
    )

    with sqlite3.connect(db_path) as connection:
        users = connection.execute(
            "SELECT role, sidebar_view, view_settings, has_own_library, "
            "user_library_seeded, my_library_intro_dismissed "
            "FROM user ORDER BY id"
        ).fetchall()
        kobo_magic_shelf_enabled = connection.execute(
            "SELECT config_kobo_sync_magic_shelves FROM settings"
        ).fetchone()[0]

    assert len(users) == 9
    assert all(sidebar & constants.SIDEBAR_FAVORITES for _role, sidebar, *_ in users)
    assert [
        bool(sidebar & constants.SIDEBAR_DUPLICATES)
        for role, sidebar, *_ in users
    ] == [bool(role & constants.ROLE_ADMIN) for role, *_ in users]
    assert all(view_settings == "{}" for _role, _sidebar, view_settings, *_ in users)
    assert all(
        (has_own_library, seeded, intro_dismissed) == (0, 0, 0)
        for *_prefix, has_own_library, seeded, intro_dismissed in users
    )
    assert kobo_magic_shelf_enabled == 1

    migration_dir = config_dir / ".cwa_migrations"
    assert (migration_dir / "favorites_sidebar_v1").is_file()
    assert (migration_dir / "duplicates_sidebar_v1").is_file()
    assert (migration_dir / "kobo_magic_shelf_intent_v1").is_file()


def _boot_migrations(db_path, config_dir, monkeypatch, caplog, capsys):
    """Run the app.db half of the real create_app boot path once."""
    previous_session = ub.session
    previous_app_db_path = ub.app_DB_path
    monkeypatch.setattr(constants, "CONFIG_DIR", str(config_dir))
    try:
        with caplog.at_level(logging.INFO):
            ub.init_db(str(db_path))
            config_sql.load_configuration(ub.session, Fernet.generate_key())
    finally:
        if ub.session is not previous_session:
            ub.session.close()
            ub.session.bind.dispose()
        ub.session = previous_session
        ub.app_DB_path = previous_app_db_path
    return _formatted_migration_output(caplog, capsys.readouterr())


def _row_counts(db_path):
    with sqlite3.connect(db_path) as connection:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            name: connection.execute(
                'SELECT count(*) FROM "{}"'.format(name.replace('"', '""'))
            ).fetchone()[0]
            for name in names
        }



def _seed_every_empty_table(db_path, skip):
    """Give every table at least one row so row-count assertions can bite.

    A downgrade that promises to leave annotations, shelves, progress and the
    Kobo tables alone cannot be tested against a fixture where those tables are
    empty: ``0 -> 0`` reads as "unchanged" no matter what the code did.  Seeding
    generically (rather than hand-listing tables) keeps the guarantee honest as
    the schema grows, because a table added next year is seeded too.
    """
    placeholder = {"INTEGER": 1, "REAL": 1.0, "BLOB": b"x"}
    seeded = []
    with sqlite3.connect(db_path) as connection:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for name in names:
            if name in skip:
                continue
            quoted = '"{}"'.format(name.replace('"', '""'))
            if connection.execute("SELECT count(*) FROM {}".format(quoted)).fetchone()[0]:
                continue
            columns, values = [], []
            for _cid, column, decl, notnull, default, pk in connection.execute(
                    "PRAGMA table_info({})".format(quoted)):
                if pk or default is not None or not notnull:
                    continue
                columns.append('"{}"'.format(column.replace('"', '""')))
                values.append(placeholder.get((decl or "").upper().split("(")[0], "x"))
            try:
                connection.execute(
                    "INSERT INTO {} ({}) VALUES ({})".format(
                        quoted, ", ".join(columns) or "rowid",
                        ", ".join("?" * len(values)) or "NULL",
                    ),
                    values,
                )
            except sqlite3.Error:
                continue          # a table we cannot synthesise stays empty
            seeded.append(name)
    return seeded


def test_user_library_rollback_removes_only_its_own_schema_and_re_migrates(
        tmp_path, monkeypatch, caplog, capsys):
    """The documented downgrade hook must be safe, total, and re-appliable.

    This project has no Alembic, so ``rollback_user_library_schema`` is the
    only way back off #1939.  It is never exercised by the boot path, which
    means nothing else in the suite can catch it dropping a table it does not
    own or leaving the permission bit behind for older code to misread.
    """
    from sqlalchemy import create_engine

    db_path = tmp_path / "app.db"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _restore_fixture(db_path)
    _boot_migrations(db_path, config_dir, monkeypatch, caplog, capsys)

    # Put the feature into its fully-adopted state before rolling back: a user
    # in personal-library mode, holding the new role bit, with real membership.
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE user SET has_own_library = 1, user_library_seeded = 1, "
            "my_library_intro_dismissed = 1, role = role | 512 WHERE id = 1"
        )
        connection.executemany(
            "INSERT INTO user_library_book (user_id, book_id, added_at) "
            "VALUES (1, ?, '2026-01-01 00:00:00')",
            [(book_id,) for book_id in range(1, 51)],
        )
    # Without this the "nothing else moved" assertion below is vacuous: the
    # frozen fixture populates only 5 of ~58 tables, so a rollback that wiped
    # every shelf and annotation would compare 0 == 0 and pass.
    seeded = _seed_every_empty_table(db_path, skip={"user_library_book"})
    assert {"shelf", "annotation", "book_read_link", "kobo_synced_books"} <= set(seeded), (
        "guard tables were not seeded, so the collateral-damage assertion is "
        "vacuous; seeded={}".format(sorted(seeded))
    )

    populated = _row_counts(db_path)
    assert populated["user_library_book"] == 50
    assert all(populated[name] for name in seeded)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM user WHERE role & 512 = 512"
        ).fetchone()[0] == 1

    engine = create_engine("sqlite:///{}".format(db_path))
    try:
        ub.rollback_user_library_schema(engine)
        after = _row_counts(db_path)
        schema = _actual_schema(db_path)

        # 1. Its own schema is gone, completely.
        assert "user_library_book" not in schema
        assert {
            "has_own_library",
            "user_library_seeded",
            "my_library_intro_dismissed",
        }.isdisjoint(schema["user"])

        # 2. The permission bit is stripped, so older code cannot misread the
        #    role mask it does not know about.
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "SELECT count(*) FROM user WHERE role & 512 = 512"
            ).fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM user").fetchone()[0] == 9

        # 3. Nothing else moved.  Annotations, shelves, reading progress and
        #    every Kobo table are explicitly out of scope for the downgrade.
        changed = {
            name: (populated[name], after[name])
            for name in set(populated) - {"user_library_book"}
            if populated[name] != after.get(name)
        }
        assert not changed, "rollback altered tables it does not own: {}".format(changed)

        # 4. Running it twice is a no-op, not an error.
        ub.rollback_user_library_schema(engine)
        assert _row_counts(db_path) == after
    finally:
        engine.dispose()

    # 5. A rolled-back database is still a database the migration can re-apply
    #    to — the round trip, not just the downgrade.
    output = _boot_migrations(db_path, config_dir, monkeypatch, caplog, capsys)
    assert FORBIDDEN_MIGRATION_OUTPUT.search(output) is None, (
        "re-migrating a rolled-back database emitted a schema failure:\n{}".format(output)
    )
    schema = _actual_schema(db_path)
    assert "user_library_book" in schema
    assert {
        "has_own_library",
        "user_library_seeded",
        "my_library_intro_dismissed",
    } <= schema["user"]
    with sqlite3.connect(db_path) as connection:
        # Membership was discarded by design and is not resurrected, and every
        # account comes back in whole-library mode rather than a half-adopted one.
        assert connection.execute(
            "SELECT count(*) FROM user_library_book"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM user WHERE has_own_library = 1"
        ).fetchone()[0] == 0
