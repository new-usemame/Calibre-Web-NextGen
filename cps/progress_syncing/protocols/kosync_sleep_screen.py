# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Identity-scoped KOReader sleep-screen manifest, image and receipt."""
import hashlib
import json

from flask import jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from .kosync import kosync, _require_kosync_enabled, authenticate_user
from .kosync_library import _device_identity
from ... import csrf, logger, ub
from ...services import device_registry, device_sleep_screen as screens
from ...api.device_sleep_screen import bytes_response, error, private

log = logger.create()


def _context(*, register=False):
    blocked = _require_kosync_enabled()
    if blocked is not None:
        return None, blocked
    user = authenticate_user()
    if user is None:
        return None, error('unauthorized', 'Authentication required', 401)
    # Unlike the older library compatibility API, identity must be in headers.
    if not request.headers.get('X-CWNG-Device-ID') or not request.headers.get('X-CWNG-Device-Name'):
        return None, error('not_found', 'Device not found', 404)
    identity = _device_identity()
    if identity is None:
        return None, error('not_found', 'Device not found', 404)
    device = device_registry.find_koreader_device(user_id=user.id, device_id=identity[1])
    if device is None and register:
        internal = device_registry.register_koreader_device_best_effort(
            user_id=user.id, device_id=identity[1], device_name=identity[0])
        if internal is None:
            return None, error('sleep_screen_unavailable', 'Device registration failed', 503)
        device = ub.session.get(ub.Device, internal)
    if device is None:
        return None, error('not_found', 'Device not found', 404)
    # Resolution may return a previously loaded identity relationship; refresh
    # liveness/capability before any conditional response or image publication.
    device = ub.session.get(ub.Device, device.id, populate_existing=True)
    if device is None:
        return None, error('not_found', 'Device not found', 404)
    return device, None


def _failure(exc):
    ub.session.rollback()
    if isinstance(exc, screens.SleepScreenError):
        return error(exc.code, exc.message, exc.status)
    log.exception('Device sleep screen transaction failed')
    return error('sleep_screen_unavailable', 'Sleep screen storage unavailable', 503)


@csrf.exempt
@kosync.route('/kosync/syncs/sleep-screen/device', methods=['PUT'])
def report_sleep_screen_device():
    try:
        # Authenticate first; invalid reports must not create a registry row.
        device, blocked = _context()
        if blocked is not None and blocked.status_code != 404:
            return blocked
        report = screens.validate_report(request.get_json(silent=True))
        if device is None:
            device, blocked = _context(register=True)
            if blocked is not None:
                return blocked
        user_id, device_id = int(device.user_id), int(device.id)
        screens.lock_account(user_id)
        device = ub.session.get(ub.Device, device_id, populate_existing=True)
        if device is None:
            raise screens.SleepScreenError('not_found', 'Device not found', 404)
        if not device.active:
            row = screens.remove(device)
        else:
            row = screens.observe(device, report)
        ub.session.commit()
        return private(jsonify(screens.metadata(device, row)))
    except (screens.SleepScreenError, SQLAlchemyError) as exc:
        return _failure(exc)


@csrf.exempt
@kosync.route('/kosync/syncs/sleep-screen', methods=['GET'])
def sleep_screen_manifest():
    device, blocked = _context()
    if blocked is not None:
        return blocked
    payload = screens.manifest(device, screens.row_for(device))
    response = jsonify(payload)
    response.set_etag(hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
    response.make_conditional(request)
    return private(response)


@csrf.exempt
@kosync.route('/kosync/syncs/sleep-screen/image', methods=['GET'])
def sleep_screen_image():
    device, blocked = _context()
    if blocked is not None:
        return blocked
    row = screens.row_for(device)
    if not device.active or row is None or row.state != 'image' or not row.prepared_png:
        return error('not_found', 'Sleep screen image not found', 404)
    if request.args.get('revision') != row.desired_revision:
        return error('sleep_screen_changed', 'Sleep screen changed; sync again', 409)
    return bytes_response(row.prepared_png)


@csrf.exempt
@kosync.route('/kosync/syncs/sleep-screen/ack', methods=['POST'])
def acknowledge_sleep_screen():
    try:
        device, blocked = _context()
        if blocked is not None:
            return blocked
        user_id, device_id = int(device.user_id), int(device.id)
        screens.lock_account(user_id)
        device = ub.session.get(ub.Device, device_id, populate_existing=True)
        if device is None:
            raise screens.SleepScreenError('not_found', 'Device not found', 404)
        row = screens.acknowledge(device, request.get_json(silent=True))
        ub.session.commit()
        return private(jsonify({'revision': row.applied_revision, 'status': row.applied_status}))
    except (screens.SleepScreenError, SQLAlchemyError) as exc:
        return _failure(exc)
