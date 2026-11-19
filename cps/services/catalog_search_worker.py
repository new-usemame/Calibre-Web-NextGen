"""Local #2971 experiment. Offload catalog ID selection on file-backed libraries."""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from ..db import _register_sqlite_udfs


class SearchBusy(Exception):
    pass


_pool = None
_admission = None


def _read(paths, count, page):
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
            return conn.execute(count).scalar_one(), conn.execute(page).scalars().all()
    finally:
        engine.dispose()


def select_page(session, count, page, environ):
    try:
        from gevent.lock import BoundedSemaphore
        from gevent.pywsgi import Input
        from gevent.threadpool import ThreadPool
    except ImportError:
        return None

    if not isinstance(environ.get("wsgi.input"), Input):
        return None
    paths = {name: path for _, name, path in
             session.connection().exec_driver_sql("PRAGMA database_list")}
    if not paths.get("calibre") or not paths.get("app_settings"):
        return None

    global _pool, _admission
    if _pool is None:
        _pool = ThreadPool(1)
        _admission = BoundedSemaphore(4)
    if not _admission.acquire(blocking=False):
        raise SearchBusy
    try:
        return _pool.apply(_read, (paths, count, page))
    finally:
        _admission.release()
