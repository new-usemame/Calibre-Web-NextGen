"""Explicitly invoked in isolation by tests/integration/test_real_app_kobo.py.

This module runs in its own interpreter because it takes the library database
away mid-session, and the calibre engine does not come back cleanly from that
inside one process. Isolating the outage is also the honest shape: a broken
metadata.db is a process-wide fault, and letting it leak into other cases would
make their results say something other than what they claim.
"""
import json

import pytest

import kobo_fixture as fixture

pytestmark = pytest.mark.integration

# In the documentation range (RFC 9562 style random v4), deliberately absent
# from the fixture library so the live lookup answers "not ours".
UNOWNED_CONTENT_ID = "5f2b8c14-9d3e-4a71-b0c6-7e18d4a52f93"


def _post_check_for_changes(client, content_ids):
    body = json.dumps([{"ContentId": value} for value in content_ids]).encode()
    return client.post(
        "/api/v3/content/checkforchanges", data=body,
        content_type="application/json", headers=fixture.DEVICE_HEADERS,
    )


def test_unknown_ownership_is_contained_at_the_check_for_changes_boundary(
    kobo_real_app, record_property,
):
    """A library-database outage suppresses Kobo's change trigger, not the data.

    Intent: ``_check_for_changes_ownership_is_filtered``
    (cps/readingservices.py:515) deliberately treats OWNERSHIP_UNKNOWN as owned.
    Nickel answers a "this content changed" reply with a replacement-set
    annotation GET, so forwarding an id whose ownership could not be determined
    risks deleting the reader's only copy of a highlight. Suppressing the id
    only costs a delayed cloud sync.
    Path: ``handle_check_for_changes`` (cps/readingservices.py:1509) driven
    twice with the same id -- once with the library readable, once after
    metadata.db has been emptied so the real ownership query raises and
    ``resolve_entitlement_ownership`` answers OWNERSHIP_UNKNOWN. The predicate
    is never called directly; only the trigger boundary is observed.
    Breaks if: UNKNOWN stops being contained -- the id would be forwarded to
    Kobo and returned to the device exactly as in the healthy control.
    """
    user_id = fixture.create_reader()
    client = fixture.login(kobo_real_app.test_client())
    # A real library row, so the outage below is the only reason any lookup
    # can fail and the control request is answered by a working database.
    owned_book = fixture.create_book("Fixture Book Epsilon")
    fixture.register_kobo_device(kobo_real_app, user_id)

    import cps.readingservices as readingservices

    assert readingservices.resolve_entitlement_ownership(owned_book.uuid) is not None
    assert readingservices.resolve_entitlement_ownership(UNOWNED_CONTENT_ID) is None

    # Control: with the library readable this id is provably not ours, so it is
    # forwarded and comes back in the device's change list.
    with fixture.stubbed_kobo_proxy(echo=True) as proxied:
        healthy = _post_check_for_changes(client, [UNOWNED_CONTENT_ID])
    assert healthy.status_code == 200, healthy.get_data()
    assert len(proxied) == 1, proxied
    assert UNOWNED_CONTENT_ID.encode() in proxied[0]
    assert healthy.get_json() == [{"ContentId": UNOWNED_CONTENT_ID}]

    _break_the_library(kobo_real_app)

    # Prove the state this case is about was actually constructed. Without
    # this, an assertion that nothing was proxied is equally satisfied by a
    # route that never ran.
    resolved = readingservices.resolve_entitlement_ownership(UNOWNED_CONTENT_ID)
    assert resolved is readingservices.OWNERSHIP_UNKNOWN, resolved

    with fixture.stubbed_kobo_proxy(echo=True) as proxied:
        outage = _post_check_for_changes(client, [UNOWNED_CONTENT_ID])
    assert outage.status_code == 200, outage.get_data()
    assert proxied == [], (
        "an id of unknown ownership was forwarded to Kobo during a database "
        "outage; the reply drives a destructive annotation GET")
    assert outage.get_json() == []
    record_property("p1_kobo_containment", "OWNERSHIP_UNKNOWN suppressed")
    print("CONTAINMENT healthy_proxied=1 outage_proxied=0 outage_body=%r"
          % outage.get_data())


def _break_the_library(app):
    """Empty metadata.db and force the next query onto the emptied file.

    This models a library volume that is present but no longer carries the
    database -- the outage class the containment comment names. The engine is
    disposed first so the pooled connection cannot keep serving the old file,
    and nothing is restored: this process is disposable.
    """
    from pathlib import Path

    from cps import calibre_db
    from cps.db import CalibreDB

    metadata = Path(app.config["CWA_TEST_ROOT"]) / "library" / "metadata.db"
    assert metadata.is_file() and metadata.stat().st_size > 0
    calibre_db.session.close()
    CalibreDB.engine.dispose()
    metadata.write_bytes(b"")
    assert metadata.stat().st_size == 0
