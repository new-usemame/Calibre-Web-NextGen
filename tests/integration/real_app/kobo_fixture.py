"""Seeding helpers for the Kobo cases that run against the real application.

Every identifier here is synthetic. Nothing in this file, or in anything it
writes, names a real reader, device, host or book.

These helpers deliberately build state with the *product's own* writers where
one exists (``register_kobo_device_best_effort`` for the device row, the route
itself for the durable annotation snapshot). A snapshot hand-written by the
test would let a test assert the bytes the test itself authored; a snapshot the
route committed is evidence about the route.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone

# ``W/"CWNG:<generation-uuid>:<revision>:<16 hex of the body digest>"`` --
# built at cps/services/kobo_annotation_authority.py:1053 for the durable
# ``cwng_revision`` form and :326 for the transient form. Both share this
# shape, so matching it proves the header is CWNG's replacement-set ETag and
# not, say, a passed-through Kobo manifest ETag.
CWNG_ETAG = re.compile(
    r'^W/"CWNG:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}'
    r'-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}:[0-9]+:[0-9a-f]{16}"$'
)

READER_NAME = "fixture-reader"
READER_PASSWORD = "fixture-reader-passphrase"
# cps/services/device_registry.py:60 accepts a Kobo device id only as 64 hex
# characters, so a readable placeholder string is silently ignored and no
# device row is ever created.
DEVICE_RAW_ID = "a1" * 32
DEVICE_HEADERS = {
    "x-kobo-deviceid": DEVICE_RAW_ID,
    "x-kobo-devicemodel": "synthetic-reader",
}


def create_reader():
    """Create the secondary account these cases authenticate as."""
    import cps
    from cps import constants, ub

    user = ub.User()
    user.name = READER_NAME
    user.email = "%s@example.invalid" % READER_NAME
    user.role = constants.ROLE_USER
    user.password = ub.generate_password_hash(READER_PASSWORD)
    ub.session.add(user)
    ub.session.commit()
    assert user.id is not None
    return user.id


def login(client):
    """Authenticate ``client`` through the real login form and CSRF token."""
    page = client.get("/login")
    assert page.status_code == 200, page.status_code
    token = re.search(
        r'name="csrf_token"[^>]*value="([^"]+)"', page.get_data(as_text=True),
    )
    assert token, "login form served no csrf token"
    response = client.post(
        "/login",
        data={"username": READER_NAME, "password": READER_PASSWORD,
              "csrf_token": token.group(1)},
        follow_redirects=False,
    )
    assert response.status_code == 302, response.status_code
    return client


def create_book(title):
    """Insert one synthetic library row and return it with its calibre uuid."""
    from cps import calibre_db, db

    now = datetime.now(timezone.utc)
    book = db.Books(title, title, "Fixture Author", now, now, "1.0", now,
                    "Fixture/%s" % title, None, [], [])
    calibre_db.session.add(book)
    calibre_db.session.commit()
    book_id = book.id
    # metadata.db's books_insert_trg rewrites sort and uuid after the INSERT,
    # so the identity this fixture addresses has to be re-read, not assumed.
    calibre_db.session.expire_all()
    book = calibre_db.session.query(db.Books).filter_by(id=book_id).one()
    assert book.uuid, "calibre did not assign a uuid"
    return book


def register_kobo_device(app, user_id):
    """Register the Kobo device through the product's own registry writer."""
    from cps import ub
    from cps.services.device_registry import register_kobo_device_best_effort

    with app.app_context():
        device_id = register_kobo_device_best_effort(
            user_id=user_id, headers=DEVICE_HEADERS,
            secret_key=app.secret_key, return_internal=True,
        )
    assert device_id is not None, "the device registry refused the fixture id"
    device = ub.session.query(ub.Device).filter_by(id=device_id).one()
    assert (device.user_id, device.kind) == (user_id, "kobo")
    return device_id


def seed_authority_state(user_id, book, *, ever_authoritative=True):
    """Create the per-book authority row without any durable snapshot."""
    from cps import ub

    state = ub.KoboAnnotationBookState(
        user_id=user_id,
        book_id=book.id,
        content_id=book.uuid.casefold(),
        authority_status="authoritative" if ever_authoritative else "unseeded",
        authority_revision=0,
        ever_authoritative=ever_authoritative,
        generation_id=str(uuid.uuid4()),
        opaque_content_status="absent",
        seeded_at=datetime.now(timezone.utc),
    )
    ub.session.add(state)
    ub.session.commit()
    return state


def seed_annotation(user_id, book, annotation_id, text):
    """Add one visible highlight to the local monotonic set."""
    from cps import ub

    annotation = ub.Annotation(
        user_id=user_id,
        book_id=book.id,
        annotation_id=annotation_id,
        source="kobo",
        annotation_type="highlight",
        highlighted_text=text,
        content_id="%s!!OEBPS/chapter.xhtml" % book.uuid,
        hidden=False,
    )
    ub.session.add(annotation)
    ub.session.commit()
    return annotation


def seed_routing_only_capture(state_id, device_id):
    """Accepted device evidence that carries no upstream capture.

    ``prepare_authoritative_device_get`` (kobo_annotation_authority.py:595)
    answers ``STICKY_GET_LOCAL`` for an ever-authoritative book as soon as this
    device has an accepted capture and none of them came from upstream.
    """
    from cps import ub

    now = datetime.now(timezone.utc)
    capture = ub.KoboAnnotationSeedCapture(
        book_state_id=state_id, device_id=device_id,
        started_at=now, completed_at=now, result="accepted",
        seed_kind="routing_only", annotation_count=0,
    )
    ub.session.add(capture)
    ub.session.commit()
    return capture


def reload_state(user_id, book_id):
    """Re-read the authority row from storage, not from the identity map."""
    from cps import ub

    ub.session.expire_all()
    return (
        ub.session.query(ub.KoboAnnotationBookState)
        .filter_by(user_id=user_id, book_id=book_id)
        .one()
    )


def annotations_path(book):
    return "/api/v3/content/%s/annotations" % book.uuid


@contextlib.contextmanager
def stubbed_kobo_proxy(*, echo=False):
    """Replace the only outbound sink and record what would have been sent.

    Yields the list of proxied request bodies. An empty list is the evidence
    that a request was answered without contacting Kobo at all. With
    ``echo=True`` the stub answers with the body it was handed, which is the
    shape of a Kobo check-for-changes reply that calls every submitted id
    changed -- the reply that makes Nickel issue the destructive GET.
    """
    import cps.readingservices as readingservices
    from flask import make_response

    calls = []
    original = readingservices.proxy_to_kobo_reading_services

    def stub(*args, **kwargs):
        data = kwargs.get("data")
        calls.append(data)
        body = data if (echo and data is not None) else b"[]"
        return make_response(body, 200, {"Content-Type": "application/json"})

    readingservices.proxy_to_kobo_reading_services = stub
    try:
        yield calls
    finally:
        readingservices.proxy_to_kobo_reading_services = original


@contextlib.contextmanager
def route_error_records():
    """Collect ERROR+ records from the reading-services route logger.

    The route's exception fallback is the only sticky exit that logs at ERROR
    (``log.exception`` at cps/readingservices.py:908), so the presence or
    absence of a record is what separates the exception exit from the two
    ordinary ones.
    """
    records = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collector(level=logging.ERROR)
    logger = logging.getLogger("cps.readingservices")
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def body_digest(body):
    return hashlib.sha256(body).hexdigest()
