# -*- coding: utf-8 -*-
"""Auto metadata fetch must record a text-only change as a book change.

Only the cover branch of ``_apply_metadata_to_book`` stamped ``last_modified``
(``helper.mark_book_modified``) and queued the file embed
(``helper.log_metadata_change``). A fetch that filled only a description,
publisher, published date or rating committed to metadata.db and did neither,
so Kobo sync (``last_modified > sync_token``) did not see the change and the
book file never got the new metadata, even with enforcement on.

Real SQLite calibre schema, same fixture shape as the #2435 tests.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import cps.helper as helper
import cps.metadata_helper as m
from cps import db

pytestmark = pytest.mark.unit

OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)


class _RealCDB:
    session_factory = True

    def __init__(self, session):
        self.session = session

    def get_book(self, book_id):
        return self.session.get(db.Books, book_id)

    def get_author_by_name(self, name):
        return self.session.query(db.Authors).filter(db.Authors.name == name).first()

    def get_publisher_by_name(self, name):
        return self.session.query(db.Publishers).filter(db.Publishers.name == name).first()

    def get_tag_by_name(self, name):
        return self.session.query(db.Tags).filter(db.Tags.name == name).first()

    def get_series_by_name(self, name):
        return self.session.query(db.Series).filter(db.Series.name == name).first()


def _settings(**over):
    base = {key: False for key in (
        "auto_metadata_update_title", "auto_metadata_update_authors",
        "auto_metadata_update_description", "auto_metadata_update_publisher",
        "auto_metadata_update_tags", "auto_metadata_update_series",
        "auto_metadata_update_published_date", "auto_metadata_update_rating",
        "auto_metadata_update_identifiers", "auto_metadata_update_cover",
    )}
    base.update(auto_metadata_fetch_enabled=True, auto_metadata_smart_application=True)
    base.update(over)
    return base


def _meta(**kw):
    base = dict(title="", authors=[], description="", publisher="", tags=[],
                series=None, series_index=None, publishedDate=None, rating=None,
                identifiers={}, cover=None)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def library():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS calibre")
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.execute(db.Books.__table__.insert(), [
        {"id": 1, "title": "Book 1", "sort": "Book 1", "path": "b1", "author_sort": "",
         "last_modified": OLD},
    ])
    session.commit()
    return session


@pytest.fixture
def harness(monkeypatch, library):
    state = {"settings": _settings(), "logged": [], "log_raises": False}
    monkeypatch.setattr(m, "CWA_DB", lambda: SimpleNamespace(get_cwa_settings=lambda: state["settings"]))

    def fake_log(book, changed=None):
        if state["log_raises"]:
            raise OSError("change-log dir not writable")
        state["logged"].append((book.id, dict(changed or {})))

    monkeypatch.setattr(helper, "log_metadata_change", fake_log)
    state["book"] = library.get(db.Books, 1)
    state["cdb"] = _RealCDB(library)
    return state


def _last_modified(book):
    value = book.last_modified
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@pytest.mark.parametrize("setting, meta", [
    ("auto_metadata_update_description", {"description": "A fetched description."}),
    ("auto_metadata_update_publisher", {"publisher": "Dutton"}),
    ("auto_metadata_update_published_date", {"publishedDate": "2019-05-07"}),
    ("auto_metadata_update_rating", {"rating": 4}),
])
def test_text_only_fill_stamps_last_modified_and_queues_the_embed(harness, setting, meta):
    harness["settings"] = _settings(**{setting: True})

    assert m._apply_metadata_to_book(harness["book"], _meta(**meta), harness["cdb"]) is True

    assert _last_modified(harness["book"]) > OLD
    assert harness["logged"] == [(1, {"metadata": True})]


def test_nothing_applied_records_nothing(harness):
    # Smart mode keeps an existing publisher, so the fetch changes nothing.
    harness["settings"] = _settings(auto_metadata_update_publisher=True)
    library = harness["cdb"].session
    harness["book"].publishers = [db.Publishers("Ace", "Ace")]
    library.commit()

    assert m._apply_metadata_to_book(harness["book"], _meta(publisher="Berkley"), harness["cdb"]) is False

    assert _last_modified(harness["book"]) == OLD
    assert harness["logged"] == []


def test_enforcement_record_failure_does_not_fail_the_fetch(harness):
    harness["settings"] = _settings(auto_metadata_update_description=True)
    harness["log_raises"] = True

    assert m._apply_metadata_to_book(
        harness["book"], _meta(description="A fetched description."), harness["cdb"]) is True

    harness["cdb"].session.expire_all()
    assert harness["cdb"].session.get(db.Books, 1).comments[0].text == "A fetched description."
