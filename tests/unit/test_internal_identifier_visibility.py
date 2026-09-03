# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Internal ingest identifiers stay outside every public ORM consumer."""

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cps import db, epub_helper, helper
from cps.duplicates import select_book_to_keep
from cps.services import parallel
from tests.fixtures.kepub_fixture import build_calibre_epub3_series_kepub


pytestmark = pytest.mark.unit

_DIGEST = "d" * 64
_MARKER_TYPE = f"cwng_ingest_sha256_{_DIGEST}"


@pytest.fixture
def calibre_session():
    def creator():
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        connection.execute("ATTACH DATABASE ':memory:' AS calibre")
        return connection

    engine = create_engine(
        "sqlite+pysqlite://", creator=creator, poolclass=StaticPool,
    )
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_book(session, title, *, timestamp=None, identifiers=()):
    now = timestamp or datetime(2026, 1, 2, tzinfo=timezone.utc)
    author = session.query(db.Authors).filter(db.Authors.name == "Test Author").first()
    if author is None:
        author = db.Authors("Test Author", "Author, Test")
    book = db.Books(
        title, title, "Author, Test", now, now, "1.0", now,
        f"test/{title.lower().replace(' ', '-')}", False, [], [],
    )
    book.uuid = f"uuid-{title.lower().replace(' ', '-')}"
    book.authors = [author]
    session.add(book)
    session.commit()
    session.add_all([
        db.Identifiers(value, identifier_type, book.id)
        for identifier_type, value in identifiers
    ])
    session.commit()
    session.expire_all()
    return session.query(db.Books).filter(db.Books.id == book.id).one()


def test_book_identifier_relationship_is_the_visibility_choke_point(calibre_session):
    """Raw reserved rows are hidden normally and available only by explicit access."""
    book = _seed_book(
        calibre_session,
        "Relationship Probe",
        identifiers=(
            ("isbn", "9780000000001"),
            (_MARKER_TYPE.upper(), _DIGEST),
            ("x-cwng_ingest_sha256_public", "near-prefix"),
        ),
    )

    assert {(row.type, row.val) for row in book.identifiers} == {
        ("isbn", "9780000000001"),
        ("x-cwng_ingest_sha256_public", "near-prefix"),
    }
    assert [(row.type.lower(), row.val) for row in book.internal_identifiers] == [
        (_MARKER_TYPE, _DIGEST),
    ]
    assert calibre_session.query(db.Identifiers).filter(
        db.Identifiers.book == book.id,
    ).count() == 3
    assert calibre_session.query(db.Books).filter(
        db.Books.id == book.id,
        db.Books.identifiers.any(db.Identifiers.type == _MARKER_TYPE),
    ).count() == 0
    assert calibre_session.query(db.Books).filter(
        db.Books.id == book.id,
        db.Books.internal_identifiers.any(db.Identifiers.type == _MARKER_TYPE),
    ).count() == 1

    from cps.editbooks import modify_identifiers
    _changed, duplicate = modify_identifiers(
        [
            db.Identifiers("9780000000005", "isbn", book.id),
            db.Identifiers("near-prefix", "x-cwng_ingest_sha256_public", book.id),
        ],
        book.identifiers,
        calibre_session,
    )
    assert duplicate is False
    calibre_session.commit()
    calibre_session.expire_all()
    reloaded = calibre_session.query(db.Books).filter(db.Books.id == book.id).one()
    assert [(row.type.lower(), row.val) for row in reloaded.internal_identifiers] == [
        (_MARKER_TYPE, _DIGEST),
    ]
    assert {row.type: row.val for row in reloaded.identifiers}["isbn"] == (
        "9780000000005"
    )


def test_listenmp3_does_not_render_internal_identifier(calibre_session):
    book = _seed_book(
        calibre_session,
        "Audio Surface",
        identifiers=(("isbn", "9780000000002"), (_MARKER_TYPE, _DIGEST)),
    )
    environment = Environment(
        loader=FileSystemLoader("cps/templates"), autoescape=True,
    )
    environment.filters.update({
        "clean_string": lambda value: value,
        "escapedlink": lambda value: value,
        "formatdate": lambda value, *_args: value,
        "formatfloat": lambda value, *_args: value,
        "last_modified": lambda _value: "",
    })

    def gettext(message, **values):
        return message % values if values else message

    rendered = environment.get_template("listenmp3.html").render(
        entry=book,
        mp3file=book.id,
        audioformat="mp3",
        bookmark=None,
        books_shelfs=[],
        current_user=SimpleNamespace(is_anonymous=True, is_authenticated=False),
        g=SimpleNamespace(google_site_verification="", shelves_access=[]),
        _=gettext,
        csrf_token=lambda: "test-csrf",
        url_for=lambda endpoint, **_values: f"/{endpoint}",
    )

    assert "ISBN" in rendered
    assert "9780000000002" in rendered
    assert _MARKER_TYPE not in rendered.lower()
    assert _DIGEST not in rendered


def test_downloaded_kepub_opf_does_not_contain_internal_identifier(
    calibre_session, monkeypatch, tmp_path,
):
    book = _seed_book(
        calibre_session,
        "Kepub Surface",
        identifiers=(("isbn", "9780000000003"), (_MARKER_TYPE, _DIGEST)),
    )
    source = build_calibre_epub3_series_kepub(tmp_path / "source.kepub")

    class Query:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return []

    monkeypatch.setattr(
        helper.calibre_db,
        "session",
        SimpleNamespace(query=lambda *_args: Query()),
    )
    monkeypatch.setattr(helper, "current_user", SimpleNamespace(locale="en"))
    monkeypatch.setattr(helper, "_", lambda value: value)
    monkeypatch.setattr(helper, "get_temp_dir", lambda: str(tmp_path))
    monkeypatch.setattr(helper, "uuid4", lambda: "served-copy")
    monkeypatch.setattr(parallel, "run_blocking", lambda job: job())

    output_dir, output_name = helper.do_kepubify_metadata_replace(book, str(source))
    served = f"{output_dir}/{output_name}.kepub"
    package, _package_name = epub_helper.get_content_opf(served)
    merged = epub_helper.etree.tostring(package, encoding="unicode")

    assert "9780000000003" in merged
    assert _MARKER_TYPE not in merged.lower()
    assert _DIGEST not in merged


def test_most_metadata_duplicate_resolution_ignores_internal_identifier(calibre_session):
    newer_time = datetime(2026, 1, 2, tzinfo=timezone.utc)
    older = _seed_book(
        calibre_session,
        "Older Duplicate",
        timestamp=newer_time - timedelta(days=1),
        identifiers=((_MARKER_TYPE, _DIGEST),),
    )
    newer = _seed_book(
        calibre_session,
        "Newer Duplicate",
        timestamp=newer_time,
    )

    kept = select_book_to_keep([older, newer], "most_metadata")

    assert kept.id == newer.id
    assert older.internal_identifiers[0].type == _MARKER_TYPE
