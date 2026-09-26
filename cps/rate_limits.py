# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The two things every sign-in path does with the rate limiter.

The application limiter runs with ``auto_check=False``, so each view counts
its own request (``pace``) and clears its buckets once the sign-in succeeds
(``clear_current_limits``). An administrator can point the limiter at an
external store; when that store is down, both calls log and let the request
through, so an outage cannot lock every reader and device out, and a sign-in
that already succeeded is never turned into an error by its clean-up.

Callers pass their own module's ``limiter`` so a test that replaces it there
still governs the call.
"""

from werkzeug.exceptions import HTTPException

from . import logger

log = logger.create()


def pace(limiter):
    """Count this request; too many raise RateLimitExceeded (a 429)."""
    if limiter is None:
        return
    try:
        limiter.check()
    except HTTPException:
        raise
    except Exception as ex:
        log.error("Rate limiter backend error: %s", ex)


def clear_current_limits(limiter):
    """Clear every bucket this request was counted in, best-effort."""
    if limiter is None:
        return
    try:
        for request_limit in limiter.current_limits:
            limiter.limiter.storage.clear(request_limit.key)
    except Exception as ex:
        log.error("Connection error clearing limiter backend after login: %s", ex)
