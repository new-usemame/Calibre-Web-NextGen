# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Byte-preserving Kobo annotation JSON capture.

The normal JSON decoder remains authoritative for the existing parsed-column
pipeline.  This module independently records lexical spans from the original
request bytes; it never reserializes the location object.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


MAX_RAW_ANNOTATION_BYTES = 64 * 1024
_WHITESPACE = b" \t\r\n"


class LexicalCaptureError(ValueError):
    """The wire body cannot produce a bounded, exact materialization."""


def _reject_duplicate_object_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LexicalCaptureError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


@dataclass(frozen=True)
class RawKoboAnnotation:
    annotation_id: str
    raw_annotation_json: bytes
    raw_location_json: bytes
    raw_client_modified_utc: str
    payload_sha256: str
    attachments_state: str


def _skip_ws(raw: bytes, offset: int) -> int:
    while offset < len(raw) and raw[offset] in _WHITESPACE:
        offset += 1
    return offset


def _scan_string(raw: bytes, offset: int) -> int:
    if offset >= len(raw) or raw[offset] != ord('"'):
        raise LexicalCaptureError("expected JSON string")
    offset += 1
    while offset < len(raw):
        value = raw[offset]
        if value == ord('"'):
            return offset + 1
        if value < 0x20:
            raise LexicalCaptureError("unescaped control byte in JSON string")
        if value == ord('\\'):
            offset += 1
            if offset >= len(raw):
                raise LexicalCaptureError("truncated JSON escape")
            if raw[offset] == ord('u'):
                if offset + 4 >= len(raw):
                    raise LexicalCaptureError("truncated JSON unicode escape")
                try:
                    int(raw[offset + 1:offset + 5], 16)
                except ValueError as error:
                    raise LexicalCaptureError("invalid JSON unicode escape") from error
                offset += 4
        offset += 1
    raise LexicalCaptureError("unterminated JSON string")


def _scan_value(raw: bytes, offset: int) -> int:
    offset = _skip_ws(raw, offset)
    if offset >= len(raw):
        raise LexicalCaptureError("missing JSON value")
    first = raw[offset]
    if first == ord('"'):
        return _scan_string(raw, offset)
    if first == ord('{'):
        cursor = offset + 1
        cursor = _skip_ws(raw, cursor)
        if cursor < len(raw) and raw[cursor] == ord('}'):
            return cursor + 1
        while True:
            key_end = _scan_string(raw, _skip_ws(raw, cursor))
            cursor = _skip_ws(raw, key_end)
            if cursor >= len(raw) or raw[cursor] != ord(':'):
                raise LexicalCaptureError("missing JSON object colon")
            cursor = _scan_value(raw, cursor + 1)
            cursor = _skip_ws(raw, cursor)
            if cursor >= len(raw):
                raise LexicalCaptureError("unterminated JSON object")
            if raw[cursor] == ord('}'):
                return cursor + 1
            if raw[cursor] != ord(','):
                raise LexicalCaptureError("missing JSON object comma")
            cursor += 1
    if first == ord('['):
        cursor = _skip_ws(raw, offset + 1)
        if cursor < len(raw) and raw[cursor] == ord(']'):
            return cursor + 1
        while True:
            cursor = _scan_value(raw, cursor)
            cursor = _skip_ws(raw, cursor)
            if cursor >= len(raw):
                raise LexicalCaptureError("unterminated JSON array")
            if raw[cursor] == ord(']'):
                return cursor + 1
            if raw[cursor] != ord(','):
                raise LexicalCaptureError("missing JSON array comma")
            cursor = _skip_ws(raw, cursor + 1)

    cursor = offset
    while cursor < len(raw) and raw[cursor] not in b",]} \t\r\n":
        cursor += 1
    if cursor == offset:
        raise LexicalCaptureError("invalid JSON scalar")
    try:
        json.loads(raw[offset:cursor])
    except (TypeError, ValueError, UnicodeDecodeError) as error:
        raise LexicalCaptureError("invalid JSON scalar") from error
    return cursor


def _complete_value_span(raw: bytes) -> tuple[int, int]:
    start = _skip_ws(raw, 0)
    end = _scan_value(raw, start)
    if _skip_ws(raw, end) != len(raw):
        raise LexicalCaptureError("trailing non-whitespace bytes")
    return start, end


def _object_members(raw: bytes):
    start, end = _complete_value_span(raw)
    if raw[start] != ord('{'):
        raise LexicalCaptureError("expected one JSON object")
    cursor = _skip_ws(raw, start + 1)
    if cursor < end and raw[cursor] == ord('}'):
        return
    seen_keys = set()
    while cursor < end:
        key_start = _skip_ws(raw, cursor)
        key_end = _scan_string(raw, key_start)
        try:
            key = json.loads(raw[key_start:key_end])
        except (ValueError, UnicodeDecodeError) as error:
            raise LexicalCaptureError("invalid JSON object key") from error
        if key in seen_keys:
            raise LexicalCaptureError(f"duplicate JSON object key: {key}")
        seen_keys.add(key)
        cursor = _skip_ws(raw, key_end)
        if cursor >= end or raw[cursor] != ord(':'):
            raise LexicalCaptureError("missing JSON object colon")
        value_start = _skip_ws(raw, cursor + 1)
        value_end = _scan_value(raw, value_start)
        yield key, value_start, value_end
        cursor = _skip_ws(raw, value_end)
        if raw[cursor] == ord('}'):
            return
        if raw[cursor] != ord(','):
            raise LexicalCaptureError("missing JSON object comma")
        cursor = _skip_ws(raw, cursor + 1)
    raise LexicalCaptureError("unterminated JSON object")


def extract_object_member_value(raw_object: bytes, member_name: str) -> bytes:
    match = None
    for key, start, end in _object_members(raw_object):
        if key == member_name:
            match = raw_object[start:end]
    if match is None:
        raise LexicalCaptureError(f"missing required {member_name} member")
    return match


def _array_values(raw_array: bytes):
    start, end = _complete_value_span(raw_array)
    if raw_array[start] != ord('['):
        raise LexicalCaptureError("updatedAnnotations is not an array")
    cursor = _skip_ws(raw_array, start + 1)
    if raw_array[cursor] == ord(']'):
        return
    while cursor < end:
        value_start = cursor
        value_end = _scan_value(raw_array, value_start)
        yield raw_array[value_start:value_end]
        cursor = _skip_ws(raw_array, value_end)
        if raw_array[cursor] == ord(']'):
            return
        if raw_array[cursor] != ord(','):
            raise LexicalCaptureError("missing JSON array comma")
        cursor = _skip_ws(raw_array, cursor + 1)


def _attachments_state(parsed: dict) -> str:
    if "attachments" not in parsed:
        return "missing"
    attachments = parsed["attachments"]
    if not isinstance(attachments, dict):
        return "invalid"
    return "empty" if not attachments else "nonempty"


def extract_annotation_materializations(
    raw_request: bytes, *, member_name: str,
) -> list[RawKoboAnnotation]:
    """Extract exact annotation/location slices from a named response member."""
    if not isinstance(raw_request, bytes):
        raise LexicalCaptureError("request body must be bytes")
    # Validate duplicate keys recursively before selecting any lexical span.
    # The normal parsed pipeline uses Python's last-wins decoder; storing a
    # first lexical match would make the sidecar disagree silently.
    try:
        json.loads(raw_request, object_pairs_hook=_reject_duplicate_object_keys)
    except LexicalCaptureError:
        raise
    except (ValueError, UnicodeDecodeError) as error:
        raise LexicalCaptureError("request body is invalid UTF-8 JSON") from error
    raw_array = extract_object_member_value(raw_request, member_name)
    records = []
    for raw_object in _array_values(raw_array):
        if len(raw_object) > MAX_RAW_ANNOTATION_BYTES:
            raise LexicalCaptureError("annotation object exceeds 64 KiB")
        try:
            parsed = json.loads(raw_object)
        except (ValueError, UnicodeDecodeError) as error:
            raise LexicalCaptureError("annotation object is invalid UTF-8 JSON") from error
        if not isinstance(parsed, dict):
            raise LexicalCaptureError("annotation member is not an object")
        annotation_id = parsed.get("id")
        client_time = parsed.get("clientLastModifiedUtc")
        if not isinstance(annotation_id, str) or not annotation_id:
            raise LexicalCaptureError("annotation id is missing or invalid")
        if not isinstance(client_time, str) or not client_time:
            raise LexicalCaptureError("clientLastModifiedUtc is missing or invalid")
        raw_location = extract_object_member_value(raw_object, "location")
        try:
            location = json.loads(raw_location)
        except (ValueError, UnicodeDecodeError) as error:
            raise LexicalCaptureError("location is invalid UTF-8 JSON") from error
        if not isinstance(location, dict):
            raise LexicalCaptureError("location is not an object")
        records.append(RawKoboAnnotation(
            annotation_id=annotation_id,
            raw_annotation_json=raw_object,
            raw_location_json=raw_location,
            raw_client_modified_utc=client_time,
            payload_sha256=hashlib.sha256(raw_object).hexdigest(),
            attachments_state=_attachments_state(parsed),
        ))
    return records


def extract_updated_annotation_materializations(raw_request: bytes) -> list[RawKoboAnnotation]:
    """Extract exact annotation/location slices from one Kobo PATCH body."""
    return extract_annotation_materializations(
        raw_request, member_name="updatedAnnotations",
    )


def project_exact_materialization(raw_annotation_json: bytes, raw_location_json: bytes) -> bytes:
    """Return an exact replay object only when its location invariant holds."""
    if extract_object_member_value(raw_annotation_json, "location") != raw_location_json:
        raise LexicalCaptureError("stored raw location differs from replay object")
    return raw_annotation_json
