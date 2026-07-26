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

import pytest
from sqlalchemy import create_engine
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
def test_session_is_materialised_not_none(unreadable_calibre_schema):
    """The precondition that removed the old accidental cushion.

    ``calibre_db.session`` resolves through the registry now, so it hands back a
    live Session instead of ``None``. That is what makes catching only
    ``AttributeError`` insufficient.
    """
    from cps.api import books as books_mod

    assert books_mod.calibre_db.session is not None
