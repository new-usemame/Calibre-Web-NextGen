# SPDX-License-Identifier: GPL-3.0-or-later
"""General startup-migration gate against a populated historical app.db.

The compressed fixture is a frozen database produced by the real models and
the shipped pre-#1939 rollback, not a hand-written approximation.  Keeping the
starting schema frozen is what lets this gate see a *populated* upgrade at all:
every #1939 column and table is absent until the real startup path adds it.

Read the schema assertion narrowly.  ``init_db`` calls
``Base.metadata.create_all()`` BEFORE ``migrate_Database()`` (cps/ub.py), so a
newly mapped table exists whether or not its migration ran, and a
query-before-ALTER defect still performs its ALTER later in the same boot.  In
both cases the final schema is complete -- which is exactly what #1950 looked
like: schema fine, data missing.  The schema check is therefore a floor, and
the defects this gate actually catches are caught by the other two detectors:
``FORBIDDEN_MIGRATION_OUTPUT`` over everything printed or logged during the
boot, and the explicit post-boot data and marker assertions.  A migration added
later inherits the first two automatically; its author must add the third.

"One boot" means one pass of the app.db phase -- ``init_db`` plus
``load_configuration``.  Later ``create_app`` phases that also write app.db are
outside this gate.
"""

from __future__ import annotations

import itertools

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
# Migrations here catch their own exceptions and print them, so a failure
# reaches us as TEXT, not as a raised error.  The pattern is therefore the
# detector, and every phrase below is a shape that has actually been produced
# by a real defect in this file's subject matter:
#   "no such column/table"        - SELECT-shape of a query-before-ALTER (#1950)
#   "has no column named"         - INSERT-shape of the same mis-ordering
#   "OperationalError"            - the class name, when only repr() is printed
#   "IntegrityError"/constraint   - a backfill meeting real data it did not expect
FORBIDDEN_MIGRATION_OUTPUT = re.compile(
    r"\boperationalerror\b"
    r"|\bintegrityerror\b"
    r"|no such (?:column|table)"
    r"|has no column named"
    r"|constraint failed",
    re.IGNORECASE,
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
        assert connection.execute("SELECT count(*) FROM user").fetchone()[0] == 10
        assert connection.execute(
            "SELECT count(*) FROM user WHERE role & ? = ?",
            (constants.ROLE_ANONYMOUS, constants.ROLE_ANONYMOUS),
        ).fetchone()[0] == 1

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
        system_shelves_per_user = connection.execute(
            "SELECT u.role, count(m.id) FROM user u "
            "LEFT JOIN magic_shelf m ON m.user_id = u.id AND m.is_system = 1 "
            "GROUP BY u.id ORDER BY u.id"
        ).fetchall()

    assert len(users) == 10
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

    # The system-shelf migration filters on ROLE_ANONYMOUS.  With a Guest row
    # in the fixture that filter is load-bearing: dropping it gives the Guest
    # five shelves it must never have, and the total moves 45 -> 50.
    anonymous_shelves = [
        count for role, count in system_shelves_per_user
        if role & constants.ROLE_ANONYMOUS
    ]
    real_user_shelves = [
        count for role, count in system_shelves_per_user
        if not role & constants.ROLE_ANONYMOUS
    ]
    assert anonymous_shelves == [0], (
        "the anonymous account was given system magic shelves: {}".format(
            system_shelves_per_user
        )
    )
    assert real_user_shelves == [5] * 9, system_shelves_per_user

    migration_dir = config_dir / ".cwa_migrations"
    assert (migration_dir / "favorites_sidebar_v1").is_file()
    assert (migration_dir / "duplicates_sidebar_v1").is_file()
    assert (migration_dir / "kobo_magic_shelf_intent_v1").is_file()


@pytest.mark.parametrize("message", [
    # SELECT-shape of a query-before-ALTER: the #1950 defect itself.
    "[Migration] Warning: (sqlite3.OperationalError) no such column: user.has_own_library",
    # INSERT-shape of the same mis-ordering, which says something different.
    "[Migration] Warning: table user has no column named has_own_library",
    # Only the class name survives when a migration prints repr(exception).
    "[Migration] Warning: OperationalError('database is locked')",
    # A backfill meeting real data it did not expect.
    "[Migration] Warning: (sqlite3.IntegrityError) UNIQUE constraint failed: user.name",
    "[Migration] Warning: FOREIGN KEY constraint failed",
])
def test_forbidden_migration_output_matches_every_shape_a_swallowed_failure_takes(message):
    """The detector is the gate, so its coverage is pinned rather than assumed.

    Migrations in this file's subject matter catch their own exceptions and
    print them, so a failure arrives as text.  Narrowing this pattern silently
    disables the gate for a whole class of defect, which is why each shape is
    named here with the defect that produces it.
    """
    assert FORBIDDEN_MIGRATION_OUTPUT.search(message) is not None


@pytest.mark.parametrize("message", [
    "[annotation-type-backfill] scan total=708 already_typed=93 applied=0",
    "Migrating system magic shelves...",
    "System shelf migration complete: 0 old shelves removed, 45 new shelves created",
    "[kobo-two-way-stage0] additive schema ready; runtime ownership unchanged",
])
def test_forbidden_migration_output_ignores_a_healthy_boot(message):
    """Real INFO lines from a clean production boot must not trip the gate."""
    assert FORBIDDEN_MIGRATION_OUTPUT.search(message) is None


def _boot_migrations(db_path, config_dir, monkeypatch, caplog, capsys):
    """Run the app.db half of the real create_app boot path once."""
    previous_session = ub.session
    previous_app_db_path = ub.app_DB_path
    monkeypatch.setattr(constants, "CONFIG_DIR", str(config_dir))
    # Drain BOTH capture surfaces, or this boot's output silently inherits the
    # previous one's: capsys is drained by reading it, caplog is not, so an
    # earlier boot's failure would be re-reported against a later boot.
    capsys.readouterr()
    caplog.clear()
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


def _table_contents(db_path):
    """Hash every row of every table.

    Row counts are the obvious identity and the wrong one: an UPDATE that
    rewrites every annotation leaves the count untouched.  Hashing the ordered
    rows costs the same and detects a value change as well as a row change.
    """
    import hashlib

    with sqlite3.connect(db_path) as connection:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        snapshot = {}
        for name in names:
            quoted = '"{}"'.format(name.replace('"', '""'))
            rows = connection.execute("SELECT * FROM {}".format(quoted)).fetchall()
            digest = hashlib.sha256(
                repr(sorted(map(repr, rows))).encode("utf-8")
            ).hexdigest()
            snapshot[name] = (len(rows), digest)
        return snapshot



def _seed_every_empty_table(db_path, skip):
    """Give every table at least one row so the untouched-tables check can bite.

    A downgrade that promises to leave annotations, shelves, progress and the
    Kobo tables alone cannot be tested against a fixture where those tables are
    empty: an empty table compares equal to itself no matter what the code did,
    whether the comparison is on counts or on contents.  Seeding generically
    (rather than hand-listing tables) keeps the guarantee honest as the schema
    grows, because a table added next year is seeded too.

    Returns ``(seeded, unseedable)``.  The caller asserts ``unseedable`` is
    empty: a table this helper cannot synthesise is a silent hole, and
    swallowing it would restore exactly the vacuum this exists to remove.
    """
    placeholder = {"INTEGER": 1, "REAL": 1.0, "BLOB": b"x"}
    seeded, unseedable = [], []
    with sqlite3.connect(db_path) as connection:
        # Deliberate, and load-bearing: these sentinel rows carry invented
        # parent ids, so with enforcement on most of them would be rejected and
        # silently swallowed -- quietly restoring the vacuum this helper exists
        # to remove.  The product never enables this pragma either, so off is
        # also the faithful setting.  Stated explicitly because it is a
        # per-connection default that varies by SQLite build.
        connection.execute("PRAGMA foreign_keys = OFF")
        tables = {
            name: sql or ""
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        for name, ddl in tables.items():
            if name in skip:
                continue
            quoted = '"{}"'.format(name.replace('"', '""'))
            if connection.execute("SELECT count(*) FROM {}".format(quoted)).fetchone()[0]:
                continue
            info = list(connection.execute("PRAGMA table_info({})".format(quoted)))
            pk_count = sum(1 for row in info if row[5])
            # A CHECK rejects a generic placeholder, so the acceptable value is
            # read straight out of the table's own DDL.  Two shapes cover every
            # constrained column in this schema:
            #   CHECK (status IN ('pending', 'done'))   -> take the first
            #   CHECK (scope = 'global' AND book_id IS NULL) -> take the literal
            # The second also satisfies cross-column CHECKs, because the branch
            # it names is the one whose other columns are nullable.
            allowed = {
                match.group(1): match.group(2).split(",")[0].strip().strip("'\"")
                for match in re.finditer(
                    r"[(\s]\"?(\w+)\"?\s+IN\s*\(([^)]*)\)", ddl, re.IGNORECASE)
            }
            for match in re.finditer(
                    r"[(\s]\"?(\w+)\"?\s*=\s*'([^']*)'", ddl):
                allowed.setdefault(match.group(1), match.group(2))
            columns, values = [], []
            for _cid, column, decl, notnull, default, pk in info:
                # Skip only a single INTEGER PRIMARY KEY (the rowid alias, which
                # SQLite fills in).  Members of a COMPOSITE key are ordinary
                # NOT NULL columns and must be supplied, or the insert fails.
                if pk and pk_count == 1 and (decl or "").upper().startswith("INTEGER"):
                    continue
                if not pk and (default is not None or not notnull):
                    continue
                kind = (decl or "").upper().split("(")[0]
                value = allowed.get(column, placeholder.get(kind, "x"))
                columns.append('"{}"'.format(column.replace('"', '""')))
                values.append(value)
            try:
                connection.execute(
                    "INSERT INTO {} ({}) VALUES ({})".format(
                        quoted, ", ".join(columns) or "rowid",
                        ", ".join("?" * len(values)) or "NULL",
                    ),
                    values,
                )
            except sqlite3.Error as error:
                unseedable.append((name, str(error)))
                continue
            seeded.append(name)
    return seeded, unseedable


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
        # Establish state through the PUBLIC capability constant.  The
        # implementation hard-codes 512/-513; if this test repeated those
        # literals it would keep passing after the bit moved, while the real
        # capability went un-stripped.
        connection.execute(
            "UPDATE user SET has_own_library = 1, user_library_seeded = 1, "
            "my_library_intro_dismissed = 1, role = role | ? WHERE id = 1",
            (constants.ROLE_BROWSE_GLOBAL,),
        )
        connection.executemany(
            "INSERT INTO user_library_book (user_id, book_id, added_at) "
            "VALUES (1, ?, '2026-01-01 00:00:00')",
            [(book_id,) for book_id in range(1, 51)],
        )
    # Without this the "nothing else moved" assertion below is vacuous: the
    # frozen fixture populates only 5 of ~58 tables, so a rollback that wiped
    # every shelf and annotation would compare 0 == 0 and pass.
    seeded, unseedable = _seed_every_empty_table(db_path, skip={"user_library_book"})
    assert {"shelf", "annotation", "book_read_link", "kobo_synced_books"} <= set(seeded), (
        "guard tables were not seeded, so the collateral-damage assertion is "
        "vacuous; seeded={} unseedable={}".format(sorted(seeded), unseedable)
    )
    # A table this helper cannot synthesise is a silent hole in the guarantee,
    # so it is surfaced rather than swallowed.
    assert not unseedable, (
        "these tables stayed empty, leaving them unguarded: {}".format(unseedable)
    )

    populated = _table_contents(db_path)
    assert populated["user_library_book"][0] == 50
    assert all(populated[name][0] for name in seeded)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM user WHERE role & ? = ?",
            (constants.ROLE_BROWSE_GLOBAL, constants.ROLE_BROWSE_GLOBAL),
        ).fetchone()[0] == 1

    # Captured before the downgrade: role with the capability bit masked out,
    # so the comparison isolates "did anything ELSE about these accounts move?"
    with sqlite3.connect(db_path) as connection:
        expected_survivors = connection.execute(
            "SELECT id, name, email, sidebar_view, locale, default_language, "
            "role & ~? FROM user ORDER BY id",
            (constants.ROLE_BROWSE_GLOBAL,),
        ).fetchall()

    engine = create_engine("sqlite:///{}".format(db_path))
    try:
        ub.rollback_user_library_schema(engine)
        after = _table_contents(db_path)
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
                "SELECT count(*) FROM user WHERE role & ? = ?",
                (constants.ROLE_BROWSE_GLOBAL, constants.ROLE_BROWSE_GLOBAL),
            ).fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM user").fetchone()[0] == 10
        assert connection.execute(
            "SELECT count(*) FROM user WHERE role & ? = ?",
            (constants.ROLE_ANONYMOUS, constants.ROLE_ANONYMOUS),
        ).fetchone()[0] == 1

        # 3. Nothing else moved.  Annotations, shelves, reading progress and
        #    every Kobo table are explicitly out of scope for the downgrade.
        # Contents, not counts: an UPDATE that rewrote every annotation would
        # leave the counts identical.
        # Contents, not counts: an UPDATE that rewrote every annotation would
        # leave the counts identical.  `user` is excluded here because the
        # downgrade genuinely owns part of it (three columns and one role bit);
        # what it must NOT touch there is asserted column-by-column below.
        owned = {"user_library_book", "user"}
        changed = {
            name: (populated[name], after.get(name))
            for name in set(populated) - owned
            if populated[name] != after.get(name)
        }
        assert not changed, "rollback altered tables it does not own: {}".format(changed)

        # Everything on `user` except the three dropped columns and the single
        # role bit must survive byte-for-byte, including accumulated permission
        # bits, legacy sidebar masks and non-English locales.
        with sqlite3.connect(db_path) as connection:
            survivors = connection.execute(
                "SELECT id, name, email, sidebar_view, locale, default_language, "
                "role & ~? FROM user ORDER BY id",
                (constants.ROLE_BROWSE_GLOBAL,),
            ).fetchall()
        assert survivors == expected_survivors, (
            "the downgrade changed user data outside its own three columns and "
            "the capability bit"
        )

        # 4. Running it twice from the completed state is a no-op, not an error.
        ub.rollback_user_library_schema(engine)
        assert _table_contents(db_path) == after

        # 4b. Each column is dropped in its OWN transaction, so a crash can
        #     leave a PARTIALLY downgraded schema.  "Idempotent" has to mean
        #     "finishes from wherever it stopped", not just "no-op when done".
        #     Every partial combination is exercised, not a representative one.
        for present in itertools.chain.from_iterable(
                itertools.combinations(
                    ("has_own_library", "user_library_seeded",
                     "my_library_intro_dismissed"), size)
                for size in (1, 2, 3)):
            with sqlite3.connect(db_path) as connection:
                for column in present:
                    connection.execute(
                        "ALTER TABLE user ADD COLUMN {} INTEGER".format(column)
                    )
            ub.rollback_user_library_schema(engine)
            recovered = _actual_schema(db_path)
            assert {
                "has_own_library",
                "user_library_seeded",
                "my_library_intro_dismissed",
            }.isdisjoint(recovered["user"]), (
                "rollback did not finish from the partial state {}".format(present)
            )
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
