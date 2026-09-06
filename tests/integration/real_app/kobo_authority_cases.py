"""Explicitly invoked in isolation by tests/integration/test_real_app_kobo.py.

Three properties of the owned-annotation GET are asserted by the code and have
never been observed on the route: which of ``_owned_annotation_get_response``'s
sticky exits emits an ``ETag``, and what that header actually looks like. All
three exits live behind ``ever_authoritative``, which the shipped fixture never
reaches, so each case below first drives the application into the sticky state
and proves it is there before asserting anything about the response.
"""
import pytest

import kobo_fixture as fixture

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def reader(kobo_real_app):
    """One secondary account, authenticated once, shared by every case here."""
    user_id = fixture.create_reader()
    return user_id, fixture.login(kobo_real_app.test_client())


def test_sticky_local_render_answers_with_a_durable_cwng_etag(
    kobo_real_app, reader, record_property,
):
    """The live-render sticky exit stamps CWNG's own replacement-set ETag.

    Intent: once CWNG has ever been authoritative for a book, an owned GET that
    still has live device proof answers from the local set and labels those
    exact bytes with a durable ``cwng_revision`` ETag, so the device can
    recognise the same replacement set later.
    Path: an authenticated Kobo GET with a registered device id, against a book
    whose authority row is ``ever_authoritative`` and whose only accepted seed
    capture is routing-only. That is the ``answered_locally`` exit at
    cps/readingservices.py:905.
    Breaks if: the exit stops setting ETag, sets a non-CWNG one, or stops
    persisting the render (etag_kind/current_etag/last_served_* would not move).
    """
    user_id, client = reader
    book = fixture.create_book("Fixture Book Alpha")
    state = fixture.seed_authority_state(user_id, book)
    fixture.seed_annotation(user_id, book, "alpha-highlight-1", "first synthetic highlight")
    device_id = fixture.register_kobo_device(kobo_real_app, user_id)
    fixture.seed_routing_only_capture(state.id, device_id)

    # Prove the state before asserting on it. Six earlier attempts at this
    # observation reported a green or absent result because the sticky branch
    # never ran, which is indistinguishable from a passing assertion unless the
    # gate itself is read first.
    from cps.services.kobo_annotation_authority import (
        AUTHORITY_EVER, STICKY_GET_LOCAL, ever_authoritative,
        prepare_authoritative_device_get,
    )
    import cps.readingservices as readingservices

    assert ever_authoritative(user_id, book.id) == AUTHORITY_EVER
    assert prepare_authoritative_device_get(
        user_id=user_id, book_id=book.id, device_id=device_id,
        log=readingservices.log,
    ) == STICKY_GET_LOCAL

    with fixture.stubbed_kobo_proxy() as proxied:
        with fixture.route_error_records() as records:
            response = client.get(fixture.annotations_path(book),
                                  headers=fixture.DEVICE_HEADERS)

    assert response.status_code == 200, response.get_data()
    assert proxied == [], "the sticky local exit must not contact Kobo"
    assert [r for r in records if r.exc_info] == [], (
        "the route's exception fallback ran; this is not the live-render exit")
    etag = response.headers.get("ETag")
    assert etag is not None, "the answered_locally exit emitted no ETag"
    assert fixture.CWNG_ETAG.match(etag), etag
    body = response.get_data()
    assert b"alpha-highlight-1" in body

    stored = fixture.reload_state(user_id, book.id)
    assert stored.etag_kind == "cwng_revision"
    assert stored.current_etag == etag
    assert stored.authority_revision == 1
    assert stored.last_served_etag == etag
    assert stored.last_served_body_sha256 == fixture.body_digest(body)
    record_property("p1_kobo_exit", "answered_locally (readingservices.py:905)")
    record_property("p1_kobo_etag_kind", stored.etag_kind)
    print("EXIT answered_locally etag_kind=%s etag=%s" % (stored.etag_kind, etag))


def test_sticky_snapshot_exit_replays_the_stored_cwng_etag(
    kobo_real_app, reader, record_property,
):
    """Without live device proof the sticky GET replays the durable snapshot.

    Intent: an ever-authoritative book whose device proof is missing must be
    answered from CWNG's last complete response -- never from Kobo's stale copy
    and never from a partial live read -- carrying the ETag those exact bytes
    were served with.
    Path: a second authenticated GET for the same book sent without a device
    id, so ``prepare_authoritative_device_get`` cannot match a device row and
    answers ``STICKY_GET_SNAPSHOT``. That is the ``answered_from_snapshot``
    exit at cps/readingservices.py:849.
    Breaks if: the exit stops setting ETag, or answers from the live set --
    a highlight added after the snapshot was stored would then appear.
    """
    user_id, client = reader
    book = fixture.create_book("Fixture Book Beta")
    state = fixture.seed_authority_state(user_id, book)
    fixture.seed_annotation(user_id, book, "beta-highlight-1", "first synthetic highlight")
    device_id = fixture.register_kobo_device(kobo_real_app, user_id)
    fixture.seed_routing_only_capture(state.id, device_id)

    # Setup, not the assertion: let the route itself commit the durable
    # snapshot, so the bytes this case checks were written by the product and
    # not by the test.
    with fixture.stubbed_kobo_proxy():
        seeding = client.get(fixture.annotations_path(book),
                             headers=fixture.DEVICE_HEADERS)
    assert seeding.status_code == 200, seeding.get_data()

    # The live set now moves past the snapshot. A live render would include
    # this row; the snapshot exit cannot.
    fixture.seed_annotation(user_id, book, "beta-highlight-2", "second synthetic highlight")

    import cps.readingservices as readingservices
    from cps.services.kobo_annotation_authority import (
        STICKY_GET_SNAPSHOT, load_last_served_complete_set,
        prepare_authoritative_device_get,
    )

    stored_snapshot = load_last_served_complete_set(
        user_id=user_id, book_id=book.id, log=readingservices.log,
    )
    assert stored_snapshot is not None, "no durable snapshot to replay"
    snapshot_body, snapshot_etag = stored_snapshot
    assert prepare_authoritative_device_get(
        user_id=user_id, book_id=book.id, device_id=None,
        log=readingservices.log,
    ) == STICKY_GET_SNAPSHOT
    before = fixture.reload_state(user_id, book.id)
    revision_before = before.authority_revision
    digest_before = before.last_served_body_sha256

    with fixture.stubbed_kobo_proxy() as proxied:
        with fixture.route_error_records() as records:
            response = client.get(fixture.annotations_path(book))

    assert response.status_code == 200, response.get_data()
    assert proxied == [], "the sticky snapshot exit must not contact Kobo"
    assert [r for r in records if r.exc_info] == [], (
        "the route's exception fallback ran; this is not the snapshot exit")
    etag = response.headers.get("ETag")
    assert etag is not None, "the answered_from_snapshot exit emitted no ETag"
    assert fixture.CWNG_ETAG.match(etag), etag
    assert etag == snapshot_etag
    body = response.get_data()
    assert body == snapshot_body
    assert b"beta-highlight-1" in body
    assert b"beta-highlight-2" not in body, (
        "the response contains a row added after the snapshot, so this was a "
        "live render and not the snapshot exit")

    after = fixture.reload_state(user_id, book.id)
    assert after.authority_revision == revision_before
    assert after.last_served_body_sha256 == digest_before
    assert after.etag_kind == "cwng_revision"
    record_property("p1_kobo_exit",
                    "answered_from_snapshot (readingservices.py:849)")
    record_property("p1_kobo_etag_kind", after.etag_kind)
    print("EXIT answered_from_snapshot etag_kind=%s etag=%s"
          % (after.etag_kind, etag))


def test_sticky_exception_fallback_answers_with_a_cwng_etag_not_kobo(
    kobo_real_app, reader, record_property,
):
    """A failure inside the sticky GET rebuilds locally instead of proxying.

    Intent: once Kobo's cloud copy is stale by construction, an unexpected
    failure while serving an owned GET must still answer with CWNG's complete
    set and its own ETag. Falling back to the proxy would hand Nickel a stale
    replacement set and delete the reader's only copy of newer highlights.
    Path: an ever-authoritative book with *no* durable snapshot, so every
    non-exception sticky exit answers 503 -- the control request below proves
    that. With the pre-serve proof raising, the same request answers 200, which
    can only be the exception fallback at cps/readingservices.py:948.
    Breaks if: that fallback stops setting ETag, stops rebuilding, or proxies.
    """
    user_id, client = reader
    book = fixture.create_book("Fixture Book Gamma")
    fixture.seed_authority_state(user_id, book)
    fixture.seed_annotation(user_id, book, "gamma-highlight-1", "gamma synthetic highlight")

    import cps.readingservices as readingservices
    import cps.services.kobo_annotation_authority as authority

    assert authority.load_last_served_complete_set(
        user_id=user_id, book_id=book.id, log=readingservices.log,
    ) is None, "this case needs a book with no durable snapshot"

    # Control: without a failure, this exact request is a 503. Any later 200
    # therefore cannot have come from the snapshot or live-render exits.
    with fixture.stubbed_kobo_proxy() as proxied:
        control = client.get(fixture.annotations_path(book))
    assert control.status_code == 503, control.get_data()
    assert control.headers.get("ETag") is None
    assert proxied == []

    injected = RuntimeError("synthetic pre-serve proof failure")

    def raising_pre_serve(**_kwargs):
        raise injected

    original = authority.prepare_authoritative_device_get
    authority.prepare_authoritative_device_get = raising_pre_serve
    try:
        with fixture.stubbed_kobo_proxy() as proxied:
            with fixture.route_error_records() as records:
                response = client.get(fixture.annotations_path(book))
    finally:
        authority.prepare_authoritative_device_get = original

    assert response.status_code == 200, response.get_data()
    assert proxied == [], "the exception fallback must not contact Kobo"
    raised = [r for r in records if r.exc_info and r.exc_info[1] is injected]
    assert raised, "the route's exception fallback did not run"
    etag = response.headers.get("ETag")
    assert etag is not None, "the sticky exception fallback emitted no ETag"
    assert fixture.CWNG_ETAG.match(etag), etag
    body = response.get_data()
    assert b"gamma-highlight-1" in body

    stored = fixture.reload_state(user_id, book.id)
    assert stored.etag_kind == "cwng_revision"
    assert stored.current_etag == etag
    assert stored.last_served_body_sha256 == fixture.body_digest(body)
    record_property("p1_kobo_exit",
                    "sticky exception fallback (readingservices.py:948)")
    record_property("p1_kobo_etag_kind", stored.etag_kind)
    print("EXIT sticky_exception_fallback etag_kind=%s etag=%s"
          % (stored.etag_kind, etag))


OWNERSHIP_SAMPLES = 12


def test_ownership_resolution_is_stable_across_identical_requests(
    kobo_real_app, reader, record_property,
):
    """Identical authenticated GETs must resolve one book the same way.

    Intent: ownership decides whether an annotation GET is answered locally or
    forwarded to Kobo. If the same book resolved differently between two
    identical requests, the same device would be served CWNG's set and Kobo's
    stale set in turn, and the replacement-set semantics would delete rows.
    Path: OWNERSHIP_SAMPLES identical authenticated GETs for one seeded owned
    book, recording what ``resolve_entitlement_ownership`` returned each time.
    Breaks if: the resolution flaps -- an intermittently failing metadata.db
    lookup would show up as ``unknown`` interleaved with ``owned``.
    """
    user_id, client = reader
    book = fixture.create_book("Fixture Book Delta")
    state = fixture.seed_authority_state(user_id, book)
    fixture.seed_annotation(user_id, book, "delta-highlight-1", "delta synthetic highlight")
    device_id = fixture.register_kobo_device(kobo_real_app, user_id)
    fixture.seed_routing_only_capture(state.id, device_id)

    import cps.readingservices as readingservices

    resolutions = []
    statuses = []
    original = readingservices.resolve_entitlement_ownership

    def recording(entitlement_id):
        result = original(entitlement_id)
        resolutions.append((
            readingservices._capture_ownership_label(result),
            getattr(result, "id", None),
        ))
        return result

    readingservices.resolve_entitlement_ownership = recording
    try:
        with fixture.stubbed_kobo_proxy() as proxied:
            for _ in range(OWNERSHIP_SAMPLES):
                statuses.append(client.get(
                    fixture.annotations_path(book),
                    headers=fixture.DEVICE_HEADERS,
                ).status_code)
    finally:
        readingservices.resolve_entitlement_ownership = original

    assert len(resolutions) == OWNERSHIP_SAMPLES, resolutions
    distinct = sorted(set(resolutions))
    record_property("p1_kobo_ownership_samples", OWNERSHIP_SAMPLES)
    record_property("p1_kobo_ownership_distinct", repr(distinct))
    print("OWNERSHIP n=%d distinct=%r statuses=%r proxied=%d"
          % (OWNERSHIP_SAMPLES, distinct, sorted(set(statuses)), len(proxied)))
    assert distinct == [("owned", book.id)], distinct
    assert set(statuses) == {200}, statuses
