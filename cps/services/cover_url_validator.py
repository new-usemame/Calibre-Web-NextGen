# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Validate a cover URL before it gets committed.

Used by two callers:

  1. ``POST /metadata/cover/preview`` — the live-preview endpoint behind
     the inline ``cover_url`` field on the edit-metadata page.
  2. The cover-picker page's URL-paste panel.

Both want the same answer: does this URL serve a real image, what are its
dimensions, what's its content-type, is the size within our limits, would
the SSRF guard let us fetch it later. The validator runs the same checks
``helper.save_cover_from_url`` does on the save path so previewing is
faithful to what would actually happen on commit.

Pure-functional. No Flask state. No DB writes. Easy to test with mocked
HTTP. No new dependencies — uses the existing ``cw_advocate`` SSRF guard
and ``Pillow`` (already in requirements via ``cps.helper``).
"""
from __future__ import annotations

import dataclasses
import io
import os
import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

try:
    from .. import cw_advocate
    from ..cw_advocate.exceptions import UnacceptableAddressException
    _ADVOCATE_AVAILABLE = True
except ImportError:  # pragma: no cover - dev/test envs without advocate
    cw_advocate = None
    UnacceptableAddressException = type("UnacceptableAddressException", (Exception,), {})
    _ADVOCATE_AVAILABLE = False

try:
    from PIL import Image as _PILImage
except ImportError:  # pragma: no cover - dev envs without Pillow
    _PILImage = None

import requests

from .. import constants, logger


log = logger.create()

_DEFAULT_TIMEOUT = float(os.environ.get("CWA_COVER_PICKER_TIMEOUT", "4"))
_DEFAULT_MAX_BYTES = int(os.environ.get("CWA_COVER_DOWNLOAD_MAX_BYTES", str(15 * 1024 * 1024)))
# A sane minimum — anything below 5 KB is almost certainly a placeholder
# (Amazon's 43-byte image/gif, OL's blank cover, etc.).
_MIN_BYTES = 5_000
# How many bytes to read from the body for dimension detection. Most JPEG
# headers fit in the first 4 KB. PNG IHDR is at offset 16. WebP is at 26.
_DIM_PROBE_BYTES = 8 * 1024
_ACCEPTED_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp")
# HEAD answers that say nothing about whether a GET would work: some hosts
# refuse HEAD outright (405/501) and Wikimedia answers 403 to a HEAD from an
# unidentified client while serving the GET. Fall back to a streamed GET.
_HEAD_FALLBACK_STATUSES = (403, 405, 501)
# Google Images "view image" links wrap the real image URL in the results
# page: https://www.google.com/imgres?imgurl=<image>&imgrefurl=... — the page
# itself serves text/html. google.<tld>, www.google.<tld>, images.google.<tld>
# with either a plain or a two-level country TLD (google.co.uk, google.com.au).
_GOOGLE_IMGRES_HOST = re.compile(r"^(?:www\.|images\.)?google\.[a-z]{2,3}(?:\.[a-z]{2})?$")


def cover_fetch_headers() -> dict:
    """Headers every remote cover fetch sends — the validator probe, the
    save-path download and the picker's preview fetch alike.

    Image hosts key their bot policy on the User-Agent: Wikimedia answers
    403 to the bare ``python-requests/x`` default (HEAD and GET) and 200 to
    an identifying agent, so a perfectly good link was refused as
    "Server returned HTTP 403." Identify the software with a contact URL,
    the way the hosts' policies ask, and say we want an image."""
    return {
        "User-Agent": f"{constants.USER_AGENT} (+https://github.com/new-usemame/calibre-web-nextgen)",
        "Accept": "image/*,*/*;q=0.8",
    }


def resolve_pasted_cover_url(url: str) -> str:
    """Unwrap the image URL from a Google Images results link.

    Copying a link from Google Images often yields the results-page form
    ``https://www.google.com/imgres?imgurl=<percent-encoded image url>&...``
    instead of the image itself; the page is text/html, so validating it
    verbatim refuses a link the user rightly expects to work. For that host
    and path, return the ``imgurl`` parameter (already percent-decoded)
    when it is itself an http(s) URL. Anything else — other hosts carrying
    an ``imgurl`` parameter, a non-http inner value — comes back unchanged
    and is validated as what it is.
    """
    url = (url or "").strip()
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not _GOOGLE_IMGRES_HOST.match(host):
        return url
    if parts.path.rstrip("/") != "/imgres":
        return url
    inner = (parse_qs(parts.query).get("imgurl") or [""])[0].strip()
    if inner.lower().startswith(("http://", "https://")):
        return inner
    return url


@dataclasses.dataclass
class ValidationResult:
    """The shape returned by :func:`validate_cover_url` and serialized to
    JSON by the live-preview endpoint."""

    valid: bool
    url: str
    error_code: Optional[str] = None      # machine-readable: 'ssrf_blocked', 'too_small', ...
    error_message: Optional[str] = None   # human-readable, safe to show in the UI
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    # The URL that will actually be downloaded when it differs from the
    # pasted one (a Google Images link unwrapped to the image behind it);
    # None when the input is used as-is. Clients apply this one and keep
    # comparing ``url`` to what the user typed.
    resolved_url: Optional[str] = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def validate_cover_url(url: str) -> ValidationResult:
    """Probe ``url`` and return whether it would succeed if saved as a cover.

    The probe is lightweight: a HEAD first to check status + content-type +
    size, then a partial GET to read dimensions out of the image header.
    Total network bytes are bounded by ``_DIM_PROBE_BYTES``. The HEAD probe
    obeys the same SSRF guard the save path uses, so URLs that would fail
    on commit (localhost, RFC1918) fail at preview time too.
    """
    url = (url or "").strip()
    if not url:
        return ValidationResult(valid=False, url=url, error_code="empty",
                                error_message="Enter a URL.")
    if not (url.startswith("http://") or url.startswith("https://")):
        return ValidationResult(valid=False, url=url, error_code="bad_scheme",
                                error_message="URL must start with http:// or https://.")

    resolved = resolve_pasted_cover_url(url)
    resolved_url = resolved if resolved != url else None
    fetch_url = resolved

    def finish(**fields) -> ValidationResult:
        result = ValidationResult(url=url, resolved_url=resolved_url, **fields)
        _log_outcome(result)
        return result

    if not _ADVOCATE_AVAILABLE or cw_advocate is None:
        return finish(valid=False, error_code="advocate_missing",
                      error_message="Cover-URL validation is unavailable on this server.")

    headers = cover_fetch_headers()
    probe_bytes: Optional[bytes] = None
    try:
        # cw_advocate.api.py exports request/get/post/options but NOT a
        # top-level head(); calling cw_advocate.head() raises AttributeError.
        # Use the generic request() shim instead.
        head = cw_advocate.request("HEAD", fetch_url, timeout=_DEFAULT_TIMEOUT,
                                   allow_redirects=True, headers=headers)
        if head.status_code in _HEAD_FALLBACK_STATUSES:
            # The GET is what the save path will do; judge by its answer.
            # Reading only the dimension-probe prefix keeps the fallback as
            # cheap as the HEAD it replaces, and reuses those bytes below.
            head, probe_bytes = _stream_probe(fetch_url, headers)
    except UnacceptableAddressException:
        return finish(valid=False, error_code="ssrf_blocked",
                      error_message="That URL points to an internal or local address. Use a public URL.")
    except (requests.RequestException, Exception) as exc:  # pragma: no cover - defensive
        log.debug("validate_cover_url probe failed for %s: %s", fetch_url, exc)
        return finish(valid=False, error_code="unreachable",
                      error_message="Could not reach that URL. Check the address and try again.")

    if head.status_code != 200:
        return finish(valid=False, error_code="bad_status",
                      error_message=f"Server returned HTTP {head.status_code}.")

    content_type = (head.headers.get("content-type") or "").split(";")[0].strip().lower()
    try:
        size_bytes = int(head.headers.get("content-length") or 0)
    except (TypeError, ValueError):
        size_bytes = 0

    if content_type and not any(content_type.startswith(t) for t in _ACCEPTED_TYPES):
        return finish(
            valid=False, error_code="not_image",
            error_message=f"That URL serves {content_type or 'an unknown content type'}, not an image.",
            content_type=content_type, size_bytes=size_bytes or None,
        )

    if size_bytes and size_bytes > _DEFAULT_MAX_BYTES:
        return finish(
            valid=False, error_code="too_large",
            error_message=f"Image is {size_bytes // (1024 * 1024)} MB; the server limit is "
                          f"{_DEFAULT_MAX_BYTES // (1024 * 1024)} MB.",
            content_type=content_type, size_bytes=size_bytes,
        )

    if size_bytes and size_bytes < _MIN_BYTES:
        return finish(
            valid=False, error_code="too_small",
            error_message=f"Image is only {size_bytes} bytes. That's almost always a placeholder, not a real cover.",
            content_type=content_type, size_bytes=size_bytes,
        )

    if probe_bytes is not None:
        width, height = _dimensions_from_bytes(probe_bytes, fetch_url)
    else:
        width, height = _probe_dimensions(fetch_url)

    return finish(
        valid=True, content_type=content_type or None,
        size_bytes=size_bytes or None, width=width, height=height,
    )


def _log_outcome(result: ValidationResult) -> None:
    """One INFO line per validation, so the server log answers "why was my
    link refused" without a reproduction. Outcome codes: ``valid``,
    ``bad_status 403``, ``not_image text/html``, ..., prefixed with
    ``resolved imgres`` when a Google Images link was unwrapped."""
    if result.valid:
        outcome = "valid"
    elif result.error_code == "bad_status":
        outcome = "bad_status " + (result.error_message or "").replace("Server returned HTTP ", "").rstrip(".")
    elif result.error_code == "not_image":
        outcome = f"not_image {result.content_type or 'unknown'}"
    else:
        outcome = result.error_code or "invalid"
    if result.resolved_url:
        outcome = "resolved imgres; " + outcome
    log.info("Cover URL validation: outcome=%s url=%s%s", outcome, result.url,
             f" resolved_url={result.resolved_url}" if result.resolved_url else "")


def _stream_probe(url: str, headers: dict):
    """Streamed GET reading at most ``_DIM_PROBE_BYTES`` of the body.
    Returns ``(response, bytes)``; raises whatever the request raised so
    the caller maps SSRF/network errors the same way as for HEAD."""
    resp = cw_advocate.get(url, timeout=_DEFAULT_TIMEOUT, stream=True,
                           allow_redirects=True, headers=headers)
    head_bytes = b""
    try:
        if resp.status_code == 200:
            for chunk in resp.iter_content(chunk_size=2048):
                head_bytes += chunk
                if len(head_bytes) >= _DIM_PROBE_BYTES:
                    break
    finally:
        resp.close()
    return resp, head_bytes


def _probe_dimensions(url: str) -> tuple[Optional[int], Optional[int]]:
    """Stream the first ``_DIM_PROBE_BYTES`` of ``url`` and parse image
    dimensions out of the header. Returns (None, None) if dimensions
    cannot be determined — never raises."""
    if _PILImage is None or cw_advocate is None:
        return None, None
    try:
        resp, head_bytes = _stream_probe(url, cover_fetch_headers())
        resp.raise_for_status()
    except Exception as exc:
        log.debug("_probe_dimensions stream failed for %s: %s", url, exc)
        return None, None
    return _dimensions_from_bytes(head_bytes, url)


def _dimensions_from_bytes(head_bytes: bytes, url: str) -> tuple[Optional[int], Optional[int]]:
    """Parse image dimensions out of an image-header prefix; (None, None)
    when they cannot be determined — never raises."""
    if _PILImage is None or not head_bytes:
        return None, None
    try:
        with _PILImage.open(io.BytesIO(head_bytes)) as img:
            return img.width, img.height
    except Exception as exc:
        log.debug("_probe_dimensions PIL parse failed for %s: %s", url, exc)
        return None, None
