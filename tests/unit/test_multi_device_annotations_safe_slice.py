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


def test_kobo_device_cap_refuses_new_identity_but_keeps_known_device(
    db_session, caplog,
):
    from cps import ub
    from cps.services import device_registry

    device_registry._kobo_cap_logged_users.clear()
    caplog.set_level("WARNING", logger=device_registry.__name__)
    devices = []
    for index in range(device_registry.MAX_KOBO_DEVICES_PER_USER):
        devices.append(device_registry.upsert_kobo_device(
            db_session,
            user_id=7,
            headers={"x-kobo-deviceid": f"{index:064x}"},
            secret_key="test-secret",
        ))
    db_session.commit()

    # Retired identities still consume a slot, matching the web-reader cap:
    # otherwise soft-delete/new-header churn restores unbounded growth.
    devices[0].active = False
    db_session.commit()
    with pytest.raises(
        device_registry.KoboDeviceLimitReached,
        match="Kobo device limit reached",
    ):
        device_registry.upsert_kobo_device(
            db_session,
            user_id=7,
            headers={"x-kobo-deviceid": "f" * 64},
            secret_key="test-secret",
        )
    db_session.rollback()

    known = device_registry.upsert_kobo_device(
        db_session,
        user_id=7,
        headers={"x-kobo-deviceid": f"{1:064x}"},
        secret_key="test-secret",
    )
    assert known.id == devices[1].id
    assert db_session.query(ub.Device).filter_by(user_id=7, kind="kobo").count() == 20
    assert db_session.query(ub.DeviceIdentity).count() == 20
    messages = [record.getMessage() for record in caplog.records]
    assert messages.count(device_registry.KOBO_DEVICE_LIMIT_MESSAGE) == 1
    assert all("ffffffff" not in message for message in messages)


def test_registry_failure_does_not_break_reading_services_request(monkeypatch):
    from cps import readingservices
    from cps.services import device_registry
    app = Flask(__name__)
    app.secret_key = "x"
    monkeypatch.setattr(readingservices.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(readingservices, "current_user", SimpleNamespace(is_authenticated=True, id=7))
    monkeypatch.setattr(device_registry, "sessionmaker", lambda **_kwargs: (_ for _ in ()).throw(OSError("disk")))
    wrapped = readingservices.requires_reading_services_auth_and_config(lambda: ("upstream", 207))
    # PATCH, not GET: device registration happens on the upload direction, and a
    # GET on this path is now intercepted by the annotation-download guard before
    # the decorator's pass-through can run (that guard is the subject of its own
    # test module). The property under test -- a registry failure must not break
    # the request -- is unchanged.
    with app.test_request_context("/api/v3/content/x/annotations", method="PATCH",
                                  headers={"x-kobo-deviceid": "b" * 64}):
        assert wrapped() == ("upstream", 207)


def test_reading_services_returns_clear_conflict_at_kobo_device_cap(monkeypatch):
    from cps import readingservices
    from cps.services import device_registry

    app = Flask(__name__)
    app.secret_key = "x"
    monkeypatch.setattr(
        readingservices.config, "config_kobo_sync", True, raising=False,
    )
    monkeypatch.setattr(
        readingservices,
        "current_user",
        SimpleNamespace(is_authenticated=True, id=7),
    )

    def at_cap(**_kwargs):
        raise device_registry.KoboDeviceLimitReached(
            device_registry.KOBO_DEVICE_LIMIT_MESSAGE,
        )

    monkeypatch.setattr(device_registry, "register_kobo_device_best_effort", at_cap)
    wrapped = readingservices.requires_reading_services_auth_and_config(
        lambda: ("must-not-run", 200),
    )
    with app.test_request_context(
        "/api/v3/content/x/annotations",
        method="PATCH",
        headers={"x-kobo-deviceid": "b" * 64},
    ):
        response = wrapped()

    assert response.status_code == 409
    assert response.get_json() == {
        "error": device_registry.KOBO_DEVICE_LIMIT_MESSAGE,
    }


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


def test_device_content_id_requires_explicit_import_opt_in():
    from cps.services.annotation_content_id import normalize_content_id, ContentIdError
    book = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"
    device = f"{book}!OEBPS!chapter.xhtml"
    canonical = f"{book}!!OEBPS/chapter.xhtml"

    with pytest.raises(ContentIdError):
        normalize_content_id(device, book_uuid=book)
    assert normalize_content_id(
        device, book_uuid=book, allow_kobo_device_content_id=True,
    ) == canonical
    assert normalize_content_id(
        canonical, book_uuid=book, allow_kobo_device_content_id=True,
    ) == canonical


@pytest.mark.parametrize(
    ("device_content_id", "message"),
    [
        pytest.param(
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa!OEBPS!chapter.xhtml",
            "content_id does not belong to this book",
            id="wrong-book",
        ),
        pytest.param("{book}!..!../outside.xhtml", "escapes", id="escape"),
        pytest.param("{book}!OEBPS//Text!chapter.xhtml", "unsafe segment", id="empty-segment"),
        pytest.param("{book}!OEBPS!chapter\x1f.xhtml", "control character", id="control"),
        pytest.param("{book}!OEBPS!" + "x" * 1536, "too long", id="chapter-length"),
    ],
)
def test_device_content_id_is_validated_after_folding(device_content_id, message):
    from cps.services.annotation_content_id import normalize_content_id, ContentIdError
    book = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"

    with pytest.raises(ContentIdError, match=message):
        normalize_content_id(
            device_content_id.format(book=book),
            book_uuid=book,
            allow_kobo_device_content_id=True,
        )


def test_backfill_is_conservative_and_idempotent():
    from cps import ub
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE annotation (id INTEGER PRIMARY KEY, book_id INTEGER, content_id TEXT)"))
        uuid = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"
        wrong = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        conn.execute(text("INSERT INTO annotation VALUES (1, 5, :v), (2, 5, :u), (3, 5, :w), (4, 99, :m), (5, 5, :d)"), {
            "v": f"file:///mnt/onboard/{uuid}.epub#(6)OEBPS/c.xhtml",
            "u": "opaque-old-value",
            "w": f"file:///mnt/onboard/{wrong}.epub#(6)OEBPS/wrong.xhtml",
            "m": f"file:///mnt/onboard/{uuid}.epub#(6)OEBPS/missing.xhtml",
            "d": f"{uuid}!OEBPS!device-only.xhtml",
        })
    ub.migrate_multi_device_annotation_safe_slice(engine, None)
    def lookup(book_id):
        return uuid if book_id == 5 else None

    ub.backfill_annotation_content_ids(engine, lookup)
    once = engine.connect().execute(text("SELECT id, content_id FROM annotation ORDER BY id")).fetchall()
    ub.backfill_annotation_content_ids(engine, lookup)
    twice = engine.connect().execute(text("SELECT id, content_id FROM annotation ORDER BY id")).fetchall()
    assert once == twice
    assert once[0][1] == f"{uuid}!!OEBPS/c.xhtml"
    assert once[1][1] == "opaque-old-value"
    assert once[2][1] == f"file:///mnt/onboard/{wrong}.epub#(6)OEBPS/wrong.xhtml"
    assert once[3][1] == f"file:///mnt/onboard/{uuid}.epub#(6)OEBPS/missing.xhtml"
    assert once[4][1] == f"{uuid}!OEBPS!device-only.xhtml"


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
        # A malformed CLOCK READING must not destroy the user's words. This
        # previously asserted the annotation was discarded, which is the same
        # data-loss shape as the content-location gate: the highlight text is
        # irreplaceable, the timestamp is an ordering hint. Store it, drop only
        # the hint.
        assert row is not None
        assert row.highlighted_text == "text"
        assert row.client_modified_at is None
        assert db_session.query(ub.Annotation).count() == 1
    elif expected == "missing":
        assert row.client_modified_at is None
    else:
        assert row.client_modified_at == datetime(2026, 8, 9, 14, 52, 21)


def test_malformed_client_clock_does_not_suppress_an_existing_annotation_edit(db_session):
    """A rejected clock is not an undated update.

    Once a row has a valid timestamp, collapsing a later malformed timestamp to
    the ordinary "missing" sentinel makes the stale-update guard discard the
    user's changed text and note.  Apply the edit by arrival order, but retain
    the last valid ordering hint.
    """
    from cps.services.annotation_sync import _upsert_annotation

    book = SimpleNamespace(id=5, uuid="b3d1b38b-74fd-43b7-a796-996e5a6a8b04")
    user = SimpleNamespace(id=7)
    row = _upsert_annotation(db_session, {
        "id": "bad-clock-existing",
        "highlightedText": "first",
        "noteText": "first note",
        "clientLastModifiedUtc": "2026-08-09T15:00:00Z",
    }, book, user)
    db_session.flush()

    updated = _upsert_annotation(db_session, {
        "id": "bad-clock-existing",
        "highlightedText": "edited",
        "noteText": "edited note",
        "clientLastModifiedUtc": "not-a-clock",
    }, book, user)

    assert updated is row
    assert row.highlighted_text == "edited"
    assert row.note_text == "edited note"
    assert row.client_modified_at == datetime(2026, 8, 9, 15, 0)


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


def test_equal_client_clock_suppresses_only_identical_retry(db_session):
    from cps.services.annotation_sync import _upsert_annotation
    book = SimpleNamespace(id=5, uuid="b3d1b38b-74fd-43b7-a796-996e5a6a8b04")
    user = SimpleNamespace(id=7)
    first = {
        "id": "same-second", "highlightedText": "first", "noteText": "note one",
        "clientLastModifiedUtc": "2026-08-09T15:00:00Z",
        "location": {"span": {"chapterFilename": "OEBPS/c.xhtml", "startChar": 1,
                                "endChar": 4, "startPath": "span#kobo.1.1",
                                "endPath": "span#kobo.1.1"}},
    }
    row = _upsert_annotation(db_session, first, book, user)
    db_session.flush()
    assert _upsert_annotation(db_session, dict(first), book, user) is None
    changed = dict(first, noteText="note two")
    row = _upsert_annotation(db_session, changed, book, user)
    assert row is not None
    assert row.note_text == "note two"
    assert row.client_modified_at == datetime(2026, 8, 9, 15, 0)


@pytest.mark.parametrize("value, book_uuid, accepted", [
    # A book whose Calibre uuid is not a canonical UUID forces its clients to
    # build a content_id the grammar calls malformed. Accept ONLY on an exact
    # match against the book's own record — direct proof of ownership, which is
    # what the UUID grammar stood in for. Everything else still bounces.
    ("bk-7!!c.html", "bk-7", True),    # exact match with our own record
    ("bk-8!!c.html", "bk-7", False),   # a different book
    ("bk-77!!c.html", "bk-7", False),  # prefix-similar, not equal
    ("bk-7!!", "bk-7", False),         # empty chapter
    ("../../etc/passwd", "bk-7", False),
    ("bk-7!!c.html", None, False),     # nothing to corroborate against
])
def test_non_uuid_book_id_accepted_only_on_exact_match(value, book_uuid, accepted):
    """Regression: validating content_id broke the KOReader push path.

    The rejection fired on the *book's* uuid — our own data, not the client's —
    so a legitimate push failed with a client-error type. Reject client input;
    never reject because our own identifier has an unexpected shape.
    """
    from cps.services.annotation_content_id import normalize_content_id, ContentIdError
    if accepted:
        assert normalize_content_id(value, book_uuid=book_uuid) == value
    else:
        with pytest.raises(ContentIdError):
            normalize_content_id(value, book_uuid=book_uuid)


def test_device_content_id_folds_on_the_first_bang_when_the_href_contains_one():
    """`!` is a legal character in an EPUB path segment, so the fold must split
    on the device grammar's separators and not on the last bang it can find.

    Guards a mutation the rest of the suite does not catch: widening
    ``_KOBO_DEVICE``'s opf-dir group from ``([^!]+)`` to ``(.+)`` makes the
    match greedy, and ``uuid!OEBPS!ch!apter.xhtml`` folds to
    ``OEBPS!ch/apter.xhtml`` — a silently wrong chapter path that resolves to
    nothing, with no error for the user to see.
    """
    from cps.services.annotation_content_id import normalize_content_id

    book = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"
    assert normalize_content_id(
        f"{book}!OEBPS!ch!apter.xhtml",
        book_uuid=book,
        allow_kobo_device_content_id=True,
    ) == f"{book}!!OEBPS/ch!apter.xhtml"


def test_device_content_id_uppercase_uuid_is_canonicalised():
    """A device uuid in upper case belongs to the same book and must normalise.

    Guards the mutation that drops ``_normal_uuid`` from the device branch:
    without it the raw device spelling is compared against the canonical book
    uuid, so the reader's own book is rejected as "does not belong to this
    book" — an annotation lost to letter case.
    """
    from cps.services.annotation_content_id import normalize_content_id

    book = "b3d1b38b-74fd-43b7-a796-996e5a6a8b04"
    assert normalize_content_id(
        f"{book.upper()}!OEBPS!chapter.xhtml",
        book_uuid=book,
        allow_kobo_device_content_id=True,
    ) == f"{book}!!OEBPS/chapter.xhtml"
