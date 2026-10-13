# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Account-owned KOReader sleep-screen editor and private preview."""
import hashlib

from flask import jsonify, make_response, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge

from . import api_v1
from .. import logger, ub
from ..cw_login import current_user
from ..services import device_sleep_screen as screens

log = logger.create()


def error(code, message, status):
    response = jsonify({'error': {'code': code, 'message': message}})
    response.status_code = status
    response.headers['Cache-Control'] = 'private, no-store'
    return response


def private(response):
    response.headers['Cache-Control'] = 'private, no-cache'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    for name in ('Cookie', 'Authorization', 'X-CWNG-Device-ID'):
        response.vary.add(name)
    return response


def bytes_response(data):
    response = make_response(data)
    response.mimetype = 'image/png'
    response.set_etag(hashlib.sha256(data).hexdigest())
    response.make_conditional(request)
    return private(response)


def _owned(public_id, user_id, *, active=True):
    query = ub.session.query(ub.Device).filter_by(public_id=public_id, user_id=user_id, kind='koreader')
    if active:
        query = query.filter_by(active=True)
    return query.populate_existing().first()


def _guard():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return error('unauthorized', 'You must be signed in', 401)
    return None


@api_v1.route('/devices/<public_id>/sleep-screen', methods=['GET', 'PUT', 'DELETE'])
def device_sleep_screen(public_id):
    blocked = _guard()
    if blocked is not None:
        return blocked
    user_id = int(current_user.id)
    device = _owned(public_id, user_id)
    if device is None:
        return error('not_found', 'Device not found', 404)
    if request.method == 'GET':
        return private(jsonify(screens.metadata(device, screens.row_for(device))))
    try:
        source = None
        if request.method == 'PUT':
            request.max_content_length = screens.UPLOAD_LIMIT + 256 * 1024
            upload = request.files.get('file')
            if (upload is None or not upload.filename or set(request.files) != {'file'}
                    or len(request.files.getlist('file')) != 1 or request.form):
                return error('sleep_screen_invalid_image', 'Choose one JPEG or PNG image', 400)
            source = screens.normalize(upload)
        screens.lock_account(user_id)
        # Ownership/liveness must still hold after waiting for the write lock.
        device = _owned(public_id, user_id)
        if device is None:
            ub.session.rollback()
            return error('not_found', 'Device not found', 404)
        row = screens.save(device, source) if request.method == 'PUT' else screens.remove(device)
        ub.session.commit()
        return private(jsonify(screens.metadata(device, row)))
    except screens.SleepScreenError as exc:
        ub.session.rollback()
        return error(exc.code, exc.message, exc.status)
    except RequestEntityTooLarge:
        ub.session.rollback()
        return error('sleep_screen_upload_too_large', 'Use an image up to 10 MiB', 413)
    except SQLAlchemyError:
        ub.session.rollback()
        log.exception('Could not save device sleep screen')
        return error('sleep_screen_unavailable', 'Could not save sleep screen. Try again.', 503)


@api_v1.route('/devices/<public_id>/sleep-screen/image', methods=['GET'])
def device_sleep_screen_image(public_id):
    blocked = _guard()
    if blocked is not None:
        return blocked
    device = _owned(public_id, int(current_user.id))
    row = screens.row_for(device) if device is not None else None
    if row is None or row.source_png is None:
        return error('not_found', 'Sleep screen image not found', 404)
    if request.args.get('revision') != row.desired_revision:
        return error('sleep_screen_changed', 'Sleep screen changed; refresh the preview', 409)
    return bytes_response(row.prepared_png or row.source_png)
