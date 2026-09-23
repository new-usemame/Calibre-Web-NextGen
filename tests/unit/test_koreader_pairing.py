# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Connecting a KOReader device with a code, end to end.

The device calls the real public ``/kosync/pair/*`` routes, a signed-in person
answers through the real ``/api/v1/devices/koreader/pair/*`` routes, and the
password the device receives is tried on the real KOReader sign-in. The only
thing held still is the pairing clock, so expiry and poll spacing can be
walked through without sleeping.
"""

import hashlib
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from cps import ub
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit

CODE_RE = re.compile(r"^[BCDFGHJKMNPQRSTVWXZ2-9]{4}-[BCDFGHJKMNPQRSTVWXZ2-9]{4}$")


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = LibraryWorld(monkeypatch, tmp_path)
    w.enable_web()
    w.freeze_pairing_clock()
    w.add_user("alice", password="alice-account-password")
    w.add_user("bob", password="bob-account-password")
    yield w
    w.close()


def start(world, device="Kindle Paperwhite", address="192.168.1.23"):
    response = world.client.post(
        "/kosync/pair/start", json={"device": device, "device_id": "kpw-1"},
        environ_base={"REMOTE_ADDR": address})
    return response


def poll(world, device_code, *, wait=5):
    world.advance(wait)
    return world.client.post("/kosync/pair/poll", json={"device_code": device_code})


def signs_in(world, username, password):
    return world.client.get("/kosync/users/auth",
                            headers=world.basic(username, password)).status_code == 200


def stored_values(world):
    """Every value app.db holds for pairings and app passwords, as text."""
    values = []
    for model in (ub.KOReaderPairing, ub.UserAppPassword):
        for row in world.session.query(model).all():
            values.extend(str(getattr(row, column.name))
                          for column in model.__table__.columns)
    return values


def test_an_approved_code_connects_the_device_exactly_once(world):
    started = start(world)
    assert started.status_code == 200
    assert "no-store" in started.headers["Cache-Control"]
    body = started.get_json()
    assert CODE_RE.match(body["user_code"])
    assert body["expires_in"] == 600 and body["interval"] == 5
    typed = body["user_code"].replace("-", "")
    assert body["verify_url"] == "http://localhost/pair"
    assert body["verify_url_complete"] == "http://localhost/pair?code=" + typed

    assert poll(world, body["device_code"]).get_json() == {"status": "pending", "interval": 5}

    alice = world.browser("alice")
    # People type codes loosely: lower case, the dash or none.
    shown = alice.get("/api/v1/devices/koreader/pair/%s" % body["user_code"].lower())
    assert shown.status_code == 200
    assert shown.get_json()["device_name"] == "Kindle Paperwhite"
    assert shown.get_json()["ip"] == "192.168.1.23"
    assert shown.get_json()["requested_at"] == "2026-09-01T12:00:00Z"
    approved = alice.post("/api/v1/devices/koreader/pair/%s/approve" % typed)
    assert approved.status_code == 200 and approved.get_json()["status"] == "approved"

    claim = poll(world, body["device_code"])
    assert claim.status_code == 200
    assert "no-store" in claim.headers["Cache-Control"]
    credentials = claim.get_json()
    assert credentials["status"] == "approved"
    assert credentials["server"] == "http://localhost"
    assert credentials["username"] == "alice"
    assert signs_in(world, "alice", credentials["password"])
    rows = world.session.query(ub.UserAppPassword).all()
    assert [(row.user_id, row.label) for row in rows] == [
        (world.session.query(ub.User).filter_by(name="alice").one().id,
         "KOReader: Kindle Paperwhite")]

    # The password went out once; the code is spent.
    again = poll(world, body["device_code"])
    assert again.status_code == 410 and again.get_json()["status"] == "expired"
    assert "password" not in again.get_json()
    assert world.session.query(ub.UserAppPassword).count() == 1


def test_neither_the_device_code_nor_the_password_is_kept_in_the_clear(world):
    body = start(world).get_json()
    alice = world.browser("alice")
    alice.post("/api/v1/devices/koreader/pair/%s/approve" % body["user_code"])
    password = poll(world, body["device_code"]).get_json()["password"]

    row = world.session.query(ub.KOReaderPairing).one()
    assert row.device_code_hash == hashlib.sha256(body["device_code"].encode()).hexdigest()
    for value in stored_values(world):
        assert body["device_code"] not in value
        assert password not in value


def test_a_declined_code_never_yields_a_password(world):
    body = start(world).get_json()
    alice = world.browser("alice")
    declined = alice.post("/api/v1/devices/koreader/pair/%s/deny" % body["user_code"])
    assert declined.status_code == 200 and declined.get_json()["status"] == "denied"

    assert poll(world, body["device_code"]).get_json() == {"status": "denied"}
    late = alice.post("/api/v1/devices/koreader/pair/%s/approve" % body["user_code"])
    assert late.status_code == 409
    assert late.get_json()["error"]["code"] == "already_decided"
    assert poll(world, body["device_code"]).get_json() == {"status": "denied"}
    assert world.session.query(ub.UserAppPassword).count() == 0


def test_a_code_nobody_answers_expires_after_ten_minutes(world):
    body = start(world).get_json()
    alice = world.browser("alice")
    world.advance(9 * 60 + 58)
    assert alice.get("/api/v1/devices/koreader/pair/%s" % body["user_code"]).status_code == 200

    world.advance(3)
    missing = alice.get("/api/v1/devices/koreader/pair/%s" % body["user_code"])
    assert missing.status_code == 404
    assert missing.get_json()["error"]["code"] == "not_found"
    assert alice.post("/api/v1/devices/koreader/pair/%s/approve"
                      % body["user_code"]).status_code == 404
    expired = poll(world, body["device_code"])
    assert expired.status_code == 410 and expired.get_json()["status"] == "expired"
    assert world.session.query(ub.UserAppPassword).count() == 0


def test_once_answered_nobody_else_can_answer_or_take_over(world):
    body = start(world).get_json()
    alice, bob = world.browser("alice"), world.browser("bob")
    assert alice.post("/api/v1/devices/koreader/pair/%s/approve"
                      % body["user_code"]).status_code == 200
    for action in ("approve", "deny"):
        answer = bob.post("/api/v1/devices/koreader/pair/%s/%s" % (body["user_code"], action))
        assert answer.status_code == 409
    assert bob.get("/api/v1/devices/koreader/pair/%s" % body["user_code"]).status_code == 409

    assert poll(world, body["device_code"]).get_json()["username"] == "alice"


def test_two_answers_racing_for_one_code_only_the_first_counts(world, monkeypatch):
    from cps.services import koreader_pairing
    body = start(world).get_json()
    bob_id = world.session.query(ub.User).filter_by(name="bob").one().id
    lookup = koreader_pairing.find_waiting

    def bob_answers_in_between(*args, **kwargs):
        row = lookup(*args, **kwargs)
        # Bob's approval commits after alice's lookup, before her update.
        world.session.query(ub.KOReaderPairing).filter_by(id=row.id).update(
            {"status": "approved", "user_id": bob_id})
        world.session.commit()
        return row

    monkeypatch.setattr(koreader_pairing, "find_waiting", bob_answers_in_between)
    late = world.browser("alice").post("/api/v1/devices/koreader/pair/%s/approve"
                                       % body["user_code"])
    assert late.status_code == 409
    assert poll(world, body["device_code"]).get_json()["username"] == "bob"


def test_two_polls_racing_for_one_approval_get_one_password(world, monkeypatch):
    from cps.services import koreader_pairing
    body = start(world).get_json()
    world.browser("alice").post("/api/v1/devices/koreader/pair/%s/approve" % body["user_code"])
    claim = koreader_pairing._claim
    first = []

    def another_poll_claims_first(session, row, now):
        first.append(claim(session, row, now))
        return claim(session, row, now)

    monkeypatch.setattr(koreader_pairing, "_claim", another_poll_claims_first)
    ours = poll(world, body["device_code"])
    assert first[0].status == "approved" and first[0].password
    assert ours.status_code == 410 and "password" not in ours.get_json()
    assert world.session.query(ub.UserAppPassword).count() == 1


def test_polling_too_fast_is_slowed_down_before_anything_is_revealed(world):
    body = start(world).get_json()
    assert poll(world, body["device_code"]).status_code == 200
    world.browser("alice").post("/api/v1/devices/koreader/pair/%s/approve" % body["user_code"])

    hurried = poll(world, body["device_code"], wait=1)
    assert hurried.status_code == 429
    assert hurried.get_json()["status"] == "slow_down"
    assert "password" not in hurried.get_json()
    assert world.session.query(ub.UserAppPassword).count() == 0

    patient = poll(world, body["device_code"], wait=3)
    assert patient.get_json()["status"] == "approved"


def test_unknown_or_malformed_device_codes_learn_nothing(world):
    unknown = world.client.post("/kosync/pair/poll", json={"device_code": "x" * 43})
    assert unknown.status_code == 404 and unknown.get_json()["status"] == "expired"
    for bad in ({}, {"device_code": 12}, {"device_code": "short"}, {"device_code": "é" * 43}):
        assert world.client.post("/kosync/pair/poll", json=bad).status_code == 400
    for bad in ({}, {"device": ""}, {"device": "Kindle", "device_id": ""},
                {"device": "K" * 101}):
        assert world.client.post("/kosync/pair/start", json=bad).status_code == 400


def test_waiting_codes_are_capped_per_address_even_with_the_limiter_off(world):
    for _ in range(5):
        assert start(world).status_code == 200
    refused = start(world)
    assert refused.status_code == 429
    assert refused.get_json()["error"] == "too_many_requests"
    assert start(world, address="192.168.1.99").status_code == 200

    # Codes that expired stop counting.
    world.advance(601)
    assert start(world).status_code == 200


def test_a_signed_out_visitor_or_guest_cannot_answer_a_code(world, monkeypatch):
    from cps import config
    body = start(world).get_json()
    visitor = world.browser()
    assert visitor.get("/api/v1/devices/koreader/pair/%s" % body["user_code"]).status_code == 401
    assert visitor.post("/api/v1/devices/koreader/pair/%s/approve"
                        % body["user_code"]).status_code == 401
    # Anonymous browsing lets a guest past the API gate; the guest still
    # has no account to connect a device to.
    monkeypatch.setattr(config, "config_anonbrowse", 1, raising=False)
    assert visitor.post("/api/v1/devices/koreader/pair/%s/approve"
                        % body["user_code"]).status_code == 401
    assert world.session.query(ub.KOReaderPairing).one().status == "pending"


def test_pairing_follows_the_koreader_sync_switch(world):
    body = start(world).get_json()
    alice = world.browser("alice")
    assert alice.get("/api/v1/auth/me").get_json()["features"]["koreader_sync"] is True

    world.sync_switch(False)
    assert start(world).status_code == 503
    assert world.client.post("/kosync/pair/poll",
                             json={"device_code": body["device_code"]}).status_code == 503
    refused = alice.post("/api/v1/devices/koreader/pair/%s/approve" % body["user_code"])
    assert refused.status_code == 409
    assert refused.get_json()["error"]["code"] == "koreader_sync_disabled"
    assert alice.get("/api/v1/auth/me").get_json()["features"]["koreader_sync"] is False


def test_no_code_is_handed_out_when_there_is_no_website_to_approve_it(monkeypatch, tmp_path):
    w = LibraryWorld(monkeypatch, tmp_path)
    w.enable_web(spa_available=False)
    try:
        refused = start(w)
        assert refused.status_code == 409
        assert refused.get_json()["error"] == "pairing_unavailable"
        assert w.session.query(ub.KOReaderPairing).count() == 0
    finally:
        w.close()


def test_the_short_pair_address_opens_the_e_readers_page(world):
    alice = world.browser("alice")
    plain = alice.get("/pair")
    assert plain.status_code == 302
    assert plain.headers["Location"].endswith("/app/account/devices?pair=1")
    from_qr = alice.get("/pair?code=k7m4-qx2p")
    assert from_qr.headers["Location"].endswith("/app/account/devices?pair=1&code=K7M4QX2P")
    junk = alice.get('/pair?code="><script>')
    assert junk.headers["Location"].endswith("/app/account/devices?pair=1")
    mounted = alice.get("/pair?code=K7M4QX2P", base_url="http://localhost/books")
    assert mounted.headers["Location"].endswith("/books/app/account/devices?pair=1&code=K7M4QX2P")


def test_a_signed_out_phone_signs_in_first_and_keeps_the_code(world):
    # The phone that scans the QR code is often not signed in. The web app
    # returns to ``next`` after signing in; landing on the e-readers page
    # signed out would drop the code on the way through the sign-in.
    for client in (world.client, world.browser()):
        scanned = client.get("/pair?code=k7m4-qx2p")
        assert scanned.status_code == 302
        target = urlsplit(scanned.headers["Location"])
        assert target.path == "/app/login"
        assert parse_qs(target.query) == {"next": ["/app/account/devices?pair=1&code=K7M4QX2P"]}
    mounted = world.client.get("/pair", base_url="http://localhost/books")
    target = urlsplit(mounted.headers["Location"])
    assert target.path == "/books/app/login"
    assert parse_qs(target.query) == {"next": ["/books/app/account/devices?pair=1"]}


def test_deleting_an_account_takes_its_app_passwords_and_pairings(world):
    from cps import constants
    from cps.admin import _delete_user

    world.add_user("admin", password="admin-password").role = constants.ROLE_ADMIN
    world.session.commit()
    body = start(world).get_json()
    world.browser("alice").post("/api/v1/devices/koreader/pair/%s/approve" % body["user_code"])
    password = poll(world, body["device_code"]).get_json()["password"]
    alice = world.session.query(ub.User).filter_by(name="alice").one()
    alice_id = alice.id

    with world.app.test_request_context():
        _delete_user(alice)

    assert world.session.query(ub.UserAppPassword).filter_by(user_id=alice_id).count() == 0
    assert world.session.query(ub.KOReaderPairing).filter_by(user_id=alice_id).count() == 0
    assert not signs_in(world, "alice", password)


def test_the_web_lookup_is_rate_limited_per_account(monkeypatch, tmp_path):
    w = LibraryWorld(monkeypatch, tmp_path)
    w.enable_web(rate_limits=True)
    try:
        w.add_user("alice")
        w.add_user("bob")
        alice, bob = w.browser("alice"), w.browser("bob")
        answers = [alice.get("/api/v1/devices/koreader/pair/BBBB-BBBB").status_code
                   for _ in range(11)]
        assert answers == [404] * 10 + [429]
        # Another account is not held back by alice's guessing.
        assert bob.get("/api/v1/devices/koreader/pair/BBBB-BBBB").status_code == 404
    finally:
        w.close()


def test_starting_pairing_is_rate_limited_per_address(monkeypatch, tmp_path):
    w = LibraryWorld(monkeypatch, tmp_path)
    w.enable_web(rate_limits=True)
    w.freeze_pairing_clock()
    try:
        answers = []
        for _ in range(7):
            answers.append(start(w).status_code)
            w.advance(601)  # keep the waiting-code cap out of the way
        assert answers == [200] * 6 + [429]
        assert start(w, address="192.168.1.99").status_code == 200
    finally:
        w.close()


def test_startup_cleanup_drops_expired_codes_only(world):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for code, expires in (("BBBBBBBB", now - timedelta(minutes=1)),
                          ("CCCCCCCC", now + timedelta(minutes=5))):
        world.session.add(ub.KOReaderPairing(
            user_code=code, device_code_hash=code * 8, device_name="Kindle",
            status="pending", created_at=expires - timedelta(minutes=10),
            expires_at=expires))
    world.session.commit()

    ub.clean_database(world.session)

    assert [row.user_code for row in world.session.query(ub.KOReaderPairing)] == ["CCCCCCCC"]
