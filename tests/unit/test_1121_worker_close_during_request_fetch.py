# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#1121 / #1048 — a background task must not break a request that is mid-fetch.

`tests/unit/test_1121_session_is_thread_local.py` pins the *mechanism*: a
thread's Session was replaced underneath it. This file pins the *symptom* the
reporter actually hit, which #1121 explicitly left unproven:

    "A reproduction should force the overlap deliberately — queue a manual scan
    while a slow paginated browse is streaming, and assert the browse
    completes."

Two earlier attempts to link the two failed because they closed and then
re-used a Session sequentially, and a Session simply re-acquires a connection
after `close()`. The traceback on #1048 is a cursor being *drained*
(`sqlalchemy/orm/loading.py` `chunks` → `_raw_all_rows`), so the interleave has
to be genuine: the worker must close while the request thread is partway
through a result set it has not finished streaming.

That is what this test builds. The reader starts iterating a `yield_per`
result — so rows arrive from a live cursor in batches rather than all at once —
stops mid-stream, and only then lets the worker run
`cps/tasks/duplicate_scan.py`'s teardown shape (`ensure_session()` …
`session.close()`). The reader then drains the rest.

The engine mirrors production deliberately: `StaticPool` over one shared
connection, which `notes/fix-udf-gil-deadlock-DESIGN.md` records as
architecturally required (the engine is `sqlite://` with per-connection
`ATTACH`, so per-thread connections are not available). The reader and the
worker therefore share a *connection* whatever happens; what #1121 changes is
whether they also share a *Session*.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import StaticPool

pytestmark = pytest.mark.unit

Base = declarative_base()

ROWS = 400
#: Small enough that the reader is still holding an open cursor when it pauses.
BATCH = 10


class _Row(Base):
    __tablename__ = "probe_rows"
    id = Column(Integer, primary_key=True)
    title = Column(String)


def _wire_calibredb():
    """A CalibreDB on a production-shaped engine: StaticPool, one connection."""
    from cps.db import CalibreDB

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    seed = sessionmaker(bind=engine, future=True)()
    seed.add_all([_Row(id=i, title="row-%d" % i) for i in range(1, ROWS + 1)])
    seed.commit()
    seed.close()

    CalibreDB.engine = engine
    CalibreDB.session_factory = scoped_session(
        sessionmaker(bind=engine, autocommit=False, autoflush=True, future=True))
    return CalibreDB()


def test_a_worker_closing_its_session_does_not_kill_a_request_mid_fetch():
    """The #1048 shape: worker teardown lands while a browse is still streaming.

    RED before #1121 — the worker's `close()` lands on the *reader's* Session,
    because both read the same shared attribute, and the reader's next batch
    hits a closed handle.
    """
    db = _wire_calibredb()

    reader_paused = threading.Event()
    worker_done = threading.Event()
    outcome = {}

    def request_thread():
        try:
            db.init_session()
            session = db.session
            seen = 0
            for _ in session.query(_Row).yield_per(BATCH):
                seen += 1
                if seen == BATCH + 1:
                    # Mid-stream: the first batch is drained, the cursor is
                    # still open, and more batches are pending.
                    reader_paused.set()
                    worker_done.wait(10)
            outcome["rows"] = seen
        except Exception as exc:  # noqa: BLE001 - the failure mode under test
            outcome["error"] = "%s: %s" % (type(exc).__name__, exc)

    def worker_thread():
        # cps/tasks/duplicate_scan.py:90 then :324-325
        reader_paused.wait(10)
        try:
            db.ensure_session()
            db.session.query(_Row).count()
            db.session.close()
        except Exception as exc:  # noqa: BLE001
            outcome["worker_error"] = "%s: %s" % (type(exc).__name__, exc)
        finally:
            worker_done.set()

    threads = [threading.Thread(target=request_thread),
               threading.Thread(target=worker_thread)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert "worker_error" not in outcome, outcome["worker_error"]
    assert "error" not in outcome, (
        "the request thread died mid-fetch because a background task tore down "
        "the session it was streaming from — this is the #1048 symptom: %s"
        % outcome.get("error"))
    assert outcome.get("rows") == ROWS, (
        "the browse did not complete: read %s of %s rows"
        % (outcome.get("rows"), ROWS))


def test_the_worker_and_the_request_do_not_share_a_session_object():
    """The invariant behind the test above, stated directly."""
    db = _wire_calibredb()
    ready = threading.Event()
    done = threading.Event()
    seen = {}

    def request_thread():
        db.init_session()
        seen["request"] = id(db.session)
        ready.set()
        done.wait(10)
        seen["request_after"] = id(db.session)

    def worker_thread():
        ready.wait(10)
        db.ensure_session()
        seen["worker"] = id(db.session)
        done.set()

    threads = [threading.Thread(target=request_thread),
               threading.Thread(target=worker_thread)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert seen["request"] != seen["worker"]
    assert seen["request_after"] == seen["request"]
