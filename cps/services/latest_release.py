# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""The latest published release tag, resolved on demand and cached.

Why this module exists (fork #1108, reported by @chloeroform)
-------------------------------------------------------------
The "update available" indicator used to compare the installed version
against ``constants.STABLE_VERSION``, which was a *snapshot taken once at
container boot*: ``s6-overlay/cwa-init`` curled the GitHub releases API,
wrote the tag to ``/app/CWA_STABLE_RELEASE``, and ``cps/constants.py`` read
that file at import time. Two layers of staleness stacked up:

1. The file is written exactly once per container start and never refreshed,
   so a container running for a week never learns about anything released
   during that week.
2. ``STABLE_VERSION`` is a module-level binding, so even editing the file
   under a running process changes nothing.

Both were confirmed live on a running container: the file's mtime equals
container boot, and rewriting it left ``constants.STABLE_VERSION`` untouched
in the same process. Since the fork publishes releases roughly daily, the
practical effect was that long-lived Docker deployments — the primary way
people run this — stopped being told about updates.

Why the fetch is NOT a plain ``requests.get`` on the request path
-----------------------------------------------------------------
``cps/server.py`` serves requests with ``gevent.pywsgi`` and deliberately
does not call ``gevent.monkey.patch_all()`` (see
``cps/services/parallel.py`` for the full account). A blocking socket read
on a request greenlet parks the single OS thread that runs the hub, so it
freezes *every* request, not just the slow one. The admin page calls
``cwa_update_available()`` on every render, so an inline fetch would hand
every admin page load the power to stall the whole app for the length of a
GitHub timeout. The call therefore goes through ``parallel.fan_out``, which
runs it on a real worker thread and waits the gevent way.

Caching keeps the API call rare
-------------------------------
GitHub's unauthenticated API allows 60 requests/hour/IP. A six-hour success
TTL means a busy admin session costs one request, and several containers
behind one NAT still stay far under the limit. Failures are cached for
fifteen minutes so an offline install retries occasionally instead of on
every page render. A refresh in progress is tracked explicitly, so callers
arriving mid-probe keep serving the previous value rather than piling onto
the API together — the failure TTL alone would only lease that guarantee for
fifteen minutes, which a wedged worker can outlive.

There is deliberately no lock around the refresh. Under unpatched gevent a
``threading.Lock`` held across a hub yield deadlocks the process: the waiter
blocks the only OS thread, so the holder can never be scheduled to release
it. A duplicated GET is harmless; a frozen app is not.
"""

from __future__ import annotations

import os
import re
import time

from .. import constants, logger
from .parallel import fan_out

log = logger.create()

#: Repository queried for the latest release. ``CWA_RELEASE_REPO`` keeps the
#: override the cwa-init shell probe used to offer, so downstream forks can
#: point the indicator at their own releases.
DEFAULT_RELEASE_REPO = "new-usemame/Calibre-Web-NextGen"

SUCCESS_TTL_SECONDS = 6 * 60 * 60
FAILURE_TTL_SECONDS = 15 * 60

#: (connect, read) — short enough that a black-holed network gives the worker
#: thread back quickly, since a stale indicator is much cheaper than a hang.
HTTP_TIMEOUT = (3.05, 5)

#: Same shape the cwa-init probe validated before persisting a tag, so a
#: mangled or HTML error response can never reach the version comparison.
_TAG_RE = re.compile(r"^[Vv]?[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.-]+)?$")

_cached_tag = ""
_cache_expires_at = 0.0
_refresh_in_flight = False


def release_repo() -> str:
    """Repo to query, honouring the ``CWA_RELEASE_REPO`` override."""
    return (os.environ.get("CWA_RELEASE_REPO") or "").strip() or DEFAULT_RELEASE_REPO


def reset_cache() -> None:
    """Drop the cached tag. For tests and for callers that need a forced
    refresh; nothing on the request path should call this."""
    global _cached_tag, _cache_expires_at, _refresh_in_flight
    _cached_tag = ""
    _cache_expires_at = 0.0
    _refresh_in_flight = False


def get_latest_release_tag() -> str:
    """Return the latest published release tag (e.g. ``"v4.1.23"``), or ``""``
    when it is not known.

    Never raises and never blocks the gevent hub. A cached value is returned
    without any network access until its TTL expires.
    """
    global _cached_tag, _cache_expires_at, _refresh_in_flight

    now = time.monotonic()
    if now < _cache_expires_at:
        return _cached_tag

    # A refresh is already running. Serve what we have rather than opening a
    # second connection. The back-off window below would usually cover this,
    # but it is only a lease: a worker wedged for longer than FAILURE_TTL (a
    # name resolution that outlives the socket timeout, say) would otherwise
    # let every later caller start another probe. The check and the set below
    # have no yield point between them, so on gevent's single request thread
    # this cannot interleave.
    if _refresh_in_flight:
        return _cached_tag

    _refresh_in_flight = True
    # Claim the back-off window before the network call too, so a *failed*
    # refresh does not retry on the very next render.
    _cache_expires_at = now + FAILURE_TTL_SECONDS
    try:
        tag = _fetch_latest_release_tag()
    finally:
        _refresh_in_flight = False

    if tag:
        _cached_tag = tag
        _cache_expires_at = time.monotonic() + SUCCESS_TTL_SECONDS
    return _cached_tag


def _fetch_latest_release_tag() -> str:
    """Run the HTTP probe on a worker thread. Returns ``""`` on any failure."""
    try:
        for _key, result in fan_out([("latest_release", _http_get_tag)], max_workers=1):
            if result.exception is not None:
                log.debug("Latest-release probe failed: %s", result.exception)
                return ""
            return result.value or ""
    except Exception as exc:  # pragma: no cover - fan_out itself misbehaving
        log.debug("Latest-release probe could not run: %s", exc)
    return ""


def _http_get_tag() -> str:
    """Blocking GitHub call. Runs on a worker thread, never the hub."""
    import requests

    url = "https://api.github.com/repos/{}/releases/latest".format(release_repo())
    response = requests.get(
        url,
        timeout=HTTP_TIMEOUT,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": constants.USER_AGENT,
        },
    )
    response.raise_for_status()
    payload = response.json() or {}
    tag = (payload.get("tag_name") or "").strip()
    if not _TAG_RE.match(tag):
        log.debug("Latest-release probe returned an unusable tag: %r", tag)
        return ""
    return tag
