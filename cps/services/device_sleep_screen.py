# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private, bounded image intent for one KOReader device.

The caller owns the transaction. Acquiring the account's SQLite write lock
before quota reads serializes publication across threads and server processes.
Capability observations live on Device, so an observation is not a saved image.
"""
import hashlib
import io
import uuid
import warnings
from datetime import datetime, timezone

from PIL import Image, ImageOps
from sqlalchemy import func, text

from .. import ub

UPLOAD_LIMIT = 10 * 1024 * 1024
PNG_LIMIT = 8 * 1024 * 1024
ACCOUNT_LIMIT = 64 * 1024 * 1024
PLATFORMS = frozenset({'kindle', 'kobo', 'pocketbook', 'android', 'remarkable', 'emulator', 'other'})
SUPPORTED_PLATFORMS = frozenset({'kindle', 'kobo', 'emulator'})
ERROR_CODES = frozenset({'download_failed', 'invalid_image', 'checksum_mismatch',
                        'storage_failed', 'persistence_failed', 'unsupported',
                        'invalid_manifest', 'restore_failed'})


class SleepScreenError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    if not value:
        return None
    return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat().replace('+00:00', 'Z')


def row_for(device, session=None):
    return (session if session is not None else ub.session).get(ub.DeviceSleepScreen, device.id, populate_existing=True)


def lock_account(user_id, session=None):
    """Acquire the database write lock before reading current quota/intent.

    A no-op update obtains SQLite's write reservation without depending on
    process-local mutexes or a SELECT FOR UPDATE that SQLite ignores.
    """
    session = session if session is not None else ub.session
    result = session.execute(text('UPDATE user SET id=id WHERE id=:owner'), {'owner': int(user_id)})
    if result.rowcount != 1:
        raise SleepScreenError('not_found', 'Device not found', 404)


def normalize(upload):
    raw = upload.stream.read(UPLOAD_LIMIT + 1)
    if len(raw) > UPLOAD_LIMIT:
        raise SleepScreenError('sleep_screen_upload_too_large', 'Use an image up to 10 MiB', 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                from ..helper import validate_cover_dimensions
                validate_cover_dimensions(*source.size)
                if source.format not in ('PNG', 'JPEG') or getattr(source, 'n_frames', 1) != 1:
                    raise ValueError('unsupported or animated image')
                mime = (upload.content_type or '').split(';', 1)[0].lower()
                if mime not in ('', 'image/png', 'image/jpeg'):
                    raise ValueError('unsupported media type')
                source.load()
                oriented = ImageOps.exif_transpose(source).convert('RGBA')
                flattened = Image.new('RGBA', oriented.size, 'white')
                flattened.alpha_composite(oriented)
                gray = flattened.convert('L')
                # A fresh image prevents PNG/EXIF/ICC/text metadata from escaping.
                clean = Image.frombytes('L', gray.size, gray.tobytes())
                return encode(clean)
    except SleepScreenError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise SleepScreenError('sleep_screen_invalid_image', 'Choose a usable JPEG or PNG image') from None


def encode(image):
    output = io.BytesIO()
    image.save(output, format='PNG')
    data = output.getvalue()
    if len(data) > PNG_LIMIT:
        raise SleepScreenError('sleep_screen_image_too_large', 'Prepared image exceeds the 8 MiB limit', 413)
    return data


def prepare(source_png, width, height):
    with Image.open(io.BytesIO(source_png)) as source:
        source.load()
        fit = ImageOps.contain(source, (width, height), method=Image.Resampling.LANCZOS)
        canvas = Image.new('L', (width, height), 255)
        canvas.paste(fit, ((width-fit.width)//2, (height-fit.height)//2))
        return encode(canvas)


def capability(device):
    return device.sleep_screen_capability or {}


def support(device):
    cap = capability(device)
    if not cap:
        return 'unknown'
    return 'supported' if cap.get('supported') is True and cap.get('platform') in SUPPORTED_PLATFORMS else 'unsupported'


def metadata(device, row=None):
    cap = capability(device)
    revision = row.desired_revision if row else '0'
    has_image = row is not None and row.source_png is not None and device.active
    return {
        'desired_revision': revision,
        'state': row.state if row else 'none',
        'support': support(device), 'platform': cap.get('platform'),
        'plugin_version': cap.get('plugin_version'),
        'screen_width': cap.get('screen_width'), 'screen_height': cap.get('screen_height'),
        'observed_at': cap.get('observed_at'),
        'applied_revision': row.applied_revision if row else None,
        'applied_status': row.applied_status if row else None,
        'applied_at': _iso(row.applied_at) if row else None,
        'last_error': row.last_error if row and row.error_revision == revision else None,
        'preview_url': ('/api/v1/devices/%s/sleep-screen/image?revision=%s' % (device.public_id, revision)) if has_image else None,
    }


def check_quota(device, source, prepared, session=None):
    session = session if session is not None else ub.session
    used = session.query(func.coalesce(func.sum(
        func.coalesce(func.length(ub.DeviceSleepScreen.source_png), 0)
        + func.coalesce(func.length(ub.DeviceSleepScreen.prepared_png), 0)), 0)).join(
            ub.Device, ub.Device.id == ub.DeviceSleepScreen.device_id).filter(
                ub.Device.user_id == device.user_id,
                ub.Device.id != device.id).scalar()
    if int(used or 0) + len(source or b'') + len(prepared or b'') > ACCOUNT_LIMIT:
        raise SleepScreenError('sleep_screen_quota_exceeded', 'Sleep-screen storage limit reached', 413)


def advance(row, state):
    row.state = state
    row.desired_revision = str(uuid.uuid4())
    row.updated_at = _now()
    row.last_error = row.error_revision = None


def save(device, source, session=None):
    session = session if session is not None else ub.session
    if support(device) == 'unsupported':
        raise SleepScreenError('sleep_screen_unsupported', 'This device cannot show a custom KOReader sleep screen', 409)
    cap = capability(device)
    prepared = prepare(source, cap['screen_width'], cap['screen_height']) if support(device) == 'supported' else None
    check_quota(device, source, prepared, session)
    row = row_for(device, session)
    if row is None:
        row = ub.DeviceSleepScreen(device_id=device.id)
        session.add(row)
    row.source_png, row.prepared_png = source, prepared
    advance(row, 'image' if prepared else 'waiting_for_device')
    return row


def remove(device, session=None):
    session = session if session is not None else ub.session
    row = row_for(device, session)
    if row is None:
        row = ub.DeviceSleepScreen(device_id=device.id)
        session.add(row)
    if row.state != 'remove':
        row.source_png = row.prepared_png = None
        advance(row, 'remove')
    return row


def validate_report(body):
    if not isinstance(body, dict) or set(body) != {'platform', 'plugin_version', 'supported', 'screen_width', 'screen_height'}:
        raise SleepScreenError('sleep_screen_invalid_report', 'Invalid device details')
    platform, version, supported = body['platform'], body['plugin_version'], body['supported']
    if (not isinstance(platform, str) or platform not in PLATFORMS
            or not isinstance(version, str) or not 1 <= len(version) <= 64
            or any(ord(c) < 32 for c in version) or type(supported) is not bool):
        raise SleepScreenError('sleep_screen_invalid_report', 'Invalid device details')
    width, height = body['screen_width'], body['screen_height']
    if width is None and height is None and not supported:
        pass
    elif (type(width) is not int or type(height) is not int or not 1 <= width <= 4096
          or not 1 <= height <= 4096 or width * height > 8_000_000):
        raise SleepScreenError('sleep_screen_invalid_report', 'Invalid screen dimensions')
    else:
        width, height = sorted((width, height))
    return dict(platform=platform, plugin_version=version,
                supported=supported and platform in SUPPORTED_PLATFORMS,
                screen_width=width, screen_height=height, observed_at=_iso(_now()))


def observe(device, report, session=None):
    session = session if session is not None else ub.session
    previous = capability(device)
    row = row_for(device, session)
    prepared = None
    changed = any(previous.get(key) != report.get(key) for key in ('supported', 'platform', 'screen_width', 'screen_height'))
    if row and row.source_png and changed and report['supported']:
        prepared = prepare(row.source_png, report['screen_width'], report['screen_height'])
        check_quota(device, row.source_png, prepared, session)
    device.sleep_screen_capability = report
    if row and row.source_png and changed:
        row.prepared_png = prepared
        advance(row, 'image' if prepared else 'waiting_for_device')
    return row


def manifest(device, row):
    if not device.active:
        return {'revision': row.desired_revision if row else '0', 'state': 'remove'}
    result = {'revision': row.desired_revision if row else '0', 'state': row.state if row else 'none'}
    if row and row.state == 'image':
        data = row.prepared_png
        cap = capability(device)
        result.update(width=cap['screen_width'], height=cap['screen_height'],
                      content_type='image/png', size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                      download_path='/syncs/sleep-screen/image?revision=' + row.desired_revision)
    return result


def acknowledge(device, body, session=None):
    session = session if session is not None else ub.session
    if not isinstance(body, dict) or set(body) - {'revision', 'status', 'error'}:
        raise SleepScreenError('sleep_screen_invalid_ack', 'Invalid acknowledgement')
    row = row_for(device, session)
    if row is None or body.get('revision') != row.desired_revision:
        raise SleepScreenError('sleep_screen_changed', 'Sleep screen changed; sync again', 409)
    status = body.get('status')
    if status == 'failed':
        error = body.get('error')
        if not isinstance(error, str) or error not in ERROR_CODES:
            raise SleepScreenError('sleep_screen_invalid_ack', 'Invalid failure code')
        row.last_error, row.error_revision = error, row.desired_revision
    elif ((status == 'applied' and row.state == 'image' and device.active)
          or (status == 'removed' and row.state == 'remove')):
        if 'error' in body:
            raise SleepScreenError('sleep_screen_invalid_ack', 'Unexpected failure code')
        if row.applied_revision != row.desired_revision or row.applied_status != status:
            row.applied_revision, row.applied_status, row.applied_at = row.desired_revision, status, _now()
        row.last_error = row.error_revision = None
    else:
        raise SleepScreenError('sleep_screen_invalid_ack', 'Acknowledgement does not match device intent', 409)
    return row
