# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression coverage for the thumbnail lookup slowdown in issue #1571."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.unit

INDEX_NAME = "ix_thumbnail_cover_lookup"


def test_thumbnail_model_defines_cover_lookup_index():
    from cps import ub

    index = next(
        (item for item in ub.Thumbnail.__table__.indexes if item.name == INDEX_NAME),
        None,
    )
    assert index is not None
    assert tuple(column.name for column in index.columns) == (
        "type",
        "entity_id",
        "resolution",
        "format",
    )


def _legacy_app_db(tmp_path, monkeypatch):
    """Build an app.db in the pre-#1571 shape, then run the real migrator."""
    from cps import config_sql, constants, ub

    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path), raising=False)
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    ub.Base.metadata.create_all(engine)
    config_sql._Settings.__table__.create(engine, checkfirst=True)
    with engine.begin() as connection:
        connection.execute(text(f"DROP INDEX IF EXISTS {INDEX_NAME}"))

    Session = sessionmaker(bind=engine, future=True)
    db_session = Session()
    ub.migrate_Database(db_session)
    return engine, db_session


def _thumbnail_query_plan(engine):
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "EXPLAIN QUERY PLAN "
                "SELECT * FROM thumbnail "
                "WHERE type = :type AND entity_id = :entity_id "
                "AND resolution = :resolution "
                "AND format IN (:webp, :jpg) "
                "AND (expiration IS NULL OR expiration > :now) "
                "ORDER BY format ASC, id ASC"
            ),
            {
                "type": 1,
                "entity_id": 123,
                "resolution": 2,
                "webp": "webp",
                "jpg": "jpg",
                "now": datetime.now(timezone.utc),
            },
        ).fetchall()
    return " | ".join(str(row[3]) for row in rows)


def test_existing_app_db_migration_adds_thumbnail_lookup_index(tmp_path, monkeypatch):
    engine, db_session = _legacy_app_db(tmp_path, monkeypatch)
    try:
        plan = _thumbnail_query_plan(engine)
        assert f"USING INDEX {INDEX_NAME}" in plan, plan
        assert "SCAN thumbnail" not in plan, plan
    finally:
        db_session.close()
        engine.dispose()


def test_thumbnail_lookup_index_migration_is_idempotent(tmp_path, monkeypatch):
    from cps import ub

    engine, db_session = _legacy_app_db(tmp_path, monkeypatch)
    try:
        marker = tmp_path / ".cwa_migrations" / "thumbnail_lookup_index_v1"
        assert marker.is_file()

        monkeypatch.setattr(
            ub,
            "_run_ddl_with_retry",
            lambda *args, **kwargs: pytest.fail("second migration run issued DDL"),
        )
        ub.migrate_thumbnail_lookup_index(engine, db_session)
    finally:
        db_session.close()
        engine.dispose()


def test_thumbnail_lookup_index_failure_does_not_break_startup(
    tmp_path, monkeypatch, caplog
):
    from cps import constants, ub

    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(
        ub,
        "_run_ddl_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("read only")),
    )

    ub.migrate_thumbnail_lookup_index(None, None)

    assert "index creation failed: read only" in caplog.text
    assert not (
        tmp_path / ".cwa_migrations" / "thumbnail_lookup_index_v1"
    ).exists()


def _thumbnail_session():
    from cps import ub

    engine = create_engine("sqlite://", future=True)
    ub.Thumbnail.__table__.create(engine)
    Session = sessionmaker(bind=engine, future=True)
    return engine, Session()


def test_collapsed_lookup_keeps_has_cover_guard(monkeypatch):
    from cps import helper, ub

    class QueryForbidden:
        @staticmethod
        def query(*args, **kwargs):
            pytest.fail("cover-less book queried the thumbnail table")

    monkeypatch.setattr(ub, "session", QueryForbidden())
    assert helper.get_book_cover_thumbnails_by_formats(None, 2, ("webp", "jpg")) == {}
    assert helper.get_book_cover_thumbnails_by_formats(
        SimpleNamespace(id=123, has_cover=False), 2, ("webp", "jpg")
    ) == {}


def test_collapsed_lookup_uses_lowest_id_per_format_and_ignores_expired(monkeypatch):
    from cps import helper, ub

    engine, db_session = _thumbnail_session()
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            ub.Thumbnail(
                id=30,
                entity_id=123,
                format="webp",
                type=helper.THUMBNAIL_TYPE_COVER,
                resolution=2,
                filename="later.webp",
            ),
            ub.Thumbnail(
                id=10,
                entity_id=123,
                format="webp",
                type=helper.THUMBNAIL_TYPE_COVER,
                resolution=2,
                filename="earlier.webp",
            ),
            ub.Thumbnail(
                id=5,
                entity_id=123,
                format="jpg",
                type=helper.THUMBNAIL_TYPE_COVER,
                resolution=2,
                filename="expired.jpg",
                expiration=now - timedelta(seconds=1),
            ),
            ub.Thumbnail(
                id=20,
                entity_id=123,
                format="jpg",
                type=helper.THUMBNAIL_TYPE_COVER,
                resolution=2,
                filename="current.jpg",
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(ub, "session", db_session)

    try:
        result = helper.get_book_cover_thumbnails_by_formats(
            SimpleNamespace(id=123, has_cover=True), 2, ("webp", "jpg")
        )
        assert result["webp"].id == 10
        assert result["jpg"].id == 20
    finally:
        db_session.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("path", "expected_filename"),
    [("/books/123", "cover.webp"), ("/kobo/123/cover", "cover.jpg")],
)
def test_collapsed_lookup_preserves_request_format_preference(
    tmp_path, monkeypatch, path, expected_filename
):
    from cps import helper, ub

    engine, db_session = _thumbnail_session()
    generated_at = datetime.now(timezone.utc)
    db_session.add_all(
        [
            ub.Thumbnail(
                entity_id=123,
                format="webp",
                type=helper.THUMBNAIL_TYPE_COVER,
                resolution=2,
                filename="cover.webp",
                generated_at=generated_at,
            ),
            ub.Thumbnail(
                entity_id=123,
                format="jpg",
                type=helper.THUMBNAIL_TYPE_COVER,
                resolution=2,
                filename="cover.jpg",
                generated_at=generated_at,
            ),
        ]
    )
    db_session.commit()

    class Cache:
        @staticmethod
        def get_cache_file_exists(filename, cache_type):
            return True

        @staticmethod
        def get_cache_file_dir(filename, cache_type):
            return str(tmp_path)

    monkeypatch.setattr(ub, "session", db_session)
    monkeypatch.setattr(helper.fs, "FileSystem", Cache)
    monkeypatch.setattr(helper, "send_from_directory", lambda directory, filename: filename)

    thumbnail_selects = []

    @event.listens_for(engine, "before_cursor_execute")
    def _count_thumbnail_selects(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "FROM thumbnail" in statement:
            thumbnail_selects.append(statement)

    book = SimpleNamespace(id=123, has_cover=True, last_modified=generated_at)
    app = Flask(__name__)
    try:
        with app.test_request_context(path):
            assert helper.get_book_cover_internal(book, resolution=2) == expected_filename
        assert len(thumbnail_selects) == 1, thumbnail_selects
    finally:
        db_session.close()
        engine.dispose()
