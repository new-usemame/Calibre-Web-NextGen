# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Share my place PR 5: a Kobo is served only its own highlights.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 17 (with
section 3 items 9-12). Read as a spec, for ``GET
/api/v3/content/<entitlement>/annotations`` when the user has two-way Kobo
annotation sync on:

* An identified Kobo (``g.annotation_origin_device_id``, which the auth
  decorator resolves from ``x-kobo-deviceid``) gets the visible rows it created
  plus legacy no-origin ``kobo`` rows, on every render path: live, the
  emergency renderer, the sticky last-served snapshot and the post-download
  restore. Web-reader, KOReader and other-Kobo rows never reach it, and its own
  deleted rows are absent (Kobo mirrors a deletion by omission).
* A snapshot built earlier from every origin is never replayed to it. When no
  own-row answer can be built, the route answers 503 as it does today; it
  never answers 200 with an empty or foreign set.
* An unresolved Kobo keeps today's path.

The route is driven as the existing #1923 tests drive it: the decorator's
identity is set on ``g`` and the undecorated handler runs over a real SQLite
app database.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask, g, make_response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
import cps.readingservices as rs
from cps.services import kobo_annotation_authority as authority
from cps.services import kobo_post_download_restore as ledger

pytestmark = pytest.mark.unit

OWNED = "5d7b3a52-6c0e-4b9e-9a0d-1f2e3d4c5b6a"
BOOK_ID = 412
USER_ID = 5
KOBO_A, KOBO_B, KINDLE, BROWSER = 31, 32, 33, 34
SWITCH = "CWNG_SHARE_PLACE_GATES"


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    ub.Base.metadata.create_all(engine)
    database = sessionmaker(bind=engine, future=True)()
    monkeypatch.setattr(ub, "session", database)
    monkeypatch.setattr(ub, "session_commit", lambda: database.commit() or True)
    for device_id, kind, name in ((KOBO_A, "kobo", "Libra"), (KOBO_B, "kobo", "Clara"),
                                  (KINDLE, "koreader", "Kindle"),
                                  (BROWSER, "webreader", "Browser")):
        database.add(ub.Device(id=device_id, user_id=USER_ID, kind=kind,
                               display_name=name, active=True, created_by="auto"))
    database.commit()
    yield database
    database.close()
    engine.dispose()


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv(SWITCH, raising=False)
    application = Flask(__name__)
    monkeypatch.setattr(rs, "current_user", SimpleNamespace(
        id=USER_ID, name="reader", is_authenticated=True,
        kobo_two_way_annotation_sync=True,
    ))
    monkeypatch.setattr(rs.config, "config_kobo_two_way_annotation_sync", True, raising=False)
    monkeypatch.setattr("cps.services.kobo_annotation_stage0.schema_capable", lambda _e: True)
    monkeypatch.setattr(rs, "_begin_exchange_capture", lambda *_a, **_k: None)
    authority.reset_skip_log_for_testing()
    book = SimpleNamespace(id=BOOK_ID, uuid=OWNED, title="Middlemarch", identifiers=[])
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _c: book)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("this GET must be answered by CWNG, not Kobo"),
    )
    return application


def _row(annotation_id, origin, source, *, hidden=False):
    return ub.Annotation(
        user_id=USER_ID, book_id=BOOK_ID, annotation_id=annotation_id,
        source=source, origin_device_id=origin, annotation_type="highlight",
        highlighted_text="passage " + annotation_id, highlight_color="#F6F3B3",
        note_text=None, content_id=f"{OWNED}!!OEBPS/chapter.xhtml",
        chapter_progress=0.25, start_container_path="span#kobo\\.3\\.1",
        end_container_path="span#kobo\\.3\\.1", start_offset=0, end_offset=12,
        context_string="", client_modified_at=datetime(2026, 10, 1, 8, 0, 0),
        hidden=hidden, content_revision=1,
    )


A_OWN = {"a-1", "a-2", "legacy-kobo"}
B_OWN = {"b-1", "legacy-kobo"}
EVERY_VISIBLE = {"a-1", "a-2", "b-1", "kindle-1", "web-1", "legacy-kobo",
                 "legacy-koreader", "legacy-web"}


def _rows(session):
    session.add_all([
        _row("a-1", KOBO_A, "kobo"),
        _row("a-2", KOBO_A, "kobo"),
        _row("a-deleted", KOBO_A, "kobo", hidden=True),
        _row("b-1", KOBO_B, "kobo"),
        _row("kindle-1", KINDLE, "koreader"),
        _row("web-1", BROWSER, "webreader"),
        _row("legacy-kobo", None, "kobo"),
        _row("legacy-koreader", None, "koreader"),
        _row("legacy-web", None, "webreader"),
    ])
    session.commit()


def _book_state(session, *, sticky):
    state = ub.KoboAnnotationBookState(
        user_id=USER_ID, book_id=BOOK_ID, content_id=OWNED,
        authority_status="authoritative" if sticky else "unseeded",
        authority_revision=1 if sticky else 0,
        generation_id="00000000-0000-0000-0000-000000000412",
        opaque_content_status="absent" if sticky else "unknown",
        seeded_at=datetime(2026, 10, 1, tzinfo=timezone.utc) if sticky else None,
    )
    if sticky:
        state.ever_authoritative = True
    session.add(state)
    session.commit()
    return state


@pytest.fixture
def sticky_world(app, session):
    """CWNG is already this book's annotation authority (the usual steady state)."""
    _book_state(session, sticky=True)
    _rows(session)
    return app, session


def _get(app, device_id):
    with app.test_request_context(f"/api/v3/content/{OWNED}/annotations?limit=100"):
        g.annotation_origin_device_id = device_id
        return rs.handle_annotations.__wrapped__(OWNED)


def _ids(response):
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = json.loads(response.get_data())
    assert payload["nextPageOffsetToken"] is None
    return [row["id"] for row in payload["annotations"]]


def _pre_upgrade_snapshot(app):
    """The last-served snapshot as a server before PR 5 left it: built from
    every origin. An unresolved device's render is still that today."""
    with app.app_context():
        rendered = authority.render_owned_annotations(
            user_id=USER_ID, book_id=BOOK_ID, entitlement_id=OWNED,
            page_limit=100, device_id=None, log=logging.getLogger("test"),
        )
    assert rendered is not None
    body, _etag = rendered
    assert {row["id"] for row in json.loads(body)["annotations"]} == EVERY_VISIBLE
    return body


def _own_or_unavailable(response, own):
    """What a fallback may answer: exactly this device's own set, or today's
    503. Never an empty 200 (it would wipe the device) and never a foreign row."""
    if response.status_code == 503:
        return
    ids = _ids(response)
    assert ids, "an empty 200 empties the book on the Kobo"
    assert set(ids) == own


# == 3. Live render ==========================================================

def test_identified_kobo_gets_only_its_own_visible_rows_and_legacy_kobo_rows(sticky_world):
    app, _session = sticky_world

    ids = _ids(_get(app, KOBO_A))

    assert set(ids) == A_OWN
    assert len(ids) == len(A_OWN)


def test_each_kobo_gets_its_own_rows_not_the_other_kobos(sticky_world):
    app, _session = sticky_world

    a = set(_ids(_get(app, KOBO_A)))
    b = set(_ids(_get(app, KOBO_B)))

    assert a == A_OWN
    assert b == B_OWN


def test_an_own_deletion_is_mirrored_by_omission(sticky_world):
    app, session = sticky_world
    assert "a-2" in _ids(_get(app, KOBO_A))

    row = session.query(ub.Annotation).filter_by(annotation_id="a-2").one()
    row.hidden = True
    row.content_revision += 1
    session.commit()

    assert set(_ids(_get(app, KOBO_A))) == A_OWN - {"a-2"}


def test_switching_place_sharing_off_does_not_bring_back_other_devices_rows(
        sticky_world, monkeypatch):
    monkeypatch.setenv(SWITCH, "0")
    app, _session = sticky_world

    assert set(_ids(_get(app, KOBO_A))) == A_OWN


# == 3. Emergency renderer and error paths ====================================

def test_emergency_render_serves_only_own_rows(sticky_world, monkeypatch):
    """The joined query fails; the simpler emergency query answers instead."""
    app, _session = sticky_world

    def joined_query_fails(*_a, **_k):
        raise RuntimeError("materialization join failed")

    monkeypatch.setattr(authority, "_annotation_rows", joined_query_fails)

    assert set(_ids(_get(app, KOBO_A))) == A_OWN


def test_when_no_query_works_an_all_origin_snapshot_is_not_replayed(
        sticky_world, monkeypatch):
    """Both row queries fail, so today's code replays the last-served snapshot.
    One built before the upgrade holds every origin's rows."""
    app, _session = sticky_world
    _pre_upgrade_snapshot(app)

    def query_fails(*_a, **_k):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(authority, "_annotation_rows", query_fails)
    monkeypatch.setattr(authority, "_simple_annotation_rows", query_fails)

    _own_or_unavailable(_get(app, KOBO_A), A_OWN)


# == 3. Sticky snapshot ======================================================

def _no_live_proof(monkeypatch):
    """The device pre-serve proof cannot be established: the route falls back
    to the last-served snapshot (readingservices' STICKY_GET_SNAPSHOT branch)."""
    monkeypatch.setattr(authority, "prepare_authoritative_device_get",
                        lambda **_k: authority.STICKY_GET_SNAPSHOT)


def test_a_pre_upgrade_all_origin_snapshot_is_never_replayed_to_an_identified_kobo(
        sticky_world, monkeypatch):
    app, _session = sticky_world
    _pre_upgrade_snapshot(app)
    _no_live_proof(monkeypatch)

    _own_or_unavailable(_get(app, KOBO_A), A_OWN)


def test_one_kobos_snapshot_is_never_replayed_to_another_kobo(sticky_world, monkeypatch):
    app, _session = sticky_world
    assert set(_ids(_get(app, KOBO_A))) == A_OWN  # leaves a last-served snapshot
    _no_live_proof(monkeypatch)

    _own_or_unavailable(_get(app, KOBO_B), B_OWN)


def test_unresolved_kobo_keeps_todays_snapshot_path(sticky_world):
    """No registered device behind the request: today's sticky path replays the
    last complete set, every origin included (section 8)."""
    app, _session = sticky_world
    body = _pre_upgrade_snapshot(app)

    response = _get(app, None)

    assert response.status_code == 200
    assert response.get_data() == body


# == 3. Post-download restore ================================================

@pytest.fixture
def downloaded_world(app, session):
    """Not yet authoritative; Kobo A has just downloaded the book, so Nickel
    emptied its local set and the next GET is answered from CWNG's rows."""
    _book_state(session, sticky=False)
    _rows(session)
    return app, session


def test_post_download_restore_serves_only_the_devices_own_rows(downloaded_world):
    app, session = downloaded_world
    ledger.record_download(device_id=KOBO_A, book_id=BOOK_ID, book_format="kepub",
                           log=logging.getLogger("test"))

    ids = _ids(_get(app, KOBO_A))

    assert set(ids) == A_OWN
    session.expire_all()
    download = session.query(ub.KoboDeviceBookDownload).one()
    assert (download.restore_state, download.restored_count) == ("served", len(A_OWN))


# == 17a.3 An identified Kobo never gets another device's rows ==============
# Spec 17a.3, narrowed 2026-10-12: when CWNG's local set is proven complete
# (authoritative, or a post-download restore), an identified Kobo gets only its
# own rows; with none it gets a valid empty set, not Kobo's upstream body.
# When the local set is NOT proven complete, the upstream body is the only
# complete source, so today's proxy stands even for an identified Kobo (pinned
# by the five proxy tests in test_1923_owned_annotations_local_authority.py).

UPSTREAM = b'{"annotations":[{"id":"b-1"},{"id":"cloud-only"}],"nextPageOffsetToken":null}'


def _upstream_must_not_be_called(monkeypatch):
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_k: pytest.fail("a proven-complete local set must not be replaced by Kobo's"))


def _upstream_answers(monkeypatch):
    monkeypatch.setattr(rs, "proxy_to_kobo_reading_services",
                        lambda **_k: make_response(UPSTREAM, 200, {"ETag": 'W/"kobo"'}))


def _others_only(session):
    session.add_all([_row("kindle-1", KINDLE, "koreader"),
                     _row("web-1", BROWSER, "webreader"),
                     _row("b-1", KOBO_B, "kobo")])
    session.commit()


def _clean_own_answer(response):
    """A valid sync answer with nothing of the device's own: 200, a complete
    page, no foreign row."""
    assert response.status_code == 200, response.get_data(as_text=True)
    payload = json.loads(response.get_data())
    assert payload["nextPageOffsetToken"] is None
    assert [row["id"] for row in payload["annotations"]] == []


def test_after_a_download_with_no_own_rows_the_kobo_gets_a_clean_empty_set(
        app, session, monkeypatch):
    """Right after a download the device's set is empty by construction
    (render_post_download_restore), so the empty set is safe; the upstream
    set, which holds another Kobo's row, is not. (Luna's review probe.)"""
    _book_state(session, sticky=False)
    _others_only(session)
    ledger.record_download(device_id=KOBO_A, book_id=BOOK_ID, book_format="kepub",
                           log=logging.getLogger("test"))
    _upstream_must_not_be_called(monkeypatch)

    _clean_own_answer(_get(app, KOBO_A))


def test_an_authoritative_book_with_no_own_rows_gives_the_kobo_a_clean_empty_set(
        app, session, monkeypatch):
    """Live render: CWNG is the book's authority and the account's set is
    non-empty, but none of it is this Kobo's."""
    _book_state(session, sticky=True)
    _others_only(session)
    _upstream_must_not_be_called(monkeypatch)

    _clean_own_answer(_get(app, KOBO_A))


def test_without_a_download_or_authority_an_identified_kobo_still_proxies(
        app, session, monkeypatch):
    """The same household as the download test, minus the download: the local
    set is not proven complete, so Kobo's upstream body (its own store of what
    the account's Kobos uploaded) is the answer, own rows or not."""
    _book_state(session, sticky=False)
    _rows(session)
    _upstream_answers(monkeypatch)

    assert _get(app, KOBO_A).get_data() == UPSTREAM


def test_an_unresolved_kobo_with_no_rows_of_its_own_still_proxies(app, session, monkeypatch):
    """Control (section 8): no registered device behind the request keeps
    today's proxy."""
    _book_state(session, sticky=False)
    _others_only(session)
    _upstream_answers(monkeypatch)

    response = _get(app, None)

    assert response.get_data() == UPSTREAM
