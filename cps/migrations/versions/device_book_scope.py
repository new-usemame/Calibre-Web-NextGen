# SPDX-License-Identifier: GPL-3.0-or-later
"""Nullable per-device scope; NULL preserves existing account behaviour.

Revision ID: device_book_scope_a
Revises: None (CWNG's existing schema uses boot migrations, not an Alembic chain)

Alembic can call the standard no-argument entry points. CWNG's boot upgrader
passes its connection to the same revision, so Alembic is not a runtime
dependency. Downgrade removes only the choice, never any reading data.
"""
from sqlalchemy import inspect, text

revision = "device_book_scope_a"
down_revision = None
branch_labels = None
depends_on = None


def _bind(connection):
    if connection is not None:
        return connection
    from alembic import op
    return op.get_bind()


def upgrade(connection=None):
    connection = _bind(connection)
    columns = {c["name"] for c in inspect(connection).get_columns("device")}
    if "book_scope" not in columns:
        connection.execute(text("ALTER TABLE device ADD COLUMN book_scope TEXT"))


def downgrade(connection=None):
    connection = _bind(connection)
    columns = {c["name"] for c in inspect(connection).get_columns("device")}
    if "book_scope" in columns:
        connection.execute(text("ALTER TABLE device DROP COLUMN book_scope"))
