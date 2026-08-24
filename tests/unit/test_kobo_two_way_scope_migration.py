# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Idempotency and defaults for user.kobo_two_way_annotation_scope.

WHY: the SPA's "Selected books" scope rides on one additive per-user column.
It must arrive on every database shape the app can boot from — a fresh
create_all, a legacy table that pre-dates the column, a partial table where
the column exists but rows are NULL, and a repeat run of the migration —
without error and without clobbering an existing preference. POSITIVE
CONTROL: a stored 'selected' value must survive every run; only NULL may be
rewritten, and only to the safe default 'all'.
"""

import pytest
from sqlalchemy import create_engine, text


def _legacy_user_table(conn):
    """A user table as it existed before the scope column (gate columns only)."""
    conn.execute(text(
        "CREATE TABLE user (id INTEGER PRIMARY KEY, name VARCHAR(64), "
        "kobo_two_way_annotation_sync BOOLEAN NOT NULL DEFAULT 0)"
    ))
    conn.execute(text("INSERT INTO user (id, name) VALUES (7, 'reader')"))
    conn.execute(text("INSERT INTO user (id, name) VALUES (8, 'picker')"))


def _columns(engine, table):
    with engine.connect() as conn:
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}


def _scope_by_id(engine):
    with engine.connect() as conn:
        return dict(conn.execute(text(
            "SELECT id, kobo_two_way_annotation_scope FROM user"
        )).all())


@pytest.mark.unit
def test_scope_column_declared_on_model_and_fresh_create_all():
    from cps import ub

    column = ub.User.__table__.c.kobo_two_way_annotation_scope
    assert column.server_default.arg.text == "'all'"
    assert column.nullable is False

    engine = create_engine("sqlite://")
    ub.Base.metadata.create_all(engine, tables=[ub.User.__table__])
    assert "kobo_two_way_annotation_scope" in _columns(engine, "user")


@pytest.mark.unit
def test_scope_column_added_to_legacy_and_backfilled():
    from cps import ub

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _legacy_user_table(conn)
    ub._ensure_kobo_two_way_gate_columns(engine)
    assert "kobo_two_way_annotation_scope" in _columns(engine, "user")
    assert _scope_by_id(engine) == {7: "all", 8: "all"}


@pytest.mark.unit
def test_scope_migration_repeat_run_preserves_selected():
    from cps import ub

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _legacy_user_table(conn)
    ub._ensure_kobo_two_way_gate_columns(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE user SET kobo_two_way_annotation_scope='selected' WHERE id=8"
        ))
    ub._ensure_kobo_two_way_gate_columns(engine)
    ub._ensure_kobo_two_way_gate_columns(engine)
    assert _scope_by_id(engine) == {7: "all", 8: "selected"}


@pytest.mark.unit
def test_scope_nulls_healed_to_all():
    from cps import ub

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _legacy_user_table(conn)
        # A partial/older path that added the column WITHOUT the default
        # leaves NULLs; the healer must rewrite exactly those to 'all'.
        conn.execute(text(
            "ALTER TABLE user ADD COLUMN kobo_two_way_annotation_scope VARCHAR(16)"
        ))
        conn.execute(text(
            "UPDATE user SET kobo_two_way_annotation_scope='selected' WHERE id=8"
        ))
    ub._ensure_kobo_two_way_gate_columns(engine)
    assert _scope_by_id(engine) == {7: "all", 8: "selected"}


@pytest.mark.unit
def test_scope_migration_runs_without_user_or_settings_table():
    from cps import ub

    engine = create_engine("sqlite://")
    ub._ensure_kobo_two_way_gate_columns(engine)
    ub._ensure_kobo_two_way_gate_columns(engine)
