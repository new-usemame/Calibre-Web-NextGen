# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later

"""Synthetic-device coverage for Kobo stranded-entitlement reconciliation."""

import ast
import io
import inspect
import re
import sqlite3
import textwrap
from types import SimpleNamespace

import pytest
from flask import Flask
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


CONTENT_DDL = """
CREATE TABLE content (
    ContentID TEXT,
    ContentType TEXT,
    BookID TEXT,
    Title TEXT,
    Attribution TEXT,
    DownloadUrl TEXT,
    EntitlementId TEXT,
    Accessibility INTEGER,
    ___UserID TEXT,
    ___FileSize INTEGER,
    ISBN TEXT,
    EpubType INTEGER,
    VolumeIndex INTEGER
)
"""


def _write_device_db(path, rows):
    connection = sqlite3.connect(path)
    connection.execute(CONTENT_DDL)
    connection.executemany(
        "INSERT INTO content VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def device_uuids():
    return SimpleNamespace(
        absent="11111111-1111-4111-8111-111111111111",
        present="22222222-2222-4222-8222-222222222222",
        unresolved="33333333-3333-4333-8333-333333333333",
        scheduled="44444444-4444-4444-8444-444444444444",
        promos=tuple(
            "55555555-5555-4555-8555-{:012d}".format(index)
            for index in range(10)
        ),
        purchase="66666666-6666-4666-8666-666666666666",
    )


@pytest.fixture
def real_shape_content_db(tmp_path, device_uuids):
    u = device_uuids
    account_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    rows = [
        (u.absent, "6", None, "Deleted from CWNG", "Synthetic Author", "false", "",
         1, account_uuid, 421337, None, 1, -1),
        (u.present, "6", None, "Still in CWNG", "Library Author", "true", "",
         1, account_uuid, 521337, None, 1, -1),
        (u.unresolved, "6", None, "Lookup fails", "Unknown Author", "false", "",
         1, account_uuid, 621337, None, 1, -1),
        (u.scheduled, "6", None, "Already scheduled", "Scheduled Author", "true", "",
         1, account_uuid, 721337, None, 1, -1),
    ]
    rows.extend([
        (promo_uuid, "6", None, "Kobo promo tile {}".format(index + 1),
         "Promo Author", "false", "", -1, "", 0,
         "9780000000{:03d}".format(index), 13, index)
        for index, promo_uuid in enumerate(u.promos)
    ])
    rows.extend([
        # A purchased store book can plausibly share the downloaded-book
        # values, so these columns must not be treated as CWNG provenance.
        (u.purchase, "6", None, "Possible Kobo purchase", "Store Author", "true", "",
         1, account_uuid, 821337, "9780000000002", 1, -1),
        (u.absent + "!chapter.xhtml", "9", u.absent, "Chapter row", None, "false", "",
         1, account_uuid, 0, None, 1, -1),
        ("not-a-uuid", "6", None, "Malformed volume", None, "false", "",
         1, account_uuid, 10, None, 1, -1),
    ])
    return _write_device_db(tmp_path / "KoboReader.sqlite", rows)


@pytest.fixture
def app_session():
    from cps import ub

    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.mark.unit
def test_content_scan_excludes_measured_preview_rows_and_keeps_human_evidence(
        real_shape_content_db, device_uuids):
    from cps.services.kobo_import import parse_kobo_device_books

    scan = parse_kobo_device_books(real_shape_content_db)

    assert [(book.uuid, book.title) for book in scan.books] == [
        (device_uuids.scheduled, "Already scheduled"),
        (device_uuids.absent, "Deleted from CWNG"),
        (device_uuids.unresolved, "Lookup fails"),
        (device_uuids.purchase, "Possible Kobo purchase"),
        (device_uuids.present, "Still in CWNG"),
    ]
    books = {book.uuid: book for book in scan.books}
    assert books[device_uuids.absent].author == "Synthetic Author"
    assert books[device_uuids.absent].file_size == 421337
    assert books[device_uuids.absent].has_isbn is False
    assert books[device_uuids.purchase].author == "Store Author"
    assert books[device_uuids.purchase].file_size == 821337
    assert books[device_uuids.purchase].has_isbn is True
    assert scan.volume_rows == 16
    assert scan.skipped_invalid == 1
    assert scan.skipped_preview == 10
    assert scan.skipped_unclassified == 0


@pytest.mark.unit
def test_content_scan_skips_all_rows_when_accessibility_column_is_missing(tmp_path):
    from cps.services.kobo_import import parse_kobo_device_books

    path = tmp_path / "missing-accessibility.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE content (ContentID TEXT, ContentType TEXT, "
        "BookID TEXT, Title TEXT)"
    )
    connection.execute(
        "INSERT INTO content VALUES (?, '6', NULL, ?)",
        ("11111111-1111-4111-8111-111111111111", "Unclassified book"),
    )
    connection.commit()
    connection.close()

    scan = parse_kobo_device_books(path)

    assert scan.volume_rows == 1
    assert scan.books == ()
    assert scan.skipped_preview == 0
    assert scan.skipped_unclassified == 1


@pytest.mark.unit
def test_content_scan_marks_missing_optional_evidence_unknown(tmp_path):
    from cps.services.kobo_import import parse_kobo_device_books

    path = tmp_path / "missing-evidence.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE content (ContentID TEXT, ContentType TEXT, BookID TEXT, "
        "Title TEXT, Accessibility INTEGER)"
    )
    connection.execute(
        "INSERT INTO content VALUES (?, '6', NULL, ?, 1)",
        ("11111111-1111-4111-8111-111111111111", "Evidence unavailable"),
    )
    connection.commit()
    connection.close()

    scan = parse_kobo_device_books(path)

    assert len(scan.books) == 1
    assert scan.books[0].author is None
    assert scan.books[0].has_isbn is None
    assert scan.books[0].file_size is None


@pytest.mark.unit
def test_preview_filters_only_server_known_state_and_leaves_ownership_to_human(
        real_shape_content_db, device_uuids):
    from cps.services.kobo_import import parse_kobo_device_books
    from cps.services.kobo_reconcile import build_reconciliation_preview

    scan = parse_kobo_device_books(real_shape_content_db)

    def lookup(book_uuid):
        if book_uuid == device_uuids.present:
            return SimpleNamespace(id=900)
        if book_uuid == device_uuids.unresolved:
            raise RuntimeError("synthetic metadata failure")
        return None

    preview = build_reconciliation_preview(
        scan,
        existing_tombstone_uuids={device_uuids.scheduled},
        synced_book_uuids={device_uuids.purchase},
        book_lookup=lookup,
    )

    assert [(book.uuid, book.title) for book in preview.candidates] == [
        (device_uuids.absent, "Deleted from CWNG"),
        (device_uuids.purchase, "Possible Kobo purchase"),
    ]
    candidates = {book.uuid: book for book in preview.candidates}
    assert candidates[device_uuids.absent].synced is False
    assert candidates[device_uuids.purchase].synced is True
    assert preview.skipped_present == 1
    assert preview.skipped_unresolved == 1
    assert preview.already_scheduled == 1
    assert preview.skipped_invalid == 1
    assert preview.skipped_preview == 10
    assert preview.skipped_unclassified == 0


@pytest.mark.unit
def test_user_tombstone_writer_is_uuid_only_idempotent_and_user_scoped(
        app_session, device_uuids):
    from cps import kobo_sync_status, ub

    app_session.add_all([
        ub.KoboSyncedBooks(user_id=7, book_id=41),
        ub.KoboSyncedBooks(user_id=8, book_id=41),
    ])
    app_session.commit()

    first = kobo_sync_status.record_user_book_deletions(
        7, [device_uuids.absent], session=app_session)
    second = kobo_sync_status.record_user_book_deletions(
        7, [device_uuids.absent], session=app_session)

    assert first == 1
    assert second == 0
    assert app_session.query(ub.KoboDeletedBook).filter_by(
        user_id=7, book_uuid=device_uuids.absent).count() == 1
    # Historical device rows have no trustworthy book-id mapping. The
    # reconciler must not guess which legacy sync row to remove.
    assert app_session.query(ub.KoboSyncedBooks).filter_by(
        user_id=7, book_id=41).count() == 1
    assert app_session.query(ub.KoboSyncedBooks).filter_by(
        user_id=8, book_id=41).count() == 1


@pytest.mark.unit
def test_admin_handler_writes_only_explicitly_selected_preview_books(
        app_session, real_shape_content_db, device_uuids, monkeypatch):
    from cps import admin, ub

    user = ub.User(id=7, name="synthetic-user", email="synthetic@example.invalid")
    app_session.add_all([
        user,
        ub.KoboSyncedBooks(
            user_id=7, book_id=99, book_uuid=device_uuids.purchase),
    ])
    app_session.commit()

    monkeypatch.setattr(ub, "session", app_session)
    monkeypatch.setattr(admin.calibre_db, "get_book_by_uuid", lambda _uuid: None)
    monkeypatch.setattr(admin, "render_title_template", lambda _template, **context: context)
    monkeypatch.setattr(admin, "_", lambda message, **values: message % values if values else message)

    app = Flask(__name__)
    app.config.update(SECRET_KEY="synthetic-secret", TESTING=True)
    uploaded = real_shape_content_db.read_bytes()

    preview_handler = admin.kobo_reconcile.__wrapped__.__wrapped__
    with app.test_request_context(
            "/admin/user/7/kobo-reconcile", method="POST",
            data={"file": (io.BytesIO(uploaded), "KoboReader.sqlite")},
            content_type="multipart/form-data"):
        preview_context = preview_handler(7)

    assert app_session.query(ub.KoboDeletedBook).count() == 0
    assert {book.uuid for book in preview_context["preview"].candidates} == {
        device_uuids.absent,
        device_uuids.present,
        device_uuids.unresolved,
        device_uuids.scheduled,
        device_uuids.purchase,
    }
    candidates = {
        book.uuid: book for book in preview_context["preview"].candidates
    }
    assert preview_context["preview"].skipped_preview == 10
    assert candidates[device_uuids.purchase].synced is True
    assert candidates[device_uuids.absent].synced is False
    confirmation_token = preview_context["confirmation_token"]

    confirm_handler = admin.kobo_reconcile_confirm.__wrapped__.__wrapped__
    with app.test_request_context(
            "/admin/user/7/kobo-reconcile/confirm", method="POST",
            data={"confirmation_token": confirmation_token}):
        empty_result, empty_status = confirm_handler(7)
    with app.test_request_context(
            "/admin/user/7/kobo-reconcile/confirm", method="POST",
            data={
                "confirmation_token": confirmation_token,
                "book_uuid": "99999999-9999-4999-8999-999999999999",
            }):
        tampered_result, tampered_status = confirm_handler(7)

    assert empty_status == 400
    assert tampered_status == 400
    assert "error" in empty_result and "error" in tampered_result
    assert app_session.query(ub.KoboDeletedBook).count() == 0

    form = {
        "confirmation_token": confirmation_token,
        "book_uuid": device_uuids.absent,
    }
    with app.test_request_context(
            "/admin/user/7/kobo-reconcile/confirm", method="POST", data=form):
        first_result = confirm_handler(7)
    with app.test_request_context(
            "/admin/user/7/kobo-reconcile/confirm", method="POST", data=form):
        second_result = confirm_handler(7)

    assert first_result["result"]["written"] == 1
    assert second_result["result"]["written"] == 0
    assert app_session.query(ub.KoboDeletedBook).filter_by(user_id=7).all()[0].book_uuid == \
        device_uuids.absent


@pytest.mark.unit
def test_preview_template_requires_per_book_selection_and_warns_about_store_books(
        device_uuids):
    from cps.services.kobo_import import ParsedKoboBook
    from cps.services.kobo_reconcile import ReconciliationPreview

    def gettext(message, **values):
        return message % values if values else message

    environment = Environment(loader=ChoiceLoader([
        DictLoader({"layout.html": "{% block body %}{% endblock %}"}),
        FileSystemLoader("cps/templates"),
    ]))
    environment.globals.update(
        _=gettext,
        csrf_token=lambda: "synthetic-csrf",
        url_for=lambda endpoint, **_values: "/" + endpoint,
    )
    preview = ReconciliationPreview(
        candidates=(
            ParsedKoboBook(
                device_uuids.absent, "Deleted from CWNG",
                author="Synthetic Author", has_isbn=False,
                file_size=421337, synced=False),
            ParsedKoboBook(
                device_uuids.purchase, "Possible Kobo purchase",
                author="Store Author", has_isbn=True,
                file_size=821337, synced=True),
        ),
        volume_rows=2,
        skipped_invalid=0,
        skipped_preview=0,
        skipped_unclassified=0,
        skipped_present=0,
        skipped_unresolved=0,
        already_scheduled=0,
    )

    rendered = environment.get_template("kobo_reconcile.html").render(
        user=SimpleNamespace(id=7, name="synthetic-user"),
        preview=preview,
        confirmation_token="synthetic-confirmation",
        result=None,
        error=None,
        max_upload_mb=100,
    )

    assert rendered.count('name="book_uuid"') == 2
    assert 'value="{}"'.format(device_uuids.absent) in rendered
    assert 'value="{}"'.format(device_uuids.purchase) in rendered
    assert "checked" not in rendered
    assert "Kobo Store books may appear in the preview" in rendered
    assert "No genuine Kobo Store purchase was available for validation" in rendered
    assert "Preview and sample entries are excluded automatically" in rendered
    assert "Deleted from CWNG" in rendered
    assert "Possible Kobo purchase" in rendered
    assert "Synthetic Author" in rendered
    assert "Store Author" in rendered
    assert re.search(
        r"Synthetic Author.*?No.*?No.*?421337 bytes", rendered, re.DOTALL)
    assert re.search(
        r"Store Author.*?Yes.*?Yes.*?821337 bytes", rendered, re.DOTALL)
    assert "Recorded sync" in rendered
    assert "ISBN" in rendered


@pytest.mark.unit
def test_delivery_records_uuid_and_backfills_an_existing_sync_row(app_session, monkeypatch):
    from cps import kobo_sync_status, ub

    app_session.add(ub.KoboSyncedBooks(user_id=7, book_id=41))
    app_session.commit()
    monkeypatch.setattr(kobo_sync_status.ub, "session", app_session)
    monkeypatch.setattr(kobo_sync_status, "current_user", SimpleNamespace(id=7))

    kobo_sync_status.add_synced_books_batch([
        (41, "11111111-1111-4111-8111-111111111111"),
        (42, "22222222-2222-4222-8222-222222222222"),
    ])

    assert {
        (row.book_id, row.book_uuid)
        for row in app_session.query(ub.KoboSyncedBooks).filter_by(user_id=7).all()
    } == {
        (41, "11111111-1111-4111-8111-111111111111"),
        (42, "22222222-2222-4222-8222-222222222222"),
    }


@pytest.mark.unit
def test_sync_entitlement_trigger_passes_book_id_and_uuid_to_delivery_ledger():
    from cps import kobo

    tree = ast.parse(textwrap.dedent(inspect.getsource(kobo.HandleSyncRequest)))
    batch_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_synced_books_batch"
    ]
    assert len(batch_calls) == 1
    assert isinstance(batch_calls[0].args[0], ast.Name)
    identities_name = batch_calls[0].args[0].id

    identity_appends = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == identities_name
        and node.func.attr == "append"
    ]
    assert len(identity_appends) == 1
    identity = identity_appends[0].args[0]
    assert isinstance(identity, ast.Tuple) and len(identity.elts) == 2
    assert isinstance(identity.elts[0], ast.Attribute)
    assert identity.elts[0].attr == "id"
    assert isinstance(identity.elts[1], ast.Call)
    assert isinstance(identity.elts[1].func, ast.Name)
    assert identity.elts[1].func.id == "str"
    assert isinstance(identity.elts[1].args[0], ast.Attribute)
    assert identity.elts[1].args[0].attr == "uuid"


@pytest.mark.unit
def test_deletion_falls_back_to_uuid_recorded_at_delivery(app_session, device_uuids):
    from cps import kobo_sync_status, ub

    app_session.add(ub.KoboSyncedBooks(
        user_id=7, book_id=41, book_uuid=device_uuids.absent))
    app_session.commit()

    kobo_sync_status.record_book_deletion(41, None, session=app_session)

    tombstone = app_session.query(ub.KoboDeletedBook).one()
    assert (tombstone.user_id, tombstone.book_uuid) == (7, device_uuids.absent)
    assert app_session.query(ub.KoboSyncedBooks).count() == 0


@pytest.mark.unit
def test_kobo_synced_book_uuid_migration_adds_nullable_column_idempotently(tmp_path):
    from cps import ub

    engine = create_engine("sqlite:///{}".format(tmp_path / "legacy-app.db"), future=True)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE kobo_synced_books ("
            "id INTEGER PRIMARY KEY, user_id INTEGER, book_id INTEGER)"
        ))
    session = sessionmaker(bind=engine, future=True)()

    ub.migrate_kobo_synced_book_uuid(engine, session)
    ub.migrate_kobo_synced_book_uuid(engine, session)

    with engine.connect() as connection:
        columns = {
            row[1] for row in connection.execute(
                text("PRAGMA table_info(kobo_synced_books)"))
        }
    assert "book_uuid" in columns
    assert "migrate_kobo_synced_book_uuid" in inspect.getsource(ub.migrate_Database)
    session.close()
    engine.dispose()
