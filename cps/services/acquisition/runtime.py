# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit repository lifetime for web workers and the standalone ingest service.

Opening a repository never migrates a database or changes its journal mode.
Only initial administrator connection setup may request key creation; ingestion
must recover with the original persisted key, even when acquisition is paused.
"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from sqlalchemy import MetaData, create_engine, inspect, select
from sqlalchemy.pool import NullPool

from .secrets import SecretBox, load_or_create_key
from .storage import Repository, StorageError, define_tables


@contextmanager
def open_repository(app_db_path, *, initialize_key=False):
    path = Path(app_db_path).absolute()
    if not path.is_file():
        raise StorageError("Acquisition database is unavailable")
    # URI mode=rw prevents a path typo or concurrently removed DB from creating
    # a second empty database. URI escaping also handles deployment path spaces.
    uri = path.as_uri() + "?mode=rw"

    def connect():
        connection = sqlite3.connect(uri, uri=True, timeout=30)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    engine = create_engine("sqlite://", creator=connect, poolclass=NullPool,
                           hide_parameters=True)
    try:
        tables = define_tables(MetaData())
        with engine.connect() as conn:
            inspector = inspect(conn)
            existing = set(inspector.get_table_names())
            for table in (tables.connections, tables.offers, tables.jobs, tables.receipts, tables.manifests):
                if table.name not in existing or not set(table.c.keys()).issubset(
                        column["name"] for column in inspector.get_columns(table.name)):
                    raise StorageError("Acquisition database needs an application upgrade")
            encrypted_rows = any(conn.execute(select(table.c.id).limit(1)).first()
                                 for table in (tables.connections, tables.offers))
        key = load_or_create_key(path.parent / "acquisition.key",
                                 allow_create=initialize_key and not encrypted_rows)
        yield Repository(engine, tables, SecretBox(key))
    finally:
        engine.dispose()


@contextmanager
def open_ingest_repository(app_db_path):
    """Acknowledge a previously authorized publication; never create its key."""
    with open_repository(app_db_path) as repository:
        yield repository


def drain_acquisition_jobs():
    """Existing APScheduler callback; one claim and bounded transfer per run."""
    from ... import ub, logger
    from ...cwa_functions import get_ingest_dir
    from ...api.acquisition import worker_available
    from . import admission
    from .worker import AcquisitionWorker
    if not ub.app_DB_path or not admission.instance_enabled(ub.app_DB_path):
        return
    try:
        if not worker_available()['available']:
            return
        with open_repository(ub.app_DB_path) as repository:
            worker = AcquisitionWorker(repository,
                Path(ub.app_DB_path).parent / 'acquisition-staging', get_ingest_dir(),
                allowed=lambda owner: admission.account_allowed(repository.engine, owner),
                execution_allowed=lambda job: admission.job_allowed(repository.engine, job.id, job.owner_id),
                media_allowed=lambda media: admission.format_allowed(repository.engine, media),
                enabled=lambda: admission.instance_enabled(repository.engine))
            worker.run_once()
    except Exception:
        # Decrypted adapter payloads and dependency errors can carry credentials.
        # Persisted jobs/leases remain the recovery authority after this failure.
        logger.create().warning('Acquisition worker could not complete its current step; durable request retained')
