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
import itertools
import re
import time
from types import SimpleNamespace
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


_DEVICE_IDS = itertools.count(1)


def start(world, device="Kindle Paperwhite", address="192.168.1.23", device_id=None):
    """The device's first call. Each call is a different device unless
    ``device_id`` says otherwise."""
    device_id = device_id or "device-%d" % next(_DEVICE_IDS)
    response = world.client.post(
        "/kosync/pair/start", json={"device": device, "device_id": device_id},
        environ_base={"REMOTE_ADDR": address})
    return response


def look_up(world, user_code, *, name="alice", address="192.168.1.50"):
    """What the approval card shows a person at ``address``."""
    return world.browser(name, address=address).get(
        "/api/v1/devices/koreader/pair/%s" % user_code)


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


def test_waiting_codes_are_capped_per_network_even_with_the_limiter_off(world):
    codes = [start(world).get_json()["user_code"] for _ in range(5)]
    refused = start(world)
    assert refused.status_code == 429
    assert refused.get_json()["error"] == "too_many_requests"
    assert start(world, address="192.168.1.99").status_code == 200

    # A code answered on the website stops counting at once...
    answered = world.browser("alice").post("/api/v1/devices/koreader/pair/%s/deny" % codes[0])
    assert answered.status_code == 200
    assert start(world).status_code == 200
    assert start(world).status_code == 429
    # ...and codes that expired stop counting.
    world.advance(601)
    assert start(world).status_code == 200


def test_codes_waiting_elsewhere_never_stop_a_household_from_pairing(world):
    """Twenty addresses holding five waiting codes each filled a server-wide
    cap of 100, and every other e-reader was then refused for ten minutes,
    again and again. Only a network's own waiting codes can hold it back."""
    for network in range(1, 21):
        for _ in range(5):
            assert start(world, address="81.2.69.%d" % network).status_code == 200
    assert start(world, address="81.2.70.7").status_code == 200


def test_an_ipv6_network_counts_as_one_address(world):
    """One IPv6 connection comes with a whole /64 of addresses to pick from;
    the waiting-code cap counts the network, not each address in it."""
    for host in range(1, 6):
        assert start(world, address="2a02:8070:1:2::%x" % host).status_code == 200
    assert start(world, address="2a02:8070:1:2:ffff:ffff:ffff:ffff").status_code == 429
    assert start(world, address="2a02:8070:1:3::1").status_code == 200


def test_a_device_asking_again_replaces_its_own_waiting_code(world):
    """Cancelling on the e-reader tells the server nothing, so every retry
    left another code waiting, and five retries locked everyone behind that
    address out for ten minutes. A device's new code replaces its old one."""
    codes = [start(world, device_id="kindle-kids").get_json()["user_code"]
             for _ in range(8)]
    assert [look_up(world, code).status_code for code in codes] == [404] * 7 + [200]
    # The same device id from another network is another device's business.
    assert start(world, address="192.168.7.7", device_id="kindle-kids").status_code == 200
    assert look_up(world, codes[-1]).status_code == 200
    # Other devices on the network still have their places.
    for _ in range(4):
        assert start(world).status_code == 200


def test_an_ipv4_device_is_shown_by_its_ipv4_address_on_a_dual_stack_server(world):
    """A server listening on IPv6 and IPv4 at once sees an IPv4 device as
    ``::ffff:192.168.1.23`` (the rig showed ``::ffff:172.17.0.1``). The
    person approving knows the device as 192.168.1.23, and the per-address
    cap counts both spellings as one address."""
    body = start(world, address="::ffff:192.168.1.23").get_json()
    shown = world.browser("alice").get("/api/v1/devices/koreader/pair/%s" % body["user_code"])
    assert shown.get_json()["ip"] == "192.168.1.23"
    for _ in range(4):
        assert start(world, address="192.168.1.23").status_code == 200
    assert start(world, address="::ffff:192.168.1.23").status_code == 429

    other = start(world, address="2001:db8::17").get_json()
    shown = world.browser("alice").get("/api/v1/devices/koreader/pair/%s" % other["user_code"])
    assert shown.get_json()["ip"] == "2001:db8::17"


@pytest.mark.parametrize("device, person, same", [
    # Both on the server's own network, or both behind one home router.
    ("192.168.1.23", "192.168.1.50", True),
    ("81.2.69.142", "81.2.69.142", True),
    ("2a02:8070:1:2::17", "2a02:8070:1:2::99", True),
    # From the internet while the person is at home, or two places apart.
    ("81.2.69.142", "192.168.1.50", False),
    ("192.168.1.23", "81.2.69.142", False),
    ("81.2.69.142", "81.2.69.160", False),
    ("2a02:8070:1:2::17", "2a02:8070:9:9::1", False),
    # A phone on IPv6 and an e-reader on IPv4 cannot be compared, even with
    # the e-reader on the server's own network: a phone at home reaches a
    # server with a public IPv6 address over IPv6.
    ("81.2.69.142", "2a02:8070:1:2::99", None),
    ("192.168.1.23", "2a02:8070:1:2::99", None),
])
def test_the_approval_card_says_whether_the_device_is_on_your_network(world, device, person, same):
    """The name and address on the card come from the device, so neither
    proves anything; whether it asked from the network the person approving
    is on is something the server can see for itself."""
    body = start(world, address=device).get_json()
    shown = look_up(world, body["user_code"], address=person).get_json()
    assert shown["same_network"] is same


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


@pytest.fixture
def limiter_clock(monkeypatch):
    """The rate limiter's clock, moved by hand: ``limiter_clock.advance(seconds)``."""
    import limits.storage.memory as memory
    clock = SimpleNamespace(now=time.time())
    clock.advance = lambda seconds: setattr(clock, "now", clock.now + seconds)
    monkeypatch.setattr(memory, "time", SimpleNamespace(
        time=lambda: clock.now, monotonic=time.monotonic, sleep=time.sleep))
    return clock


def test_starting_pairing_holds_a_busy_network_back_for_a_minute_at_most(
        monkeypatch, tmp_path, limiter_clock):
    """Every device behind one home router, a carrier's shared address or the
    Docker Desktop network arrives from one address. Too many starts in one
    minute are refused with "wait a minute", which is then true: there is no
    daily allowance to run out of."""
    w = LibraryWorld(monkeypatch, tmp_path)
    w.enable_web(rate_limits=True)
    w.freeze_pairing_clock()
    try:
        answers = [start(w, device_id="kindle").status_code for _ in range(7)]
        assert answers == [200] * 6 + [429]
        refused = start(w, device_id="kindle").get_json()
        assert refused == {"error": "rate_limit_exceeded",
                           "message": "Too many pairing requests from this network. "
                                      "Wait a minute and try again."}
        assert start(w, address="192.168.1.99").status_code == 200

        limiter_clock.advance(60)
        assert start(w, device_id="kindle").status_code == 200
        # An evening of setting e-readers up, one every minute and a bit.
        for _ in range(60):
            limiter_clock.advance(61)
            assert start(w, device_id="kindle").status_code == 200

        # Picking another address from the same IPv6 /64 is the same network.
        limiter_clock.advance(61)
        answers = [start(w, address="2a02:8070:1:2::%x" % host, device_id="kindle").status_code
                   for host in range(1, 8)]
        assert answers == [200] * 6 + [429]
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


EARLIER_PAIRING_TABLE = """
CREATE TABLE koreader_pairing (
    id INTEGER NOT NULL PRIMARY KEY,
    user_code VARCHAR(8) NOT NULL UNIQUE,
    device_code_hash VARCHAR(64) NOT NULL UNIQUE,
    device_name VARCHAR(100) NOT NULL,
    requester_ip VARCHAR(64),
    status VARCHAR(16) NOT NULL,
    user_id INTEGER REFERENCES user (id) ON DELETE CASCADE,
    created_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    decided_at DATETIME,
    claimed_at DATETIME,
    last_poll_at DATETIME,
    app_password_id INTEGER REFERENCES user_app_password (id) ON DELETE SET NULL
)
"""


def test_a_pairing_table_from_an_earlier_build_is_brought_up_to_date(tmp_path, monkeypatch):
    """Boot the real migrator, twice, on an app.db whose pairing table was
    made before codes were counted per network, with a code waiting in it."""
    from datetime import datetime, timedelta
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from cps import config_sql, constants
    from cps.services import koreader_pairing

    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path), raising=False)
    engine = create_engine("sqlite:///%s" % (tmp_path / "app.db"), future=True)
    ub.Base.metadata.create_all(engine)
    config_sql._Settings.__table__.create(engine, checkfirst=True)
    now = datetime(2026, 9, 23, 20, 0, 0)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE koreader_pairing"))
        connection.execute(text(EARLIER_PAIRING_TABLE))
        connection.execute(text(
            "INSERT INTO koreader_pairing (user_code, device_code_hash, device_name, "
            "requester_ip, status, created_at, expires_at) VALUES ('BBBBCCCC', :hash, "
            "'Kindle', '192.168.1.23', 'pending', :created, :expires)"),
            {"hash": "ab" * 32, "created": now, "expires": now + timedelta(minutes=10)})

    session = sessionmaker(bind=engine, future=True)()
    try:
        ub.migrate_Database(session)
        ub.migrate_Database(session)
        indexes = {row[0] for row in session.execute(text(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND tbl_name = 'koreader_pairing'"))}
        assert "ix_koreader_pairing_requester_net" in indexes

        # The code already waiting still works, and new ones can be made.
        assert koreader_pairing.find_waiting("BBBB-CCCC", now=now, session=session)
        for _ in range(2):
            koreader_pairing.start("Kindle Kids", address="192.168.1.23", device_id="kk",
                                   now=now, session=session)
        waiting = session.query(ub.KOReaderPairing).filter_by(status="pending").all()
        assert {(row.device_id, row.requester_net) for row in waiting} == {
            (None, None), ("kk", "192.168.1.23")}
    finally:
        session.close()
        engine.dispose()
