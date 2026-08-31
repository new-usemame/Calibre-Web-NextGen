# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for #1873's uncontained app.db SAVEPOINT."""

from __future__ import annotations

import ast
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from cps import ub
from cps.services.annotation_sync import (
    dispatch_existing_annotation_sync,
    register_handler,
    reset_registry_for_testing,
    set_remote_enqueue,
)
from cps.services.annotation_sync.base import AnnotationSyncTargetHandler, SyncResult


class _StubHandler(AnnotationSyncTargetHandler):
    target_name = "stub"

    def is_enabled(self, user):
        return True

    def push(self, annotation, book, user, payload=None):
        return SyncResult(status="synced", target_record_id="remote-1873")

    def delete(self, sync_target, user):
        return SyncResult(status="tombstone")


class _BeginNestedCallVisitor(ast.NodeVisitor):
    """Collect the function containing each direct ``begin_nested`` call."""

    def __init__(self):
        self.function_names = []
        self._function_stack = []

    def visit_FunctionDef(self, node):
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "begin_nested":
            self.function_names.append(self._function_stack[-1] if self._function_stack else None)
        self.generic_visit(node)


@pytest.fixture(autouse=True)
def _reset_annotation_sync_registry():
    reset_registry_for_testing()
    set_remote_enqueue(None)
    yield
    reset_registry_for_testing()
    set_remote_enqueue(None)


@pytest.fixture
def file_backed_app_db(tmp_path):
    """Initialize the real app.db engine and restore the module globals after."""
    previous_session = ub.session
    previous_path = ub.app_DB_path
    db_path = tmp_path / "app.db"

    ub.init_db(str(db_path))
    test_session = ub.session
    try:
        yield db_path, test_session
    finally:
        engine = test_session.get_bind()
        test_session.close()
        engine.dispose()
        ub.session = previous_session
        ub.app_DB_path = previous_path


@pytest.mark.unit
def test_existing_annotation_sync_savepoint_rolls_back_with_failed_commit(
    file_backed_app_db, monkeypatch,
):
    """The web-reader/KOReader create arm must not commit at SAVEPOINT release.

    ``dispatch_annotation_sync`` (the Kobo PATCH entry point) flushes an
    Annotation before it creates the sync-target row. That DML happens to make
    sqlite3's legacy transaction mode contain its SAVEPOINT, so testing that
    caller passes with or without the fix. ``dispatch_existing_annotation_sync``
    reaches the same create arm after SELECTs only and is the discriminating
    real caller.

    Seed through a separate sqlite3 connection so this SQLAlchemy connection
    has emitted no DML before the dispatcher opens ``begin_nested()``. Then
    model a failed outer commit by rolling back, and verify durability through
    another independent connection rather than the session under test.
    """
    db_path, session = file_backed_app_db
    session.rollback()

    with sqlite3.connect(db_path) as seed:
        user_id = seed.execute(
            "SELECT id FROM user WHERE name = 'admin'"
        ).fetchone()[0]
        seed.execute(
            "INSERT INTO annotation "
            "(user_id, annotation_id, book_id, source, routing_revision, content_revision) "
            "VALUES (?, 'webreader-1873', 1873, 'webreader', 1, 1)",
            (user_id,),
        )

    user = session.query(ub.User).filter(ub.User.id == user_id).one()
    annotation = session.query(ub.Annotation).filter(
        ub.Annotation.annotation_id == "webreader-1873"
    ).one()
    register_handler(_StubHandler())

    commit_attempts = []

    def fail_outer_commit():
        commit_attempts.append(True)
        session.rollback()
        return False

    monkeypatch.setattr(ub, "session_commit", fail_outer_commit)

    dispatch_existing_annotation_sync(
        annotation,
        SimpleNamespace(id=1873, title="SAVEPOINT regression"),
        user,
    )

    assert commit_attempts == [True], "the test did not exercise the failed commit path"
    with sqlite3.connect(db_path) as observer:
        durable_targets = observer.execute(
            "SELECT target, status FROM annotation_sync_target "
            "WHERE annotation_id = ?",
            (annotation.id,),
        ).fetchall()

    assert durable_targets == [], (
        "the sync-target SAVEPOINT committed at RELEASE and survived the outer rollback"
    )


@pytest.mark.unit
def test_every_production_begin_nested_call_routes_through_containment_helper():
    """A new direct SAVEPOINT caller must not silently lose containment."""
    cps_root = Path(ub.__file__).resolve().parent
    calls = []
    for source_path in cps_root.rglob("*.py"):
        visitor = _BeginNestedCallVisitor()
        visitor.visit(ast.parse(source_path.read_text(encoding="utf-8")))
        calls.extend(
            (source_path.relative_to(cps_root).as_posix(), function_name)
            for function_name in visitor.function_names
        )

    assert calls == [("ub.py", "begin_contained_nested")]


@pytest.mark.unit
def test_every_app_db_session_uses_legacy_transactions_and_factory_keeps_timeout(
    file_backed_app_db,
):
    """All app.db constructors share WAL without changing driver transactions."""
    _db_path, main_session = file_backed_app_db
    thread_session = ub.init_db_thread()
    ad_hoc_session = ub.get_new_session_instance()
    sessions = {
        "init_db": main_session,
        "init_db_thread": thread_session,
        "get_new_session_instance": ad_hoc_session,
    }

    try:
        for constructor, session in sessions.items():
            connection = session.connection()
            driver_connection = connection.connection.driver_connection
            assert driver_connection.isolation_level is not None, constructor
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
            assert driver_connection.in_transaction is False, constructor
            session.rollback()

        # Startup migrations temporarily lower busy_timeout on the pooled main
        # connection for their own retry loop. Check a fresh factory connection
        # so this pins the connect_args=30 contract rather than that unrelated,
        # pre-existing migration policy.
        timeout_engine = ub._create_app_db_engine(_db_path)
        try:
            with timeout_engine.connect() as connection:
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30_000
        finally:
            timeout_engine.dispose()
    finally:
        thread_engine = thread_session.get_bind()
        thread_session.close()
        thread_engine.dispose()
        ad_hoc_engine = ad_hoc_session.get_bind()
        ad_hoc_session.remove()
        ad_hoc_engine.dispose()


@pytest.mark.unit
def test_startup_read_session_does_not_block_backfill_on_second_pool_connection(
    file_backed_app_db,
):
    """Match create_app's config SELECT followed by engine-based backfill."""
    _db_path, session = file_backed_app_db
    engine = session.get_bind()
    session.rollback()
    checkout_ids = []

    def record_checkout(dbapi_connection, _record, _proxy):
        checkout_ids.append(id(dbapi_connection))

    event.listen(engine, "checkout", record_checkout)
    try:
        assert session.query(ub.User).first() is not None
        driver_connection = session.connection().connection.driver_connection
        assert driver_connection.in_transaction is False

        started = time.monotonic()
        ub.backfill_annotation_content_ids(engine, lambda _book_id: None)
        elapsed = time.monotonic() - started
    finally:
        event.remove(engine, "checkout", record_checkout)

    assert elapsed < 5
    assert len(set(checkout_ids)) == 2, (
        "the test did not force backfill onto a second pooled DBAPI connection"
    )


@pytest.mark.unit
def test_read_then_write_starts_fresh_after_a_concurrent_commit(
    tmp_path,
):
    """A legacy-mode SELECT cannot leave a stale snapshot to upgrade.

    Under #1888's deferred ``BEGIN``, connection A takes a read snapshot, B
    commits, and A's INSERT fails immediately with ``database is locked``:
    SQLite cannot upgrade the stale snapshot and does not apply busy_timeout to
    SQLITE_BUSY_SNAPSHOT. Under sqlite3's legacy transaction control, A's
    SELECT owns no driver transaction. B commits first, then A starts a fresh
    write transaction and succeeds.
    """
    db_path = tmp_path / "busy-snapshot.db"
    with sqlite3.connect(db_path) as setup:
        assert setup.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        setup.execute("CREATE TABLE probe (value TEXT NOT NULL)")
        setup.execute("INSERT INTO probe VALUES ('seed')")

    engine = ub._create_app_db_engine(db_path)
    writer_started = threading.Event()
    writer_committed = threading.Event()
    writer_errors = []

    def concurrent_writer():
        try:
            with sqlite3.connect(db_path, timeout=2) as connection_b:
                writer_started.set()
                connection_b.execute("INSERT INTO probe VALUES ('connection-b')")
                connection_b.commit()
                writer_committed.set()
        except BaseException as error:  # surfaced in the test thread below
            writer_errors.append(error)

    connection_b_thread = None
    connection_a = engine.connect()
    try:
        assert connection_a.exec_driver_sql(
            "SELECT value FROM probe ORDER BY rowid"
        ).all() == [("seed",)]
        driver_connection = connection_a.connection.driver_connection
        assert driver_connection.in_transaction is False

        connection_b_thread = threading.Thread(target=concurrent_writer)
        connection_b_thread.start()
        assert writer_started.wait(timeout=1), "connection B never attempted its write"

        assert writer_committed.wait(timeout=1), (
            "connection A's read-only bookkeeping transaction blocked connection B"
        )
        connection_a.exec_driver_sql("INSERT INTO probe VALUES ('connection-a')")
        connection_a.commit()
    finally:
        connection_a.close()
        if connection_b_thread is not None:
            connection_b_thread.join(timeout=3)
        engine.dispose()

    assert writer_errors == []
    with sqlite3.connect(db_path) as observer:
        assert observer.execute(
            "SELECT value FROM probe ORDER BY rowid"
        ).fetchall() == [
            ("seed",),
            ("connection-b",),
            ("connection-a",),
        ]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("journal_mode", "contained_outer"),
    (
        pytest.param("delete", False, id="delete-legacy"),
        pytest.param("delete", True, id="delete-local-immediate"),
        pytest.param("wal", False, id="wal-legacy"),
        pytest.param("wal", True, id="wal-local-immediate"),
    ),
)
def test_sqlite_transaction_mode_four_arm_probe(
    tmp_path, journal_mode, contained_outer,
):
    """Pin legacy behavior and the local containment cost in all four arms."""
    db_path = tmp_path / "transaction-mode-probe.db"
    with sqlite3.connect(db_path) as setup:
        assert setup.execute(
            "PRAGMA journal_mode={}".format(journal_mode)
        ).fetchone() == (journal_mode,)
        setup.execute("CREATE TABLE probe (value TEXT NOT NULL)")
        setup.execute("INSERT INTO probe VALUES ('seed')")

    connection_a = sqlite3.connect(db_path, timeout=0.1)
    if contained_outer:
        connection_a.isolation_level = None
        connection_a.execute("BEGIN IMMEDIATE")
    try:
        assert connection_a.execute("SELECT value FROM probe").fetchall() == [("seed",)]
        assert connection_a.in_transaction is contained_outer

        # A reserved writer never blocks an independent reader. In WAL this is
        # the production arm; DELETE + immediate is retained as an off-policy
        # diagnostic arm so a journal-mode behavior change is visible.
        with sqlite3.connect(db_path, timeout=0.1) as independent_reader:
            assert independent_reader.execute("SELECT value FROM probe").fetchall() == [
                ("seed",),
            ]

        writer_blocked = False
        try:
            with sqlite3.connect(db_path, timeout=0.05) as connection_b:
                connection_b.execute("INSERT INTO probe VALUES ('connection-b')")
        except sqlite3.OperationalError as error:
            assert "database is locked" in str(error).lower()
            writer_blocked = True
        assert writer_blocked is contained_outer

        connection_a.execute("SAVEPOINT contained")
        connection_a.execute("INSERT INTO probe VALUES ('savepoint')")
        connection_a.execute("RELEASE SAVEPOINT contained")
        connection_a.rollback()
    finally:
        connection_a.close()

    with sqlite3.connect(db_path) as observer:
        savepoint_rows = observer.execute(
            "SELECT value FROM probe WHERE value = 'savepoint'"
        ).fetchall()
    assert savepoint_rows == ([] if contained_outer else [("savepoint",)])


@pytest.mark.unit
def test_wal_unavailable_keeps_legacy_transactions_warns_and_does_not_block_writer(
    tmp_path, monkeypatch,
):
    """A WAL-incapable engine degrades consistently and observably."""
    db_path = tmp_path / "wal-unavailable.db"
    with sqlite3.connect(db_path) as setup:
        setup.execute("CREATE TABLE probe (value TEXT NOT NULL)")
        setup.execute("INSERT INTO probe VALUES ('seed')")

    wal_requests = []

    def reject_wal(connection):
        wal_requests.append(connection)
        return "delete", None

    monkeypatch.setattr(ub, "_request_app_db_wal", reject_wal)
    warnings = []

    def capture_warning(message, *args):
        warnings.append(message % args)

    monkeypatch.setattr(ub.log, "warning", capture_warning)
    engine = ub._create_app_db_engine(db_path)

    try:
        with engine.connect() as reader:
            assert reader.exec_driver_sql("SELECT value FROM probe").scalar_one() == "seed"
            driver_connection = reader.connection.driver_connection
            assert driver_connection.isolation_level is not None
            assert driver_connection.in_transaction is False

            # Keep the reader checked out so this must create another DBAPI
            # connection. The WAL capability probe is engine-wide, not a
            # per-connection choice that could produce mixed semantics.
            with engine.connect() as sibling:
                sibling_driver = sibling.connection.driver_connection
                assert sibling_driver is not driver_connection
                assert sibling_driver.isolation_level is not None
                assert sibling.exec_driver_sql("SELECT count(*) FROM probe").scalar_one() == 1
                assert sibling_driver.in_transaction is False

            # In rollback-journal mode, a legacy SELECT releases its read lock
            # with the statement. An independent writer must not wait for this
            # SQLAlchemy Connection's bookkeeping transaction to be closed.
            with sqlite3.connect(db_path, timeout=0.25) as writer:
                writer.execute("INSERT INTO probe VALUES ('writer')")
    finally:
        engine.dispose()

    assert len(wal_requests) == 1
    assert len(warnings) == 1
    assert "WAL is unavailable" in warnings[0]
    assert "legacy sqlite3 transaction control" in warnings[0]
    assert "contained SAVEPOINTs acquire a local write transaction" in warnings[0]

    with sqlite3.connect(db_path) as observer:
        assert observer.execute("SELECT value FROM probe ORDER BY rowid").fetchall() == [
            ("seed",),
            ("writer",),
        ]


@pytest.mark.unit
def test_network_share_mode_skips_wal_uses_legacy_transactions_and_warns(
    tmp_path, monkeypatch,
):
    """NETWORK_SHARE_MODE applies to app.db even when its own path is local."""
    db_path = tmp_path / "network-share-mode.db"
    with sqlite3.connect(db_path) as setup:
        setup.execute("CREATE TABLE probe (value TEXT NOT NULL)")
        setup.execute("INSERT INTO probe VALUES ('seed')")

    monkeypatch.setenv("NETWORK_SHARE_MODE", "true")

    def unexpected_wal_request(_connection):
        raise AssertionError("NETWORK_SHARE_MODE must skip PRAGMA journal_mode=WAL")

    monkeypatch.setattr(ub, "_request_app_db_wal", unexpected_wal_request)
    warnings = []
    monkeypatch.setattr(
        ub.log,
        "warning",
        lambda message, *args: warnings.append(message % args),
    )
    engine = ub._create_app_db_engine(db_path)

    try:
        with engine.connect() as reader:
            driver_connection = reader.connection.driver_connection
            assert reader.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "delete"
            assert driver_connection.isolation_level is not None
            assert reader.exec_driver_sql("SELECT value FROM probe").scalar_one() == "seed"
            assert driver_connection.in_transaction is False

            # A second DBAPI connection must inherit the engine-wide decision
            # without another warning or a deferred WAL negotiation.
            with engine.connect() as sibling:
                sibling_driver = sibling.connection.driver_connection
                assert sibling_driver is not driver_connection
                assert sibling_driver.isolation_level is not None
    finally:
        engine.dispose()

    assert len(warnings) == 1
    assert "NETWORK_SHARE_MODE=true" in warnings[0]
    assert "even when /config is on local disk" in warnings[0]
    assert "contained SAVEPOINTs acquire a local write transaction" in warnings[0]
