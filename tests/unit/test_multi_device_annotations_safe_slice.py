# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import datetime
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def db_session():
    from cps import ub
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_device_registry_upsert_is_idempotent_and_hides_raw_id(db_session):
    from cps import ub
    from cps.services.device_registry import upsert_kobo_device
    headers = {"x-kobo-deviceid": "a" * 64, "x-kobo-devicemodel": "Kobo Libra Colour",
               "x-kobo-appversion": "4.45.23684"}
    first = upsert_kobo_device(db_session, user_id=7, headers=headers, secret_key="test-secret",
                               seen_at=datetime(2026, 8, 9, 1, 0))
    db_session.commit()
    second = upsert_kobo_device(db_session, user_id=7, headers=headers, secret_key="test-secret",
                                seen_at=datetime(2026, 8, 9, 2, 0))
    db_session.commit()
    assert first.id == second.id
    assert db_session.query(ub.Device).count() == 1
    identity = db_session.query(ub.DeviceIdentity).one()
    assert identity.fingerprint != headers["x-kobo-deviceid"]
    assert identity.last_seen_at == datetime(2026, 8, 9, 2, 0)


def test_known_device_model_change_updates_and_warns(db_session, caplog):
    from cps.services.device_registry import upsert_kobo_device
    headers = {"x-kobo-deviceid": "c" * 64, "x-kobo-devicemodel": "Kobo Clara HD"}
    device = upsert_kobo_device(db_session, user_id=7, headers=headers, secret_key="test-secret",
                                seen_at=datetime(2026, 8, 9, 1, 0))
    db_session.commit()
    headers["x-kobo-devicemodel"] = "Kobo Libra Colour"
    with caplog.at_level("WARNING"):
        updated = upsert_kobo_device(db_session, user_id=7, headers=headers, secret_key="test-secret",
                                     seen_at=datetime(2026, 8, 9, 2, 0))
    assert updated.id == device.id
    assert updated.model == "Kobo Libra Colour"
    assert updated.display_name == "Kobo Clara HD"
    assert "Kobo Clara HD" in caplog.text and "Kobo Libra Colour" in caplog.text


def test_known_device_last_seen_write_is_throttled(db_session):
    from cps.services.device_registry import upsert_kobo_device
    headers = {"x-kobo-deviceid": "d" * 64, "x-kobo-devicemodel": "Kobo Clara HD",
               "x-kobo-appversion": "4.45"}
    first_seen = datetime(2026, 8, 9, 1, 0)
    device = upsert_kobo_device(
        db_session, user_id=7, headers=headers, secret_key="test-secret",
        seen_at=first_seen,
    )
    db_session.commit()
    upsert_kobo_device(
        db_session, user_id=7, headers=headers, secret_key="test-secret",
        seen_at=datetime(2026, 8, 9, 1, 0, 30),
    )
    db_session.commit()
    db_session.refresh(device)
    assert device.last_seen_at == first_seen


def test_registry_failure_does_not_break_reading_services_request(monkeypatch):
    from cps import readingservices
    from cps.services import device_registry
    app = Flask(__name__)
    app.secret_key = "x"
    monkeypatch.setattr(readingservices.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(readingservices, "current_user", SimpleNamespace(is_authenticated=True, id=7))
    monkeypatch.setattr(device_registry, "sessionmaker", lambda **_kwargs: (_ for _ in ()).throw(OSError("disk")))
    wrapped = readingservices.requires_reading_services_auth_and_config(lambda: ("upstream", 207))
    with app.test_request_context("/api/v3/content/x/annotations", headers={"x-kobo-deviceid": "b" * 64}):
        assert wrapped() == ("upstream", 207)


@pytest.mark.parametrize("value", ["not-a-shape", "../bad", "00000000-0000-0000-0000-000000000000!!../x"])
def test_content_id_refuses_unknown_or_unsafe_shapes(value):
    from cps.services.annotation_content_id import normalize_content_id, ContentIdError
    with pytest.raises(ContentIdError):
        normalize_content_id(value)


def test_content_id_normalizes_both_legacy_shapes_idempotently():
    from cps.services.annotation_content_id import normalize_content_id
    book = "B3D1B38B-74FD-43B7-A796-996E5A6A8B04"
    canonical = normalize_content_id(f"{book}!!OEBPS/chapter.xhtml")
    assert canonical == "b3d1b38b-74fd-43b7-a796-996e5a6a8b04!!OEBPS/chapter.xhtml"
    assert normalize_content_id(canonical) == canonical
    raw = "file:///mnt/onboard/book.epub#(6)OEBPS/chapter.xhtml"
    assert normalize_content_id(raw, book_uuid=book, allow_legacy_file_uri=True) == canonical


def test_backfill_is_conservative_and_idempotent():
    from cps import ub
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE annotation (id INTEGER PRIMARY KEY, book_id INTEGER, content_id TEXT)"))
        uuid = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"
        wrong = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        conn.execute(text("INSERT INTO annotation VALUES (1, 5, :v), (2, 5, :u), (3, 5, :w), (4, 99, :m)"), {
            "v": f"file:///mnt/onboard/{uuid}.epub#(6)OEBPS/c.xhtml",
            "u": "opaque-old-value",
            "w": f"file:///mnt/onboard/{wrong}.epub#(6)OEBPS/wrong.xhtml",
            "m": f"file:///mnt/onboard/{uuid}.epub#(6)OEBPS/missing.xhtml",
        })
    ub.migrate_multi_device_annotation_safe_slice(engine, None)
    lookup = lambda book_id: uuid if book_id == 5 else None
    ub.backfill_annotation_content_ids(engine, lookup)
    once = engine.connect().execute(text("SELECT id, content_id FROM annotation ORDER BY id")).fetchall()
    ub.backfill_annotation_content_ids(engine, lookup)
    twice = engine.connect().execute(text("SELECT id, content_id FROM annotation ORDER BY id")).fetchall()
    assert once == twice
    assert once[0][1] == f"{uuid}!!OEBPS/c.xhtml"
    assert once[1][1] == "opaque-old-value"
    assert once[2][1] == f"file:///mnt/onboard/{wrong}.epub#(6)OEBPS/wrong.xhtml"
    assert once[3][1] == f"file:///mnt/onboard/{uuid}.epub#(6)OEBPS/missing.xhtml"


def test_backfill_repairs_journaled_wrong_book_without_overwriting_later_edits():
    from cps import ub
    engine = create_engine("sqlite:///:memory:")
    actual = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"
    wrong = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    original = f"file:///mnt/onboard/{wrong}.epub#(6)OEBPS/wrong.xhtml"
    normalized = f"{wrong}!!OEBPS/wrong.xhtml"
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE annotation (id INTEGER PRIMARY KEY, book_id INTEGER, content_id TEXT)"
        ))
        conn.execute(text("INSERT INTO annotation VALUES (1, 5, :normalized)"), {
            "normalized": normalized,
        })
    ub.migrate_multi_device_annotation_safe_slice(engine, None)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO annotation_content_id_migration "
            "(annotation_row_id, original_content_id, normalized_content_id, migrated_at) "
            "VALUES (1, :original, :normalized, CURRENT_TIMESTAMP)"
        ), {"original": original, "normalized": normalized})
    ub.backfill_annotation_content_ids(engine, lambda _book_id: actual)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT content_id FROM annotation WHERE id=1")).scalar() == original
        assert conn.execute(text("SELECT COUNT(*) FROM annotation_content_id_migration")).scalar() == 0


@pytest.mark.parametrize("raw, expected", [
    (None, "missing"), ("nonsense", "malformed"), ("2026-08-09T14:52:21Z", "valid")
])
def test_client_last_modified_missing_malformed_valid(db_session, raw, expected):
    from cps import ub
    from cps.services.annotation_sync import _upsert_annotation
    payload = {"id": f"ann-{expected}", "highlightedText": "text"}
    if raw is not None:
        payload["clientLastModifiedUtc"] = raw
    row = _upsert_annotation(db_session, payload,
                             SimpleNamespace(id=5, uuid="b3d1b38b-74fd-43b7-a796-996e5a6a8b04"),
                             SimpleNamespace(id=7))
    if expected == "malformed":
        assert row is None
        assert db_session.query(ub.Annotation).count() == 0
    elif expected == "missing":
        assert row.client_modified_at is None
    else:
        assert row.client_modified_at == datetime(2026, 8, 9, 14, 52, 21)


def test_kobo_create_records_origin_once_and_update_cannot_replace_it(db_session):
    from cps import ub
    from cps.services.annotation_sync import _upsert_annotation
    first = ub.Device(user_id=7, kind="kobo", display_name="First", active=True, created_by="auto")
    second = ub.Device(user_id=7, kind="kobo", display_name="Second", active=True, created_by="auto")
    db_session.add_all([first, second])
    db_session.flush()
    book = SimpleNamespace(id=5, uuid="b3d1b38b-74fd-43b7-a796-996e5a6a8b04")
    user = SimpleNamespace(id=7)
    row = _upsert_annotation(
        db_session,
        {"id": "origin-once", "highlightedText": "first"},
        book, user, origin_device_id=first.id,
    )
    assert row.origin_device_id == first.id
    row = _upsert_annotation(
        db_session,
        {"id": "origin-once", "highlightedText": "updated"},
        book, user, origin_device_id=second.id,
    )
    assert row.origin_device_id == first.id


def test_older_client_clock_cannot_overwrite_newer_annotation(db_session):
    from cps.services.annotation_sync import _upsert_annotation
    book, user = SimpleNamespace(id=5, uuid="b3d1b38b-74fd-43b7-a796-996e5a6a8b04"), SimpleNamespace(id=7)
    newer = {"id": "ann", "highlightedText": "new", "clientLastModifiedUtc": "2026-08-09T15:00:00Z"}
    older = {"id": "ann", "highlightedText": "old", "clientLastModifiedUtc": "2026-08-09T14:00:00Z"}
    row = _upsert_annotation(db_session, newer, book, user)
    db_session.flush()
    assert _upsert_annotation(db_session, older, book, user) is None
    assert row.highlighted_text == "new"
