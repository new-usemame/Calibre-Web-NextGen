# SPDX-License-Identifier: GPL-3.0-or-later
"""Additive, provenance-aware app.db acquisition migration.

Run before any metadata.create_all or personal-library migration: legacy Store
used bit9 for access, whereas current accounts use it for Global Library.
No provider, key, credential, job or background service is activated here.
"""
import json
import time

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, inspect, select

from ... import constants
from .storage import define_tables

VERSION = 1
LEGACY_ACCESS = 1 << 9
LEGACY_AUTO_APPROVE = 1 << 10
LEGACY_TABLE_COLUMNS = {
    'store_credentials': {'id', 'user_id', 'provider', 'ciphertext', 'nonce', 'key_version'},
    'store_request_mappings': {'id', 'user_id', 'shelfmark_request_id', 'work', 'release'},
    'store_download_mappings': {'id', 'user_id', 'source', 'source_id', 'title', 'format'},
}
ROLE_COLUMNS = (('user', 'role'), ('settings', 'config_default_role'),
                ('oauthProvider', 'oauth_default_role'))


def define_migration_table(metadata):
    existing = metadata.tables.get('acquisition_schema_migration')
    if existing is not None:
        return existing
    return Table('acquisition_schema_migration', metadata,
        Column('version', Integer, primary_key=True),
        Column('source_layout', String(32), nullable=False),
        Column('status', String(32), nullable=False),
        Column('source_schema_json', Text, nullable=False),
        Column('role_changes_json', Text, nullable=False),
        Column('completed_at', Float, nullable=False))


def _classify(schema):
    present = set(schema)
    store_present = present.intersection(LEGACY_TABLE_COLUMNS)
    complete_store = all(columns <= schema.get(table, set())
                         for table, columns in LEGACY_TABLE_COLUMNS.items())
    personal_column = 'has_own_library' in schema.get('user', set())
    personal_table = 'user_library_book' in present
    if store_present:
        if complete_store and not personal_column and not personal_table and {'id', 'role'} <= schema.get('user', set()):
            return 'legacy_store', 'mapped'
        return 'hybrid', 'needs_review'
    if personal_column != personal_table:
        return 'partial', 'needs_review'
    if personal_column:
        return 'audit', 'preserved'
    return ('pre_personal', 'preserved') if 'user' in present else ('fresh', 'preserved')


def _remap(mask):
    if mask is None:
        return None
    return ((mask & ~(LEGACY_ACCESS | LEGACY_AUTO_APPROVE))
            | (constants.ROLE_ACQUISITION_ACCESS if mask & LEGACY_ACCESS else 0)
            | (constants.ROLE_ACQUISITION_AUTO_APPROVE if mask & LEGACY_AUTO_APPROVE else 0))


def migrate_acquisition_schema(engine, metadata=None):
    """Capture source and remap only proven Store masks in one transaction.

    Return durable status for admin/runtime diagnostics. Failed capture/remap
    propagates to the caller: boot must not erase provenance and continue.
    SQLite's explicit write transaction includes DDL under legacy sqlite3 mode.
    """
    metadata = metadata if metadata is not None else MetaData()
    tables = define_tables(metadata)
    marker = define_migration_table(metadata)
    with engine.begin() as conn:
        if conn.dialect.name == 'sqlite' and not conn.connection.driver_connection.in_transaction:
            conn.exec_driver_sql('BEGIN IMMEDIATE')
        inspector = inspect(conn)
        table_names = inspector.get_table_names()
        if marker.name in table_names:
            recorded = conn.execute(select(marker)).mappings().all()
            if recorded:
                if len(recorded) != 1 or recorded[0]['version'] != VERSION:
                    raise RuntimeError('Unsupported acquisition migration version; refusing role reinterpretation')
                return dict(recorded[0])
        # Capture only shape, never credential values or legacy request payloads.
        schema = {name: {column['name'] for column in inspector.get_columns(name)}
                  for name in table_names}
        layout, status = _classify(schema)
        changes = []
        for table_name, column_name in ROLE_COLUMNS:
            columns = schema.get(table_name, set())
            if column_name not in columns:
                continue
            if 'id' not in columns:
                raise RuntimeError('Role template lacks an identity; refusing ambiguous migration')
            # Identifiers are fixed local constants; values remain parameters.
            for identity, before in conn.exec_driver_sql(
                    f'SELECT id, "{column_name}" FROM "{table_name}" ORDER BY id'):
                if before is not None and (not isinstance(before, int) or before < 0):
                    raise RuntimeError('Invalid stored role mask; refusing role reinterpretation')
                after = _remap(before) if layout == 'legacy_store' else before
                changes.append({'table': table_name, 'id': identity,
                                'column': column_name, 'before': before, 'after': after})
                if after != before:
                    conn.exec_driver_sql(f'UPDATE "{table_name}" SET "{column_name}"=? WHERE id=?',
                                         (after, identity))
        # Upgrade settings now as well as via config_sql's fresh-install model.
        # Preserve an explicit setting on subsequent boots; no automatic enables.
        if 'settings' in schema and 'config_acquisition_enabled' not in schema['settings']:
            conn.exec_driver_sql('ALTER TABLE settings ADD COLUMN config_acquisition_enabled BOOLEAN NOT NULL DEFAULT 0')
        owned = [tables.connections, tables.offers, tables.jobs, tables.receipts, marker]
        metadata.create_all(conn, tables=owned, checkfirst=True)
        for table in owned:
            for index in table.indexes:
                index.create(conn, checkfirst=True)
        values = dict(version=VERSION, source_layout=layout, status=status,
                      source_schema_json=json.dumps({k: sorted(v) for k,v in schema.items()}, sort_keys=True),
                      role_changes_json=json.dumps(changes, sort_keys=True), completed_at=time.time())
        conn.execute(marker.insert().values(**values))
        return values
