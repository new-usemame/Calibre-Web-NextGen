# SPDX-License-Identifier: GPL-3.0-or-later
"""Upgrade regressions for issue #1939's populated pre-feature schema."""

import ast
import logging
from pathlib import Path
import re
import sqlite3

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from cps import config_sql, constants, ub


pytestmark = pytest.mark.unit

UB_SOURCE = Path(ub.__file__)


def _is_bulk_dml_query(query_call, parents, function):
    """Return whether query(Model) feeds update/delete instead of loading it."""
    current = query_call
    while current is not function:
        current = parents.get(current)
        if current is None:
            break
        if isinstance(current, ast.Attribute) and current.attr in {"update", "delete"}:
            return True
    return False


def test_additive_columns_precede_full_model_loads_in_migration_functions():
    """A mapped entity load selects every declared column, even unused ones."""
    tree = ast.parse(UB_SOURCE.read_text(encoding="utf-8"))
    model_tables = {
        mapper.class_.__name__: mapper.local_table.name
        for mapper in ub.Base.registry.mappers
    }
    alter_table = re.compile(
        r"\bALTER\s+TABLE\s+[`\"'\[]?([A-Za-z_]\w*)[^;]*?"
        r"\bADD\s+(?:COLUMN\s+)?",
        re.IGNORECASE,
    )
    violations = []

    for function in (
            node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("migrate_")):
        parents = {
            child: parent
            for parent in ast.walk(function)
            for child in ast.iter_child_nodes(parent)
        }
        additive_lines = {}
        for node in ast.walk(function):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            match = alter_table.search(node.value)
            if match:
                additive_lines.setdefault(match.group(1).lower(), []).append(node.lineno)

        for node in ast.walk(function):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "query"
                and node.args
                and isinstance(node.args[0], ast.Name)
            ):
                continue
            model_name = node.args[0].id
            table_name = model_tables.get(model_name)
            if table_name is None or _is_bulk_dml_query(node, parents, function):
                continue
            later_adds = [
                line for line in additive_lines.get(table_name.lower(), [])
                if line > node.lineno
            ]
            if later_adds:
                violations.append(
                    "{} loads {} at line {} before additive {} ALTER(s) at {}".format(
                        function.name, model_name, node.lineno, table_name, later_adds,
                    )
                )

    assert not violations, "\n".join(violations)


def _build_populated_pre_feature_db(db_path):
    """Create current app.db data, then apply the supported #1939 rollback."""
    engine = create_engine("sqlite:///{}".format(db_path))
    ub.Base.metadata.create_all(engine)
    config_sql._Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        for index in range(9):
            is_admin = index < 5
            session.add(ub.User(
                name="migration-admin-{}".format(index) if is_admin
                else "migration-user-{}".format(index),
                email="migration-{}@example.invalid".format(index),
                password="x",
                role=constants.ROLE_ADMIN if is_admin else constants.ROLE_USER,
                sidebar_view=0,
                locale="en",
                default_language="all",
            ))
        session.commit()
    finally:
        session.close()

    ub.rollback_user_library_schema(engine)
    schema = inspect(engine)
    assert {
        "has_own_library",
        "user_library_seeded",
        "my_library_intro_dismissed",
    }.isdisjoint({column["name"] for column in schema.get_columns("user")})
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM user").scalar_one() == 9
    engine.dispose()


def test_one_boot_migrates_populated_pre_feature_users_without_schema_errors(
        tmp_path, monkeypatch, caplog, capsys):
    db_path = tmp_path / "app.db"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _build_populated_pre_feature_db(db_path)

    previous_session = ub.session
    previous_app_db_path = ub.app_DB_path
    monkeypatch.setattr(constants, "CONFIG_DIR", str(config_dir))
    try:
        with caplog.at_level(logging.DEBUG):
            ub.init_db(str(db_path))
    finally:
        if ub.session is not previous_session:
            ub.session.close()
            ub.session.bind.dispose()
        ub.session = previous_session
        ub.app_DB_path = previous_app_db_path

    captured = capsys.readouterr()
    formatter = logging.Formatter()
    migration_output = "\n".join([
        captured.out,
        captured.err,
        *(formatter.format(record) for record in caplog.records),
    ]).lower()
    assert "operationalerror" not in migration_output
    assert "no such column" not in migration_output

    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT role, sidebar_view FROM user ORDER BY id"
        ).fetchall()
    assert len(rows) == 9
    assert all(sidebar & constants.SIDEBAR_FAVORITES for _role, sidebar in rows)
    assert [
        bool(sidebar & constants.SIDEBAR_DUPLICATES)
        for _role, sidebar in rows
    ] == [
        bool(role & constants.ROLE_ADMIN)
        for role, _sidebar in rows
    ]

    assert (config_dir / ".cwa_migrations" / "favorites_sidebar_v1").is_file()
    assert (config_dir / ".cwa_migrations" / "duplicates_sidebar_v1").is_file()
