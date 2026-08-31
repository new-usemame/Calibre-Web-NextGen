# Calibre-Web-NextGen — fork of Calibre-Web-Automated
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The whole send-to-device cycle, against a running server and a real library.

Every other test of this feature stops at a seam. The service tests drive
``device_delivery`` with a hand-built session; the protocol tests drive the Flask
routes with a stubbed user and an in-memory database; the Lua contract tests drive
the plugin with the network mocked. Each half can be green while the two disagree
about the thing between them -- the exact failure the ``api.json`` two-list
invariant already exists to catch on the client side.

So this joins them: a real device identity registers itself, a real book is queued
through the same web API a person clicks, and the device claims, downloads and
confirms it, with the bytes checked against the size the server promised.

The negative control is not optional here. A download that succeeds proves nothing
unless the same request with a wrong claim token fails, because "the server hands
out the file" and "the server hands out the file to whoever asks" look identical
from the happy path.

Requires a running container (``CWA_TEST_PORT``, default 8085); skips otherwise.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest

# KOReader sync ships OFF, and while it is off every /kosync endpoint answers 503
# before any handler runs -- so without this fixture these four tests would fail
# on `assert 503`, which looks like a delivery outage and is a missing
# precondition. These are docker_integration, not unit, so requesting a
# container-backed fixture here is legitimate.
pytestmark = [
    pytest.mark.docker_integration,
    pytest.mark.slow,
    pytest.mark.usefixtures("koreader_sync_enabled"),
]

KOREADER_READABLE = {"EPUB", "PDF", "MOBI", "FB2", "DJVU", "CBZ", "CBR", "TXT", "HTML", "RTF"}
KOSYNC_ACCEPT = "application/vnd.koreader.v1+json"


@pytest.fixture
def cwng_server():
    """An authenticated session against whatever server is already serving.

    Deliberately not ``cwa_api_client``: that fixture also OWNS a container's
    lifecycle under a fixed name, so it collides with any other session already
    running one and it cannot be pointed at a server started some other way.
    This one only asks whether something is listening, which lets the same test
    run against a CI container, a local dev stack, or a worktree instance.
    """
    import requests

    port = os.getenv("CWA_TEST_PORT", "8085")
    base_url = f"http://localhost:{port}"
    try:
        requests.get(base_url, timeout=3)
    except requests.exceptions.RequestException:
        pytest.skip(f"no CWNG server is listening on port {port}")

    session = requests.Session()
    # The login form is CSRF-protected, so the token has to come off the rendered
    # page first. Posting credentials alone returns 400, not 401 -- which reads
    # like a bad request rather than a missing token and is easy to misdiagnose.
    form = session.get(f"{base_url}/login", timeout=10)
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', form.text)
    if token is None:
        pytest.skip("could not read a CSRF token from the login page")
    login = session.post(
        f"{base_url}/login",
        data={"username": "admin", "password": "admin123",
              "csrf_token": token.group(1)},
        allow_redirects=False, timeout=10,
    )
    if login.status_code not in (200, 302):
        pytest.skip(
            f"could not authenticate against the CWNG server (HTTP {login.status_code})"
        )
    return {"base_url": base_url, "session": session}


@pytest.fixture
def device(cwng_server):
    """A device identity unique to this run, so reruns never collide."""
    return {
        "device": "IntegrationReader",
        # A fresh id each run: the registry keys deliveries by device, and a
        # reused id would inherit the previous run's queue state.
        "device_id": f"integration-{uuid.uuid4().hex}",
    }


def _kosync(client, method, path, **kwargs):
    """kosync speaks Basic auth and its own accept header, not the web session."""
    import requests

    headers = kwargs.pop("headers", {})
    headers.setdefault("accept", KOSYNC_ACCEPT)
    return requests.request(
        method, f"{client['base_url']}/kosync{path}",
        auth=("admin", "admin123"), headers=headers, timeout=30, **kwargs,
    )


def _csrf(client):
    response = client["session"].get(f"{client['base_url']}/api/v1/auth/csrf", timeout=10)
    response.raise_for_status()
    return response.json()["csrf_token"]


def _first_deliverable_book(client):
    response = client["session"].get(f"{client['base_url']}/api/v1/books?limit=50", timeout=15)
    response.raise_for_status()
    for item in response.json().get("items", []):
        if KOREADER_READABLE.intersection(item.get("formats") or []):
            return item
    pytest.skip("the library holds no book in a format KOReader can read")


def test_a_device_can_be_sent_a_book_and_confirm_it(cwng_server, device):
    client = cwng_server

    # Registering happens as a side effect of the device's first report, which is
    # also the only way a device gets an id the web UI can address.
    registered = _kosync(client, "PUT", "/syncs/inventory",
                         json={**device, "inventory": []})
    assert registered.status_code == 200, registered.text
    public_id = registered.json()["device"]

    book = _first_deliverable_book(client)
    queued = client["session"].post(
        f"{client['base_url']}/api/v1/books/{book['id']}/device-deliveries",
        json={"device": public_id},
        headers={"X-CSRFToken": _csrf(client)},
        timeout=30,
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()["queued"] is True
    # Sending an unreadable format would look like success and fail on the device.
    assert queued.json()["format"] in KOREADER_READABLE

    # A claim carries the device's fresh free/total space: the server records it
    # and will not hand over a book it knows cannot fit. Both api.json's payload
    # and its required_params list them, so a claim without them is refused --
    # asserted here so the two halves of that contract cannot drift apart
    # silently, which is the failure mode lua-Spore makes invisible.
    storage = {"free_space": 4 * 1024 * 1024 * 1024,
               "total_space": 8 * 1024 * 1024 * 1024}
    storageless = _kosync(client, "POST", "/syncs/deliveries/claim", json=device)
    assert storageless.status_code == 400, (
        "a claim with no storage reading was accepted; the fit check is then "
        f"decoration (got {storageless.status_code})"
    )

    claimed = _kosync(client, "POST", "/syncs/deliveries/claim",
                      json={**device, **storage})
    assert claimed.status_code == 200, claimed.text
    delivery = claimed.json()["delivery"]
    assert delivery is not None, "the queued book was not offered to its own device"
    assert delivery["book_id"] == book["id"]
    token = delivery["claim_token"]

    device_headers = {
        "X-CWNG-Device-ID": device["device_id"],
        "X-CWNG-Device-Name": device["device"],
    }

    # The negative control runs BEFORE the real download: if a wrong token were
    # accepted, a later success would prove nothing, and running it first also
    # shows the refusal is not merely "already downloaded".
    refused = _kosync(client, "GET", f"/syncs/deliveries/{delivery['id']}/download",
                      headers={**device_headers, "X-CWNG-Claim-Token": "not-the-token"})
    assert refused.status_code == 409, (
        f"a wrong claim token was not refused (got {refused.status_code})"
    )

    downloaded = _kosync(client, "GET", f"/syncs/deliveries/{delivery['id']}/download",
                         headers={**device_headers, "X-CWNG-Claim-Token": token})
    assert downloaded.status_code == 200, downloaded.text
    assert len(downloaded.content) == delivery["size"], (
        "the server promised a size it did not deliver"
    )
    assert len(downloaded.content) > 0

    import hashlib

    confirmed = _kosync(client, "PUT", "/syncs/deliveries/complete", json={
        **device,
        "delivery_id": delivery["id"],
        "claim_token": token,
        "lpath": f"books/{delivery['id']}-integration.{delivery['format'].lower()}",
        "checksum": hashlib.md5(downloaded.content).hexdigest(),
        "size": len(downloaded.content),
        "mtime": 1700000000,
    })
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["completed"] is True

    # A confirmed delivery must leave the queue. If it did not, the device would
    # re-download the same book on every sync for the rest of its life.
    drained = _kosync(client, "POST", "/syncs/deliveries/claim",
                      json={**device, **storage})
    assert drained.status_code == 200, drained.text
    assert drained.json()["delivery"] is None, (
        "the completed delivery was offered again; this device would loop forever"
    )


def _report_inventory(client, device, books, *, free_space=None, total_space=None):
    payload = {**device, "inventory": books}
    if free_space is not None:
        payload["free_space"] = free_space
        payload["total_space"] = total_space
    response = _kosync(client, "PUT", "/syncs/inventory", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _inventory_view(client, public_id):
    response = client["session"].get(
        f"{client['base_url']}/api/annotations/devices/{public_id}/inventory",
        timeout=15,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_named_deletion_is_carried_out_and_confirmed(cwng_server, device):
    """Deleting from a device is NAMED end to end -- never inferred.

    The request addresses an inventory item the device itself reported, so
    "delete what is missing from the latest report" is not expressible through
    this API. That is the property worth holding: a device that syncs mid-copy,
    or with a card unmounted, sends a short report and must lose nothing.
    """
    client = cwng_server
    book = {
        "lpath": "Books/named-deletion.epub",
        "checksum": "0" * 32,
        "size": 1024,
        "mtime": 1700000000,
    }
    registered = _report_inventory(client, device, [book])
    public_id = registered["device"]

    listed = _inventory_view(client, public_id)
    item = next(b for b in listed["books"] if b["lpath"] == book["lpath"])

    requested = client["session"].post(
        f"{client['base_url']}/api/annotations/devices/{public_id}"
        f"/inventory/{item['inventory_item_id']}/delete",
        headers={"X-CSRFToken": _csrf(client)},
        timeout=15,
    )
    assert requested.status_code == 202, requested.text

    claimed = _kosync(client, "POST", "/syncs/deletions/claim", json=device)
    assert claimed.status_code == 200, claimed.text
    deletion = claimed.json()["deletion"]
    assert deletion is not None, "the requested deletion was not offered to its device"
    assert deletion["lpath"] == book["lpath"], (
        "the device was told to delete a path it was not asked about"
    )

    confirmed = _kosync(client, "PUT", "/syncs/deletions/complete", json={
        **device,
        "deletion_id": deletion["id"],
        "claim_token": deletion["claim_token"],
        "deleted": True,
    })
    assert confirmed.status_code == 200, confirmed.text

    # Only the device's confirmation removes the observation. Until it reports
    # back, the server must keep believing the book is there.
    remaining = _inventory_view(client, public_id)
    assert all(b["lpath"] != book["lpath"] for b in remaining["books"]), (
        "the confirmed deletion did not clear the observation"
    )

    drained = _kosync(client, "POST", "/syncs/deletions/claim", json=device)
    assert drained.status_code == 200, drained.text
    assert drained.json()["deletion"] is None, (
        "the completed deletion was offered again; the device would delete twice"
    )


def test_a_device_reports_its_free_space(cwng_server, device):
    """Storage has to survive the wire, not just the service layer.

    ``free_space``/``total_space`` are declared in api.json, and lua-Spore drops
    any field missing from the method's lists with no error at all, so only a
    round trip shows they actually arrive.

    Note the two names are deliberately different on the way in and the way out:
    the device reports ``free_space``/``total_space``; the read API returns
    ``storage_free``/``storage_total``. Asserting the wire names on the read side
    would pass while reading nothing.
    """
    client = cwng_server
    registered = _report_inventory(
        client, device, [],
        free_space=512 * 1024 * 1024,
        total_space=8 * 1024 * 1024 * 1024,
    )

    listed = _inventory_view(client, registered["device"])
    reported = listed["device"]
    assert reported.get("storage_free") == 512 * 1024 * 1024, reported
    assert reported.get("storage_total") == 8 * 1024 * 1024 * 1024, reported
    assert reported.get("storage_observed") is not None, (
        "storage was stored without a timestamp, so nothing can tell a fresh "
        "reading from a stale one"
    )


def test_collections_are_acknowledged_by_revision(cwng_server, device):
    """A device may only acknowledge the revision it was actually handed.

    Acknowledgement clears "this device is out of date". Accepting a stale
    revision would mark the device current against collections it never saw,
    and nothing later would notice.
    """
    client = cwng_server
    _report_inventory(client, device, [])

    handed = _kosync(client, "POST", "/syncs/collections", json=device)
    assert handed.status_code == 200, handed.text
    snapshot = handed.json()
    assert "collections" in snapshot and "revision" in snapshot, snapshot

    stale = _kosync(client, "PUT", "/syncs/collections/complete",
                    json={**device, "revision": snapshot["revision"] + 1})
    assert stale.status_code == 409, (
        f"a revision the device was never handed was accepted ({stale.status_code})"
    )

    acknowledged = _kosync(client, "PUT", "/syncs/collections/complete",
                           json={**device, "revision": snapshot["revision"]})
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["completed"] is True
