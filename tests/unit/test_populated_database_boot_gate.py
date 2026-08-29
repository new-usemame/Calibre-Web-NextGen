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
