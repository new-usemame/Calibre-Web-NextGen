# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Out-of-range device clocks must never cost a user their Kobo highlight."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.kobo import parse_kobo_timestamp
from cps.services.annotation_sync import (
    parse_client_modified_utc,
    reset_registry_for_testing,
)

pytestmark = pytest.mark.unit

OVERFLOWING_CLOCKS = (
    pytest.param("9999-12-31T23:59:59-23:59", id="above-maxyear"),
    pytest.param("0001-01-01T00:00:00+23:59", id="below-minyear"),
)
BOOK_UUID = "9e5251ad-d530-4e58-9121-8b8336099fdd"


@pytest.fixture
def annotation_session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/app.db")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    user = ub.User(name="clock-user", email="clock@example.invalid", role=0, password="x")
    session.add(user)
    session.commit()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(ub, "session_commit", lambda: session.commit())
    reset_registry_for_testing()
    yield session, user
    reset_registry_for_testing()
    session.close()


@pytest.mark.parametrize("clock", OVERFLOWING_CLOCKS)
def test_annotation_patch_clock_parser_rejects_both_utc_overflows(clock):
    assert parse_client_modified_utc(clock) is None


@pytest.mark.parametrize("clock", OVERFLOWING_CLOCKS)
def test_kobo_reading_state_clock_parser_rejects_both_utc_overflows(clock):
    assert parse_kobo_timestamp(clock) is None


@pytest.mark.parametrize("clock", OVERFLOWING_CLOCKS)
def test_live_patch_stores_the_highlight_without_the_rejected_clock(
    annotation_session, monkeypatch, clock
):
    """The local-success response must correspond to a durable local row."""
    from cps import readingservices

    session, user = annotation_session
    book = SimpleNamespace(id=347, title="The Clock", uuid=BOOK_UUID)
    app = Flask(__name__)
    monkeypatch.setattr(readingservices, "current_user", user)
    monkeypatch.setattr(
        readingservices,
        "resolve_entitlement_ownership",
        lambda _entitlement_id: book,
    )
    monkeypatch.setattr(readingservices, "log_annotation_data", lambda *_args: None)
    monkeypatch.setattr(
        readingservices, "_owned_patch_is_local_authority",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        readingservices,
        "proxy_to_kobo_reading_services",
        lambda: pytest.fail("owned PATCH must not contact Kobo"),
    )
    payload = {
        "updatedAnnotations": [{
            "id": f"highlight-{clock[:4]}",
            "type": "highlight",
            "highlightedText": "This highlight must survive the rejected clock.",
            "highlightColor": "yellow",
            "clientLastModifiedUtc": clock,
            "location": {"span": {}},
        }],
    }

    with app.test_request_context(
        f"/api/v3/content/{BOOK_UUID}/annotations",
        method="PATCH",
        json=payload,
    ):
        response = readingservices.handle_annotations.__wrapped__(BOOK_UUID)

    assert response.status_code == 204
    assert response.get_data() == b""
    row = session.query(ub.Annotation).one()
    assert row.highlighted_text == "This highlight must survive the rejected clock."
    assert row.client_modified_at is None


def test_valid_annotation_clock_keeps_its_existing_naive_utc_value():
    assert parse_client_modified_utc("2026-08-20T15:30:45.123456+02:30") == datetime(
        2026, 8, 20, 13, 0, 45, 123456
    )


def test_valid_kobo_clock_keeps_its_existing_aware_utc_value():
    assert parse_kobo_timestamp("2026-08-20T15:30:45.123456+02:30") == datetime(
        2026, 8, 20, 13, 0, 45, 123456, tzinfo=timezone.utc
    )
