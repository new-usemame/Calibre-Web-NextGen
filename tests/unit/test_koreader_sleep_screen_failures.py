# SPDX-License-Identifier: GPL-3.0-or-later
"""Failure-path controls beyond the independently owned acceptance contract."""
import io

import pytest
from PIL import Image
from sqlalchemy.exc import OperationalError
from werkzeug.datastructures import MultiDict

from tests.unit.test_koreader_sleep_screen_acceptance import (
    world, _add_user, _register_device, _report, _upload, _png, _metadata, _image,
)

pytestmark = pytest.mark.unit


def ready(world):
    _add_user(world, 'alice')
    device = _register_device(world, 'alice', 'failure-reader')
    assert _report(world, 'alice', 'failure-reader').status_code == 200
    client = world.browser('alice')
    response = _upload(client, device.public_id, _png(Image.new('L', (8, 12), 80)), 'first.png')
    assert response.status_code == 200
    return client, device, response.get_json()['desired_revision']


def test_commit_failure_rolls_back_replacement_and_preserves_previous_bytes(world, monkeypatch):
    client, device, revision = ready(world)
    before = _image(client, device.public_id, revision).data
    session = world.session
    original_commit = session.commit

    def fail_commit():
        raise OperationalError('injected commit failure', {}, Exception('disk full'))

    monkeypatch.setattr(session, 'commit', fail_commit)
    response = _upload(client, device.public_id, _png(Image.new('L', (8, 12), 170)), 'replacement.png')
    assert response.status_code == 503
    monkeypatch.setattr(session, 'commit', original_commit)
    assert _metadata(client, device.public_id).get_json()['desired_revision'] == revision
    assert _image(client, device.public_id, revision).data == before


def test_failed_receipt_retains_previous_success_until_current_success(world):
    client, device, revision = ready(world)
    headers = world.device_headers('alice', 'sleep-screen-password', 'failure-reader')
    path = '/kosync/syncs/sleep-screen/ack'
    assert world.client.post(path, headers=headers, json={'revision': revision, 'status': 'applied'}).status_code == 200
    replacement = _upload(client, device.public_id, _png(Image.new('L', (8, 12), 170)), 'replacement.png')
    current = replacement.get_json()['desired_revision']
    assert current != revision
    assert world.client.post(path, headers=headers, json={
        'revision': current, 'status': 'failed', 'error': 'checksum_mismatch',
    }).status_code == 200
    metadata = _metadata(client, device.public_id).get_json()
    assert metadata['applied_revision'] == revision
    assert metadata['applied_status'] == 'applied'
    assert metadata['last_error'] == 'checksum_mismatch'
    assert world.client.post(path, headers=headers, json={
        'revision': current, 'status': 'failed', 'error': 'arbitrary private text',
    }).status_code == 400
    assert world.client.post(path, headers=headers, json={'revision': current, 'status': 'applied'}).status_code == 200
    metadata = _metadata(client, device.public_id).get_json()
    assert metadata['applied_revision'] == current
    assert metadata['last_error'] is None


def test_duplicate_file_values_cannot_silently_replace_a_saved_image(world):
    client, device, revision = ready(world)
    png = _png(Image.new('L', (8, 12), 170))
    response = client.put(f'/api/v1/devices/{device.public_id}/sleep-screen',
        data=MultiDict([('file', (io.BytesIO(png), 'one.png')),
                        ('file', (io.BytesIO(png), 'two.png'))]),
        content_type='multipart/form-data')
    assert response.status_code == 400
    assert _metadata(client, device.public_id).get_json()['desired_revision'] == revision


@pytest.mark.parametrize('width,height', [(True, 12), (8, '12'), (1.5, 12), (4096, 4096)])
def test_bad_dimensions_cannot_register_or_prepare_an_identity(world, width, height):
    _add_user(world, 'alice')
    from cps import ub
    before = world.session.query(ub.Device).count()
    assert _report(world, 'alice', 'bad-dimensions', width=width, height=height).status_code == 400
    assert world.session.query(ub.Device).count() == before


def test_body_limit_precedes_csrf_multipart_parsing_and_keeps_other_uploads_unchanged():
    from flask import Flask, request
    from flask_wtf.csrf import CSRFProtect
    from cps import _register_sleep_screen_upload_limiter
    from cps.services.device_sleep_screen import UPLOAD_LIMIT

    app = Flask(__name__)
    app.secret_key = 'bounded-upload-test'
    _register_sleep_screen_upload_limiter(app)
    _register_sleep_screen_upload_limiter(app)
    csrf = CSRFProtect(app)

    @app.route('/sleep', methods=['PUT'], endpoint='api_v1.device_sleep_screen')
    def sleep():
        return 'route must never parse an oversized body'

    @app.route('/book', methods=['PUT'])
    @csrf.exempt
    def book():
        return {'max_content_length': request.max_content_length}

    client = app.test_client()
    # Without the early hook this is 400 (CSRF missing), after parsing the form.
    response = client.put('/sleep', data=b'x' * (UPLOAD_LIMIT + 256 * 1024 + 1),
                          content_type='multipart/form-data; boundary=missing')
    assert response.status_code == 413
    assert response.get_json()['error']['code'] == 'sleep_screen_upload_too_large'
    assert client.put('/book', data=b'book').get_json()['max_content_length'] is None
    assert len(app.before_request_funcs[None]) == 2, 'registration is idempotent'
