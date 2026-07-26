# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Book detail must survive an unreadable calibre metadata schema.

``_detail_custom_columns`` documents itself as "degrading safely if DB metadata
is unavailable", but it only caught ``AttributeError``/``KeyError``/``TypeError``.
The failure that actually happens in production is a ``SQLAlchemyError``: the
metadata DB opens fine but its schema is not there (library still being written,
a wrong or renamed library path, ``custom_columns`` mid-migration), so the query
raises ``OperationalError`` and the whole detail payload 500s over a
supplementary field.

This stayed invisible while ``calibre_db.session`` could be ``None`` -- the
``AttributeError`` from ``None.query`` was doing the catching, which is why the
gap only showed up once the session became a property that always materialises
(#1149). Production never had that cushion.
"""
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import scoped_session, sessionmaker


@pytest.fixture
def unreadable_calibre_schema():
    """Point ``CalibreDB.session_factory`` at a DB with no calibre schema.

    Restores the previous factory so this cannot leak into other tests -- the
    bug under test is itself a cross-test-pollution story.
    """
    from cps import db as dbmod

    tmp = Path(tempfile.mkdtemp()) / "metadata.db"
    sqlite3.connect(tmp).close()  # exists, opens, has no custom_columns

    engine = create_engine(f"sqlite:///{tmp}", future=True)
    factory = scoped_session(sessionmaker(autocommit=False, autoflush=True,
                                          bind=engine, future=True))

    previous_factory = dbmod.CalibreDB.session_factory
    previous_init = dbmod.CalibreDB._init
    dbmod.CalibreDB.session_factory = factory
    dbmod.CalibreDB._init = True
    try:
        yield
    finally:
        factory.remove()
        engine.dispose()
        dbmod.CalibreDB.session_factory = previous_factory
        dbmod.CalibreDB._init = previous_init
        tmp.unlink(missing_ok=True)


@pytest.mark.unit
def test_missing_custom_columns_table_does_not_raise(unreadable_calibre_schema):
    """The documented contract: unavailable metadata degrades, it does not raise."""
    from cps.api import books as books_mod

    assert books_mod._detail_custom_columns() == []


@pytest.mark.unit
def test_get_cc_columns_degrades_for_every_caller(unreadable_calibre_schema):
    """The degradation lives in ``get_cc_columns``, not in one caller's wrapper.

    Five callers want "the custom columns, if any": the book detail page
    (``cps/web.py`` ``show_book``), the detail API, the books table, and both
    search surfaces. Only the API had a guard, so a classic-theme book page
    still 500'd on an unreadable schema -- caught by driving the real routes,
    not by the unit tests. Pinning it at the source keeps the other four from
    regressing independently.
    """
    from cps.api import books as books_mod

    assert books_mod.calibre_db.get_cc_columns(books_mod.config,
                                               filter_config_custom_read=True) == []


@pytest.mark.unit
def test_fixture_really_reproduces_an_unreadable_schema(unreadable_calibre_schema):
    """Pins that the fixture reproduces the real failure.

    Without this the contract tests would still pass if ``get_cc_columns``
    quietly stopped touching the DB, and the regression they guard would be
    unpinned. Asserts against the raw query rather than the guarded method.
    """
    from cps import db as dbmod

    session = dbmod.CalibreDB.session_factory()
    with pytest.raises(SQLAlchemyError):
        session.query(dbmod.CustomColumns).all()


@pytest.mark.unit
def test_request_can_keep_querying_after_the_failure(unreadable_calibre_schema):
    """The safety property that actually matters, not just the return value.

    Degrading is only useful if the *rest* of the page still renders. A caught
    DB error can leave a Session in partial-rollback, where every later query
    raises ``PendingRollbackError`` -- which would turn one dead page into a
    subtly broken one and still satisfy a test that only asserts ``[]``.

    So: fail the custom-column read, then keep using the same session.
    """
    from cps import db as dbmod
    from cps.api import books as books_mod

    session = dbmod.CalibreDB.session_factory()
    session.execute(sa_text("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)"))
    session.execute(sa_text("INSERT INTO books (id, title) VALUES (1, 'Still here')"))
    session.commit()

    assert books_mod.calibre_db.get_cc_columns(books_mod.config,
                                               filter_config_custom_read=True) == []

    # Same session, after the swallowed error.
    assert session.execute(sa_text("SELECT title FROM books WHERE id = 1")).scalar() == "Still here"
    assert session.is_active


@pytest.mark.unit
def test_healthy_library_still_returns_its_custom_columns():
    """Guards the degrade tests against passing vacuously.

    Every other test here asserts ``[]``, so an accidental unconditional
    ``return []`` -- which would silently hide custom columns from every user
    with a perfectly healthy library -- would leave them all green.
    """
    from cps import db as dbmod

    tmp = Path(tempfile.mkdtemp()) / "metadata.db"
    con = sqlite3.connect(tmp)
    con.execute("""CREATE TABLE custom_columns (
        id INTEGER PRIMARY KEY, label TEXT, name TEXT, datatype TEXT,
        mark_for_delete BOOL, editable BOOL, display TEXT, is_multiple BOOL,
        normalized BOOL)""")
    con.execute("INSERT INTO custom_columns VALUES (1,'read','Read','bool',0,1,'{}',0,0)")
    con.commit()
    con.close()

    engine = create_engine(f"sqlite:///{tmp}", future=True)
    factory = scoped_session(sessionmaker(autocommit=False, autoflush=True,
                                          bind=engine, future=True))
    previous_factory = dbmod.CalibreDB.session_factory
    previous_init = dbmod.CalibreDB._init
    dbmod.CalibreDB.session_factory = factory
    dbmod.CalibreDB._init = True
    try:
        cdb = dbmod.CalibreDB.__new__(dbmod.CalibreDB)
        cc = cdb.get_cc_columns(SimpleNamespace(config_columns_to_ignore=None,
                                                config_read_column=0))
        assert [c.name for c in cc] == ["Read"]
    finally:
        factory.remove()
        engine.dispose()
        dbmod.CalibreDB.session_factory = previous_factory
        dbmod.CalibreDB._init = previous_init
        tmp.unlink(missing_ok=True)


@pytest.mark.unit
def test_session_is_materialised_not_none(unreadable_calibre_schema):
    """The precondition that removed the old accidental cushion.

    ``calibre_db.session`` resolves through the registry now, so it hands back a
    live Session instead of ``None``. That is what makes catching only
    ``AttributeError`` insufficient.
    """
    from cps.api import books as books_mod

    assert books_mod.calibre_db.session is not None
