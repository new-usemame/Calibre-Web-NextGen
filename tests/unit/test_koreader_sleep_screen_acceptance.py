# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral acceptance tests for per-device KOReader sleep screens.

These cases drive the public Flask routes through LibraryWorld's real account,
Basic-auth, SQLite, and CSRF machinery. The red baseline control is always a
valid request with an expected success response; negative 404s alone cannot
make an absent route look correct.
"""

from __future__ import annotations

import io
import hashlib
import random
import struct
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
import pytest
from PIL import Image
from sqlalchemy import LargeBinary, create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

from cps import db, ub
from cps.services import device_registry
from tests.unit.koreader_library_world import LibraryWorld


pytestmark = pytest.mark.unit

UPLOAD_LIMIT = 10 * 1024 * 1024
ACCOUNT_LIMIT = 64 * 1024 * 1024


@pytest.fixture
def world(monkeypatch, tmp_path):
    fixture = LibraryWorld(monkeypatch, tmp_path)
    fixture.enable_web()
    from cps import annotations, csrf
    fixture.app.register_blueprint(annotations.annotations_bp)
    csrf.init_app(fixture.app)
    yield fixture
    fixture.close()


@pytest.fixture
def threaded_world(monkeypatch, tmp_path):
    """Use a file-backed SQLite DB and per-thread sessions for the one race test."""
    fixture = LibraryWorld(monkeypatch, tmp_path)
    fixture.enable_web()
    fixture.close()

    engine = create_engine(
        f"sqlite:///{tmp_path / 'sleep-screen-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    ub.Base.metadata.create_all(engine)
    sessions = scoped_session(sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(ub, "session", sessions)
    monkeypatch.setattr(db.ub, "session", sessions)
    fixture.engine = engine
    fixture.session = sessions
    yield fixture
    fixture.close()
    engine.dispose()


def _add_user(world, name):
    return world.add_user(name, password="sleep-screen-password")


def _register_device(world, username, device_id, *, label="Kindle Paperwhite"):
    user = world.session.query(ub.User).filter_by(name=username).one()
    registered_id = device_registry.register_koreader_device_best_effort(
        user_id=user.id,
        device_id=device_id,
        device_name=label,
        secret_key=world.app.secret_key,
    )
    assert registered_id is not None, "valid KOReader identity should register before the feature request"
    world.session.expire_all()
    return world.session.get(ub.Device, registered_id)


def _report(world, username, device_id, *, platform="kindle", supported=True,
            width=8, height=12, plugin_version="4.2.0"):
    return world.client.put(
        "/kosync/syncs/sleep-screen/device",
        headers=world.device_headers(username, "sleep-screen-password", device_id),
        json={
            "platform": platform,
            "plugin_version": plugin_version,
            "supported": supported,
            "screen_width": width,
            "screen_height": height,
        },
    )


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG", compress_level=1)
    return output.getvalue()


def _exif_jpeg() -> bytes:
    # The stored pixels are 2×4 portrait; EXIF orientation 6 makes the viewed
    # artwork 4×2 landscape. Red and blue regions make an accidental missing
    # transpose distinguishable after grayscale conversion.
    image = Image.new("RGB", (2, 4))
    image.putdata([
        (250, 20, 20), (250, 20, 20),
        (250, 20, 20), (250, 20, 20),
        (20, 20, 250), (20, 20, 250),
        (20, 20, 250), (20, 20, 250),
    ])
    exif = Image.Exif()
    exif[274] = 6
    output = io.BytesIO()
    image.save(output, "JPEG", quality=100, exif=exif, comment=b"private source comment")
    return output.getvalue()


def _palette_transparency_png() -> bytes:
    image = Image.new("P", (2, 2))
    image.putpalette([0, 0, 0, 255, 0, 0] + [0, 0, 0] * 254)
    image.putdata([0, 1, 1, 0])
    image.info["transparency"] = 0
    return _png(image)


def _upload(client, public_id, data, filename, content_type="image/png"):
    return client.put(
        f"/api/v1/devices/{public_id}/sleep-screen",
        data={"file": (io.BytesIO(data), filename, content_type)},
        content_type="multipart/form-data",
    )


def _metadata(client, public_id):
    return client.get(f"/api/v1/devices/{public_id}/sleep-screen")


def _image(client, public_id, revision):
    return client.get(
        f"/api/v1/devices/{public_id}/sleep-screen/image",
        query_string={"revision": revision},
    )


def _error_code(response):
    payload = response.get_json(silent=True) or {}
    error = payload.get("error", payload)
    return error.get("code") if isinstance(error, dict) else None


def _make_noise_png(size=(2000, 3500), seed=20261012):
    pixels = random.Random(seed).randbytes(size[0] * size[1])
    return _png(Image.frombytes("L", size, pixels))


def _png_header_with_dimensions(width, height):
    def chunk(name, value):
        body = name + value
        return struct.pack(">I", len(value)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00"))
        + chunk(b"IEND", b"")
    )


def test_set_replace_and_preview_are_owned_by_one_koreader_device(world):
    """A saved image is private per device, queued until dimensions arrive, then prepared exactly.

    This fails if valid upload, report, replace, image ownership, grayscale
    preparation, stale-revision rejection, or failed-save rollback stops working.
    """
    _add_user(world, "alice")
    _add_user(world, "bob")
    target = _register_device(world, "alice", "alice-bedroom-reader")
    sibling = _register_device(world, "alice", "alice-living-room-reader", label="Kobo Clara")
    alice = world.browser("alice")
    bob = world.browser("bob")

    source = _exif_jpeg()
    queued = _upload(alice, target.public_id, source, "engraving.jpg", "image/jpeg")
    queued_body = queued.get_json(silent=True) or {}
    queued_revision = queued_body.get("desired_revision", "missing-on-baseline")
    queued_preview = _image(alice, target.public_id, queued_revision)
    report = _report(world, "alice", "alice-bedroom-reader")

    prepared = _metadata(alice, target.public_id)
    prepared_body = prepared.get_json(silent=True) or {}
    revision = prepared_body.get("desired_revision", "missing-on-baseline")
    preview = _image(alice, target.public_id, revision)
    unchanged_report = _report(world, "alice", "alice-bedroom-reader")
    same_revision = _metadata(alice, target.public_id)

    replacement = _upload(
        alice, target.public_id, _palette_transparency_png(), "transparent.png",
    )
    replacement_body = replacement.get_json(silent=True) or {}
    replacement_revision = replacement_body.get("desired_revision", "missing-on-baseline")
    replaced_preview = _image(alice, target.public_id, replacement_revision)
    stale_preview = _image(alice, target.public_id, revision)
    malformed_replace = _upload(alice, target.public_id, b"\x89PNG\r\n\x1a\ntruncated", "broken.png")
    after_failure = _metadata(alice, target.public_id)
    after_failure_body = after_failure.get_json(silent=True) or {}
    current_preview = _image(
        alice, target.public_id,
        after_failure_body.get("desired_revision", "missing-on-baseline"),
    )

    foreign_metadata = _metadata(bob, target.public_id)
    foreign_image = _image(bob, target.public_id, replacement_revision)
    sibling_metadata = _metadata(alice, sibling.public_id)
    unknown_metadata = _metadata(alice, "not-a-device-id")

    # The valid controls run before any negative assertions, so absent routes
    # cannot make the ownership 404s look like a passing implementation.
    assert queued.status_code == 200, f"valid image upload returned HTTP {queued.status_code}, expected 200"
    assert queued_body["state"] == "waiting_for_device"
    assert queued_preview.status_code == 200
    queued_image = Image.open(io.BytesIO(queued_preview.data))
    assert queued_image.mode == "L" and queued_image.size == (4, 2)
    assert report.status_code == 200
    assert prepared.status_code == 200 and prepared_body["state"] == "image"
    assert prepared_body["desired_revision"] != queued_body["desired_revision"]
    assert unchanged_report.status_code == 200
    assert same_revision.get_json()["desired_revision"] == prepared_body["desired_revision"]
    assert preview.status_code == 200
    prepared_image = Image.open(io.BytesIO(preview.data))
    assert prepared_image.mode == "L" and prepared_image.size == (8, 12)
    assert prepared_image.getpixel((0, 0)) == 255
    assert prepared_image.getpixel((0, 11)) == 255
    assert min(prepared_image.getextrema()) < 255
    assert not ({"exif", "icc_profile", "comment", "XML:com.adobe.xmp"} & prepared_image.info.keys())
    assert "private" in preview.headers.get("Cache-Control", "")
    assert preview.headers.get("X-Content-Type-Options", "").lower() == "nosniff"

    assert replacement.status_code == 200
    assert replacement_body["desired_revision"] != prepared_body["desired_revision"]
    assert replaced_preview.status_code == 200
    replacement_image = Image.open(io.BytesIO(replaced_preview.data))
    assert replacement_image.mode == "L" and replacement_image.size == (8, 12)
    assert min(replacement_image.getextrema()) < 255
    assert stale_preview.status_code == 409 and _error_code(stale_preview) == "sleep_screen_changed"
    assert malformed_replace.status_code == 400
    assert after_failure_body["desired_revision"] == replacement_body["desired_revision"]
    assert current_preview.data == replaced_preview.data

    assert foreign_metadata.status_code == foreign_image.status_code == 404
    assert sibling_metadata.status_code == 200 and sibling_metadata.get_json()["state"] == "none"
    assert unknown_metadata.status_code == 404


def test_invalid_uploads_and_size_limits_preserve_the_saved_image(world):
    """Validation distinguishes bad image data, the 10 MiB upload cap, and 8 MiB normalized output.

    The test first proves a supported reader can save, then every rejection must
    leave that same desired revision and preview bytes in place.
    """
    _add_user(world, "alice")
    client = world.browser("alice")
    reader = _register_device(world, "alice", "validation-reader")
    report = _report(world, "alice", "validation-reader")
    valid = _upload(client, reader.public_id, _png(Image.new("L", (8, 12), 90)), "valid.png")
    valid_body = valid.get_json(silent=True) or {}
    initial_revision = valid_body.get("desired_revision", "missing-on-baseline")
    initial_preview = _image(client, reader.public_id, initial_revision)

    animated = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(
        animated, "PNG", save_all=True,
        append_images=[Image.new("RGB", (4, 4), "blue")], duration=100, loop=0,
    )
    animated_data = animated.getvalue()
    assert Image.open(io.BytesIO(animated_data)).n_frames == 2

    oversized_upload = _upload(
        client, reader.public_id, b"x" * (UPLOAD_LIMIT + 1), "too-large.png",
    )
    invalids = [
        ("truncated", _upload(client, reader.public_id, b"\x89PNG\r\n\x1a\nshort", "truncated.png"), 400),
        ("non-image-spoofed-as-png", _upload(client, reader.public_id, b"not an image", "spoof.png", "image/png"), 400),
        ("animated-png", _upload(client, reader.public_id, animated_data, "animated.png"), 400),
        ("pixel-bomb", _upload(client, reader.public_id, _png_header_with_dimensions(5001, 5000), "bomb.png"), 400),
    ]

    # 4096² grayscale noise is within the input geometry ceiling but its clean
    # canonical PNG is larger than the independent 8 MiB stored-image bound.
    noise = random.Random(20261013).randbytes(4096 * 4096)
    noisy_jpeg = io.BytesIO()
    Image.frombytes("L", (4096, 4096), noise).save(noisy_jpeg, "JPEG", quality=10)
    noisy_input = noisy_jpeg.getvalue()
    assert len(noisy_input) <= UPLOAD_LIMIT
    noisy_output = _upload(client, reader.public_id, noisy_input, "noisy.jpg", "image/jpeg")
    after_rejections = _metadata(client, reader.public_id)
    final_preview = _image(
        client, reader.public_id,
        (after_rejections.get_json(silent=True) or {}).get("desired_revision", "missing-on-baseline"),
    )

    assert report.status_code == 200
    assert valid.status_code == 200, f"valid image upload returned HTTP {valid.status_code}, expected 200"
    assert initial_preview.status_code == 200
    assert oversized_upload.status_code == 413
    assert _error_code(oversized_upload), "the 10 MiB refusal should have a machine-readable error"
    for label, response, expected in invalids:
        assert response.status_code == expected, f"{label}: HTTP {response.status_code}, expected {expected}"
    assert noisy_output.status_code == 413
    assert _error_code(noisy_output), "the 8 MiB stored-image refusal should have a machine-readable error"
    assert after_rejections.status_code == 200
    assert after_rejections.get_json()["desired_revision"] == initial_revision
    assert final_preview.data == initial_preview.data


def test_kosync_manifest_auth_etags_checksums_and_stale_receipts(world):
    """Device manifests are identity-scoped and receipts apply only to the current revision.

    A valid registered reader is the positive control; this fails if auth, the
    manifest/image wire contract, 304 handling, revision freshness, or ack truth drifts.
    """
    user = _add_user(world, "alice")
    device_headers = world.device_headers("alice", "sleep-screen-password", "wire-reader")
    unknown_before = world.session.query(ub.Device).count()
    unknown_get = world.client.get("/kosync/syncs/sleep-screen", headers=device_headers)
    unknown_after = world.session.query(ub.Device).count()

    report = world.client.put(
        "/kosync/syncs/sleep-screen/device",
        headers=device_headers,
        json={
            "platform": "kindle", "plugin_version": "4.2.0", "supported": True,
            "screen_width": 8, "screen_height": 12,
        },
    )
    manifest = world.client.get("/kosync/syncs/sleep-screen", headers=device_headers)
    manifest_body = manifest.get_json(silent=True) or {}
    cached = world.client.get(
        "/kosync/syncs/sleep-screen",
        headers={**device_headers, "If-None-Match": manifest.headers.get("ETag", '"missing"')},
    )
    unauthorized = world.client.get(
        "/kosync/syncs/sleep-screen",
        headers={"X-CWNG-Device-ID": "wire-reader", "X-CWNG-Device-Name": "Kindle",
                 "If-None-Match": manifest.headers.get("ETag", '"missing"')},
    )
    auth = world.client.get(
        "/kosync/users/auth",
        headers=world.basic("alice", "sleep-screen-password"),
    )

    owner = world.browser("alice")
    device = _register_device(world, "alice", "wire-reader")
    uploaded = _upload(owner, device.public_id, _png(Image.new("L", (8, 12), 80)), "wire.png")
    upload_body = uploaded.get_json(silent=True) or {}
    image_manifest = world.client.get("/kosync/syncs/sleep-screen", headers=device_headers)
    image_manifest_body = image_manifest.get_json(silent=True) or {}
    download_path = image_manifest_body.get("download_path", "/syncs/sleep-screen/image?revision=missing")
    asset = world.client.get("/kosync" + download_path, headers=device_headers)
    asset_cached = world.client.get(
        "/kosync" + download_path,
        headers={**device_headers, "If-None-Match": asset.headers.get("ETag", '"missing"')},
    )

    changed_upload = _upload(owner, device.public_id, _png(Image.new("L", (8, 12), 120)), "new.png")
    changed_body = changed_upload.get_json(silent=True) or {}
    stale_asset = world.client.get(
        "/kosync/syncs/sleep-screen/image",
        query_string={"revision": image_manifest_body.get("revision", "missing")},
        headers=device_headers,
    )
    stale_ack = world.client.post(
        "/kosync/syncs/sleep-screen/ack",
        headers=device_headers,
        json={"revision": image_manifest_body.get("revision", "missing"), "status": "applied"},
    )
    current_manifest = world.client.get("/kosync/syncs/sleep-screen", headers=device_headers)
    current_body = current_manifest.get_json(silent=True) or {}
    ack = world.client.post(
        "/kosync/syncs/sleep-screen/ack",
        headers=device_headers,
        json={"revision": current_body.get("revision", "missing"), "status": "applied"},
    )
    ack_again = world.client.post(
        "/kosync/syncs/sleep-screen/ack",
        headers=device_headers,
        json={"revision": current_body.get("revision", "missing"), "status": "applied"},
    )

    assert unknown_get.status_code == 404
    assert unknown_after == unknown_before, "manifest GET must not register an unknown identity"
    assert report.status_code == 200, f"valid capability report returned HTTP {report.status_code}, expected 200"
    assert manifest.status_code == 200 and manifest_body["state"] == "none"
    assert manifest.headers.get("ETag", "").startswith('"')
    assert cached.status_code == 304
    assert unauthorized.status_code == 401, "auth and device identity must be checked before 304"
    assert auth.status_code == 200
    auth_body = auth.get_json() or {}
    capabilities = auth_body.get("capabilities", [])
    assert "sleep_screen_v1" in capabilities

    assert uploaded.status_code == 200
    assert image_manifest.status_code == 200 and image_manifest_body["state"] == "image"
    assert image_manifest_body["size"] == len(asset.data)
    assert image_manifest_body["sha256"] == hashlib.sha256(asset.data).hexdigest()
    assert asset.status_code == 200 and asset.mimetype == "image/png"
    assert asset.headers.get("Content-Length") == str(len(asset.data))
    assert asset.headers.get("ETag", "").startswith('"')
    assert "private" in asset.headers.get("Cache-Control", "")
    assert asset_cached.status_code == 304
    assert changed_upload.status_code == 200
    assert changed_body["desired_revision"] != upload_body["desired_revision"]
    assert stale_asset.status_code == 409 and _error_code(stale_asset) == "sleep_screen_changed"
    assert stale_ack.status_code == 409
    assert ack.status_code == ack_again.status_code == 200


def test_account_quota_serializes_concurrent_saves_at_64_mib(threaded_world):
    """Two simultaneous saves near the per-user cap cannot both publish beyond 64 MiB.

    Three existing 7 MP grayscale images leave room for one candidate. This
    fails if quota accounting omits either BLOB, checks outside the write
    transaction, or deletes another device's saved image on rejection.
    """
    _add_user(threaded_world, "alice")
    owner = threaded_world.browser("alice")
    candidate_png = _make_noise_png()
    assert len(candidate_png) <= UPLOAD_LIMIT

    seeded_devices = []
    for index in range(3):
        device_id = f"quota-reader-{index}"
        device = _register_device(threaded_world, "alice", device_id, label=f"Reader {index}")
        report = _report(
            threaded_world, "alice", device_id, width=2000, height=3500,
        )
        saved = _upload(owner, device.public_id, candidate_png, f"seed-{index}.png")
        assert report.status_code == 200
        assert saved.status_code == 200, f"seed image {index} returned HTTP {saved.status_code}, expected 200"
        seeded_devices.append(device)

    contenders = [
        _register_device(threaded_world, "alice", f"quota-contender-{index}", label=f"Contender {index}")
        for index in range(2)
    ]
    for index, device in enumerate(contenders):
        report = _report(
            threaded_world, "alice", f"quota-contender-{index}", width=2000, height=3500,
        )
        assert report.status_code == 200

    start_together = threading.Barrier(2)

    def save_contender(index, device):
        client = threaded_world.browser("alice")
        start_together.wait(timeout=10)
        return _upload(client, device.public_id, candidate_png, f"contender-{index}.png")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: save_contender(*pair), enumerate(contenders)))

    assert sorted(response.status_code for response in results) == [200, 413]
    assert sum(response.status_code == 200 for response in results) == 1
    seeded_ids = [device.id for device in seeded_devices]
    contender_ids = [device.id for device in contenders]
    threaded_world.session.remove()
    thread_rows = threaded_world.session.query(ub.Device).filter(
        ub.Device.user_id == threaded_world.session.query(ub.User.id).filter_by(name="alice").scalar_subquery()
    ).all()
    screen_model = ub.DeviceSleepScreen
    byte_columns = [column for column in screen_model.__table__.columns
                    if isinstance(column.type, LargeBinary)]
    all_rows = threaded_world.session.query(screen_model).all()
    stored_bytes = sum(
        len(getattr(row, column.key) or b"")
        for row in all_rows for column in byte_columns
    )
    saved_contenders = sum(
        threaded_world.session.query(screen_model).filter_by(device_id=device_id).count() > 0
        for device_id in contender_ids
    )
    assert stored_bytes <= ACCOUNT_LIMIT
    assert saved_contenders == 1
    assert len(thread_rows) == 5, "quota rejection must not drop a device or its owner"
    assert threaded_world.session.query(screen_model).filter(
        screen_model.device_id.in_(seeded_ids),
    ).count() == 3


def test_device_dimensions_reprepare_the_queued_source_and_unsupported_family_stays_blocked(world):
    """A real capability report determines the rendition; an unsupported family cannot install one.

    This distinguishes the server's source from each device's prepared canvas,
    ensures a repeat report is idempotent, and pairs a valid Kindle control
    with an unsupported Android report and upload refusal.
    """
    _add_user(world, "alice")
    owner = world.browser("alice")
    reader = _register_device(world, "alice", "dimensions-reader")
    source = _png(Image.new("RGB", (16, 8), (30, 170, 90)))
    queued = _upload(owner, reader.public_id, source, "source.png")
    queued_body = queued.get_json(silent=True) or {}
    waiting_preview = _image(owner, reader.public_id, queued_body.get("desired_revision", "missing"))

    first_report = _report(world, "alice", "dimensions-reader", width=8, height=12)
    first_state = _metadata(owner, reader.public_id).get_json() or {}
    first_image = _image(owner, reader.public_id, first_state.get("desired_revision", "missing"))
    repeat_report = _report(world, "alice", "dimensions-reader", width=8, height=12)
    repeated_state = _metadata(owner, reader.public_id).get_json() or {}
    changed_report = _report(world, "alice", "dimensions-reader", width=6, height=10)
    changed_state = _metadata(owner, reader.public_id).get_json() or {}
    changed_image = _image(owner, reader.public_id, changed_state.get("desired_revision", "missing"))

    unsupported = _register_device(world, "alice", "unsupported-reader", label="Android e-reader")
    unsupported_report = _report(
        world, "alice", "unsupported-reader", platform="android", supported=False,
        width=None, height=None,
    )
    unsupported_state = _metadata(owner, unsupported.public_id)
    blocked_upload = _upload(
        owner, unsupported.public_id, _png(Image.new("L", (4, 4), 90)), "blocked.png",
    )

    assert queued.status_code == 200 and queued_body["state"] == "waiting_for_device"
    assert waiting_preview.status_code == 200
    assert first_report.status_code == repeat_report.status_code == changed_report.status_code == 200
    assert first_state["state"] == "image" and first_state["screen_width"] == 8
    assert first_image.status_code == 200 and Image.open(io.BytesIO(first_image.data)).size == (8, 12)
    assert repeated_state["desired_revision"] == first_state["desired_revision"]
    assert changed_state["desired_revision"] != first_state["desired_revision"]
    assert Image.open(io.BytesIO(changed_image.data)).size == (6, 10)
    assert unsupported_report.status_code == 200
    assert unsupported_state.status_code == 200
    assert unsupported_state.get_json()["support"] == "unsupported"
    assert blocked_upload.status_code == 409
    assert _error_code(blocked_upload) == "sleep_screen_unsupported"
    assert _metadata(owner, unsupported.public_id).get_json()["state"] == "none"


def test_device_soft_delete_restores_previous_image_state_and_account_purge_removes_blobs(world):
    """Soft delete leaves only a removal tombstone, Undo does not revive bytes, and account purge removes the row.

    This fails if feature data outlives its device/account or the existing
    delete/Undo behavior loses the reader's removal instruction.
    """
    _add_user(world, "alice")
    from cps import annotations
    world.monkeypatch.setattr(
        annotations,
        "_visible_book_scopes_for_owners",
        lambda owners, _session, _candidates: {
            int(owner.id): frozenset() for owner in owners
        },
    )
    device = _register_device(world, "alice", "purge-reader")
    report = _report(world, "alice", "purge-reader")
    owner = world.browser("alice")
    uploaded = _upload(owner, device.public_id, _png(Image.new("L", (8, 12), 90)), "saved.png")
    uploaded_body = uploaded.get_json(silent=True) or {}

    retired = owner.delete(f"/api/annotations/devices/{device.public_id}")
    removal_manifest = world.client.get(
        "/kosync/syncs/sleep-screen",
        headers=world.device_headers("alice", "sleep-screen-password", "purge-reader"),
    )
    manifest_body = removal_manifest.get_json(silent=True) or {}
    obsolete_preview = _image(owner, device.public_id, uploaded_body.get("desired_revision", "missing"))
    undo = owner.post(f"/api/annotations/devices/{device.public_id}/restore")
    after_undo = _metadata(owner, device.public_id)
    after_undo_body = after_undo.get_json(silent=True) or {}
    user_id = world.session.query(ub.User.id).filter_by(name="alice").scalar()

    assert report.status_code == 200
    assert uploaded.status_code == 200, f"valid image upload returned HTTP {uploaded.status_code}, expected 200"
    assert retired.status_code == 200
    assert removal_manifest.status_code == 200 and manifest_body["state"] == "remove"
    assert "download_path" not in manifest_body
    assert obsolete_preview.status_code == 404
    assert undo.status_code == 200
    assert after_undo.status_code == 200 and after_undo_body["state"] == "remove"
    assert after_undo_body["preview_url"] is None

    screen_model = ub.DeviceSleepScreen
    byte_columns = [column for column in screen_model.__table__.columns
                    if isinstance(column.type, LargeBinary)]
    state_row = world.session.query(screen_model).filter_by(device_id=device.id).one()
    assert all(getattr(state_row, column.key) is None for column in byte_columns)
    from cps.user_account_data import purge_user_account
    purge_user_account(user_id, session=world.session)
    world.session.commit()
    assert world.session.get(ub.User, user_id) is None
    assert world.session.get(ub.Device, device.id) is None
    assert world.session.query(screen_model).filter_by(device_id=device.id).count() == 0


def test_french_and_dutch_sleep_screen_catalog_entries_match_the_ui_copy(world):
    """The existing locale API supplies the sleep-screen labels and interpolation text to the SPA.

    This fails when either compiled catalog omits the shipped msgids or falls
    back to English instead of the required French/Dutch copy.
    """
    expected_by_locale = {
        "fr": {
            "Sleep screen": "Écran de veille",
            "Sleep screen for {name}": "Écran de veille de {name}",
            "Set sleep screen": "Définir l’écran de veille",
            "Replace image": "Remplacer l’image",
            "Remove sleep screen": "Supprimer l’écran de veille",
            "Choose an image": "Choisir une image",
            "Sleep screen preview for {name}": "Aperçu de l’écran de veille de {name}",
            "Shown when this device sleeps in KOReader.": "Affiché lorsque cet appareil se met en veille dans KOReader.",
            "The native Kindle lock screen is unchanged.": "L’écran de verrouillage natif du Kindle reste inchangé.",
            "Waiting for next sync": "En attente de la prochaine synchronisation",
            "Applied on device": "Appliqué sur l’appareil",
            "Removal waiting for sync": "Suppression en attente de synchronisation",
            "Previous settings restored": "Réglages précédents restaurés",
            "Waiting for device details": "En attente des informations de l’appareil",
            "Update the CWNG plugin": "Mettre à jour le plugin CWNG",
            "Update the CWNG plugin on this device, then sync.": "Mettez à jour le plugin CWNG sur cet appareil, puis synchronisez.",
            "Connect this device and use Sync now in KOReader.": "Connectez cet appareil, puis utilisez Synchroniser maintenant dans KOReader.",
            "This device cannot show a custom KOReader sleep screen.": "Cet appareil ne peut pas afficher d’écran de veille KOReader personnalisé.",
            "Use a JPEG or PNG image up to 10 MiB.": "Utilisez une image JPEG ou PNG de 10 Mio maximum.",
            "Could not save sleep screen. Try again.": "Impossible d’enregistrer l’écran de veille. Réessayez.",
            "Could not apply sleep screen": "Impossible d’appliquer l’écran de veille",
            "Remove this image? Your previous KOReader sleep-screen settings will return after the next sync.": "Supprimer cette image ? Vos réglages précédents d’écran de veille KOReader seront restaurés après la prochaine synchronisation.",
        },
        "nl": {
            "Sleep screen": "Slaapscherm",
            "Sleep screen for {name}": "Slaapscherm voor {name}",
            "Set sleep screen": "Slaapscherm instellen",
            "Replace image": "Afbeelding vervangen",
            "Remove sleep screen": "Slaapscherm verwijderen",
            "Choose an image": "Kies een afbeelding",
            "Sleep screen preview for {name}": "Voorbeeld van het slaapscherm voor {name}",
            "Shown when this device sleeps in KOReader.": "Wordt getoond wanneer dit apparaat in KOReader in slaapstand gaat.",
            "The native Kindle lock screen is unchanged.": "Het oorspronkelijke Kindle-vergrendelscherm blijft ongewijzigd.",
            "Waiting for next sync": "Wachten op de volgende synchronisatie",
            "Applied on device": "Toegepast op het apparaat",
            "Removal waiting for sync": "Verwijdering wacht op synchronisatie",
            "Previous settings restored": "Vorige instellingen hersteld",
            "Waiting for device details": "Wachten op apparaatgegevens",
            "Update the CWNG plugin": "Werk de CWNG-plugin bij",
            "Update the CWNG plugin on this device, then sync.": "Werk de CWNG-plugin op dit apparaat bij en synchroniseer daarna.",
            "Connect this device and use Sync now in KOReader.": "Verbind dit apparaat en gebruik Nu synchroniseren in KOReader.",
            "This device cannot show a custom KOReader sleep screen.": "Dit apparaat kan geen aangepast KOReader-slaapscherm tonen.",
            "Use a JPEG or PNG image up to 10 MiB.": "Gebruik een JPEG- of PNG-afbeelding van maximaal 10 MiB.",
            "Could not save sleep screen. Try again.": "Het slaapscherm kon niet worden opgeslagen. Probeer het opnieuw.",
            "Could not apply sleep screen": "Het slaapscherm kon niet worden toegepast",
            "Remove this image? Your previous KOReader sleep-screen settings will return after the next sync.": "Deze afbeelding verwijderen? Je vorige KOReader-slaapscherminstellingen worden na de volgende synchronisatie hersteld.",
        },
    }

    for locale, expected in expected_by_locale.items():
        response = world.client.get(f"/api/v1/i18n/{locale}.json")
        assert response.status_code == 200
        catalog = (response.get_json() or {}).get("catalog", {})
        for msgid, translation in expected.items():
            assert catalog.get(msgid) == translation, (
                f"{locale} catalog translated {msgid!r} as {catalog.get(msgid)!r}, expected {translation!r}"
            )


def test_browser_writes_require_csrf_and_a_valid_upload_control(world):
    """Authenticated writes use the application's ordinary CSRF protection.

    This fails if a browser can mutate an owned device without a token, or if
    the same owner cannot save after obtaining the real CSRF token.
    """
    _add_user(world, "alice")
    reader = _register_device(world, "alice", "csrf-reader")
    owner = world.browser("alice")

    world.app.config["WTF_CSRF_ENABLED"] = True
    file_data = _png(Image.new("L", (8, 12), 100))
    without_token = _upload(owner, reader.public_id, file_data, "csrf.png")
    token_response = owner.get("/api/v1/auth/csrf")
    token = (token_response.get_json() or {}).get("csrf_token")
    with_token = owner.put(
        f"/api/v1/devices/{reader.public_id}/sleep-screen",
        data={"file": (io.BytesIO(file_data), "csrf.png", "image/png")},
        content_type="multipart/form-data",
        headers={"X-CSRFToken": token or "missing-token"},
    )

    assert token_response.status_code == 200 and token
    assert with_token.status_code == 200, f"CSRF-authenticated valid upload returned HTTP {with_token.status_code}"
    assert without_token.status_code == 400
