# SPDX-License-Identifier: GPL-3.0-or-later
"""Rebuild the frozen populated app.db used by the migration boot gate.

This is a provenance tool, not part of the test run.  The fixture is created
from the product's current SQLAlchemy models, populated through those models,
and then downgraded with the shipped ``rollback_user_library_schema`` hook.
The resulting database is intentionally frozen: regenerating it after a new
migration lands would add that migration's schema to the starting point and
erase the coverage the fixture exists to provide.

Run this only when deliberately advancing the oldest supported upgrade
boundary, then review the accompanying gate and its documented coverage.
"""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
from pathlib import Path
import sqlite3
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cps import config_sql, constants, ub  # noqa: E402
from cps.progress_syncing import models as _progress_models  # noqa: E402,F401


OUTPUT = Path(__file__).with_name("populated_pre_1939.sqlite.gz")
FIXTURE_TIME = datetime(2026, 8, 28, tzinfo=timezone.utc)


def _build_product_database(db_path: Path) -> None:
    engine = create_engine("sqlite:///{}".format(db_path))
    ub.Base.metadata.create_all(engine)
    config_sql._Base.metadata.create_all(engine)

    session = sessionmaker(bind=engine)()
    try:
        users = []
        for index in range(9):
            is_admin = index < 5
            user = ub.User(
                name=("migration-admin-{}" if is_admin else "migration-user-{}").format(index),
                email="migration-{}@example.invalid".format(index),
                password="x",
                role=constants.ROLE_ADMIN if is_admin else constants.ROLE_USER,
                sidebar_view=0,
                locale="en",
                default_language="all",
                view_settings={},
            )
            session.add(user)
            users.append(user)

        session.add(ub.Registration(domain="%.%", allow=1))
        session.add(
            config_sql._Settings(
                config_kobo_sync_magic_shelves=False,
                config_upload_formats=",".join(sorted(constants.EXTENSIONS_UPLOAD)),
                config_logfile="/config/calibre-web.log",
                config_access_logfile="/config/access.log",
            )
        )
        session.flush()
        session.add(
            ub.MagicShelf(
                uuid="00000000-0000-0000-0000-000000001939",
                name="Fixture Kobo shelf",
                user_id=users[0].id,
                is_system=False,
                rules={},
                kobo_sync=True,
                created=FIXTURE_TIME,
                last_modified=FIXTURE_TIME,
            )
        )
        session.add(
            ub.User_Sessions(
                user_id=users[0].id,
                session_key="fixture-session",
                random="fixture-random",
                expiry=1,
            )
        )
        session.commit()
    finally:
        session.close()

    # Product-owned downgrade: this gives us a genuine pre-#1939 schema
    # without hand-maintaining a second CREATE TABLE definition.
    ub.rollback_user_library_schema(engine)

    # Seed legacy values that require data migrations on the next boot.  Raw
    # DML is intentional here: the current User mapping can no longer load the
    # downgraded table after the product rollback removed mapped columns.
    with engine.begin() as connection:
        connection.execute(text("UPDATE user SET sidebar_view = 0"))
        connection.execute(text("UPDATE user SET view_settings = NULL WHERE id <= 3"))
        connection.execute(
            text("UPDATE settings SET config_kobo_sync_magic_shelves = 0")
        )
    engine.dispose()

    with sqlite3.connect(db_path) as connection:
        connection.execute("VACUUM")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cwa-populated-app-db-") as temp_dir:
        db_path = Path(temp_dir) / "app.db"
        _build_product_database(db_path)
        with OUTPUT.open("wb") as raw_output:
            # Suppress timestamp and source filename in the gzip envelope.
            # SQLite may still order equivalent index DDL differently across
            # processes, so logical schema/data—not compressed bytes—is the
            # reproducibility contract.
            with gzip.GzipFile(fileobj=raw_output, mode="wb", mtime=0) as compressed:
                compressed.write(db_path.read_bytes())
    print(OUTPUT)


if __name__ == "__main__":
    main()
