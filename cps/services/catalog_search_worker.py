"""Local #2971 experiment. Offload catalog ID selection on file-backed libraries."""
import os
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from ..db import _register_sqlite_udfs
from ..sqlite_utils import network_share_mode_enabled


class SearchBusy(Exception):
    pass


@dataclass(frozen=True)
class SearchPage:
    total: int
    ids: list[int]
    metadata_changed: bool
    check_source: Callable[[], None]

    @contextmanager
    def validate_source(self):
        self.check_source()
        try:
            yield
        finally:
            self.check_source()


# Give an already-admitted search a short chance to finish during bursts.
_ADMISSION_WAIT_SECONDS = 0.25
_pool = None
_admission = None


def _read(paths, count, page, combined):
    # The production engine uses StaticPool. Borrowing it here would still
    # block unrelated reads on its serialized SQLite connection.
    engine = create_engine("sqlite:///file::memory:?uri=true", poolclass=NullPool)
    try:
        with engine.connect() as conn:
            _register_sqlite_udfs(conn.connection, None)
            for schema in ("calibre", "app_settings"):
                conn.exec_driver_sql("ATTACH DATABASE ? AS " + schema,
                                     (Path(paths[schema]).as_uri() + "?mode=ro",))
            conn.exec_driver_sql("PRAGMA query_only = ON")
            conn.exec_driver_sql("BEGIN")
            if combined is not None:
                rows = conn.execute(combined).all()
                if rows:
                    return rows[0][1], [row[0] for row in rows]
                # An empty page beyond the end still owes the exact total.
                return conn.execute(count).scalar_one(), []
            return conn.execute(count).scalar_one(), conn.execute(page).scalars().all()
    finally:
        engine.dispose()


def _file_identity(path):
    try:
        info = os.stat(path)
    except OSError as exc:
        raise SearchBusy from exc
    return (info.st_dev, info.st_ino) if info.st_ino else None


def _metadata_version(connection):
    # data_version sees other connections' commits. total_changes also covers
    # writes through CalibreDB's shared SQLite connection.
    return (connection.connection.driver_connection,
            connection.exec_driver_sql("PRAGMA calibre.data_version").scalar_one(),
            connection.exec_driver_sql("SELECT total_changes()").scalar_one())


def select_page(session, count, page, environ, *, combined=None, calibre_db=None, watch_metadata=False):
    try:
        from gevent.lock import BoundedSemaphore
        from gevent.pywsgi import Input
        from gevent.threadpool import ThreadPool
    except ImportError:
        return None

    if not isinstance(environ.get("wsgi.input"), Input) or network_share_mode_enabled():
        return None
    bind = session.bind
    catalog_engine = calibre_db.engine if calibre_db is not None else None
    catalog_factory = calibre_db.session_factory if calibre_db is not None else None
    connection = session.connection()
    engine = connection.engine
    pool = engine.pool
    paths = {name: path for _, name, path in connection.exec_driver_sql("PRAGMA database_list")}
    if not paths.get("calibre") or not paths.get("app_settings"):
        return None

    identities = {paths[schema]: _file_identity(paths[schema]) for schema in ("calibre", "app_settings")}
    if None in identities.values():
        return None

    if calibre_db is not None:
        opened_path = calibre_db._metadata_db_path
        if (catalog_engine is engine or (opened_path and
                os.path.realpath(opened_path) == os.path.realpath(paths["calibre"]))):
            opened_identity = calibre_db._metadata_db_identity
            if not opened_identity or not opened_identity[1]:
                return None
            # PRAGMA database_list reports a filename, even if the live SQLite
            # handle still has the previous inode open after an atomic rename.
            if ((catalog_engine is not None and catalog_engine is not engine)
                    or identities[paths["calibre"]] != opened_identity):
                raise SearchBusy
            if opened_path:
                identities[opened_path] = opened_identity

    def check_source():
        if (session.bind is not bind or engine.pool is not pool
                or (calibre_db is not None and (
                    calibre_db.session is not session or calibre_db.engine is not catalog_engine
                    or calibre_db.session_factory is not catalog_factory))):
            raise SearchBusy
        if any(_file_identity(path) != identity for path, identity in identities.items()):
            raise SearchBusy

    check_source()
    metadata_version = _metadata_version(connection) if watch_metadata else None
    global _pool, _admission
    if _pool is None:
        workers = int(os.environ.get("CWNG_EXPERIMENTAL_SEARCH_THREADS", "1"))
        admitted = int(os.environ.get("CWNG_EXPERIMENTAL_SEARCH_ADMISSION", "8"))
        if not 1 <= workers <= admitted <= 32:
            raise ValueError("Expected 1 <= search threads <= search admission <= 32")
        _pool = ThreadPool(workers)
        _admission = BoundedSemaphore(admitted)
    if not _admission.acquire(timeout=_ADMISSION_WAIT_SECONDS):
        raise SearchBusy
    try:
        check_source()
        try:
            total, ids = _pool.apply(_read, (paths, count, page, combined))
            metadata_changed = watch_metadata and _metadata_version(session.connection()) != metadata_version
            return SearchPage(total, ids, metadata_changed, check_source)
        finally:
            # Also check when an ATTACH or query fails during a replacement.
            check_source()
    finally:
        _admission.release()
