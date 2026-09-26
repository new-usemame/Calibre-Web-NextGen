# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""What every sign-in path does with the rate limiter.

The application limiter runs with ``auto_check=False``, so each view counts
its own request (``pace``) and clears its buckets once the sign-in succeeds
(``clear_current_limits``). Clients that send HTTP Basic credentials with
every request are paced by ``BasicAuthPacing`` instead. An administrator can
point the limiter at an external store; when that store is down, these calls
log and let the request through, so an outage cannot lock every reader and
device out, and a sign-in that already succeeded is never turned into an
error by its clean-up.

Callers pass their own module's ``limiter`` so a test that replaces it there
still governs the call.
"""

import hashlib
import hmac
import secrets
import time

from flask import request
from limits import parse
from werkzeug.exceptions import HTTPException, TooManyRequests

from . import logger

log = logger.create()


class BasicAuthPacing:
    """Sign-in pacing for clients that send HTTP Basic credentials every time.

    KOReader sync and OPDS readers send the account and password with every
    request, so a device still holding an old password repeats the same wrong
    one on every sync, and such a device often shares its address with the
    owner's other devices behind one household router. Pacing each request
    would let it lock the owner out. Instead:

    - a bucket is kept per client address and account, and
    - only a *new* wrong password counts against it: repeating one already
      refused this minute is no guess, so a stale device fills nothing, while
      someone trying passwords fills it after ``SIGN_IN_ATTEMPTS``;
    - a full bucket refuses the request before its password is checked, so a
      right guess is refused too until the minute is out;
    - an app password is checked before the bucket: it is a random token no
      one can guess, so devices using one are never refused;
    - a right password clears the bucket;
    - a wrong password this client already sent this minute is answered
      without checking it again, so a stale device costs neither a password
      hash nor a directory bind (a failed bind a directory may count towards
      locking the account) on every sync.

    When the limiter's store stops answering, pacing carries on in memory,
    as the limiter's own checks do. It lets requests through only when the
    limiter is off, not yet set up, or memory fails too: an outage must not
    lock readers out.
    """

    SIGN_IN_ATTEMPTS = parse("3/minute")
    _ONCE_A_MINUTE = parse("1/minute")
    # Keys wrong passwords by an HMAC, never the password or a plain hash,
    # since the store may be an external Redis or Memcached. The key lives as
    # long as the one server process, and so does the minute it guards.
    _SEEN_KEY = secrets.token_bytes(32)

    def __init__(self, limiter, scope):
        self.limiter = limiter
        self.scope = scope

    def _strategy(self):
        if (self.limiter is None or not getattr(self.limiter, "enabled", False)
                or not getattr(self.limiter, "initialized", False)):
            return None
        return self.limiter.limiter

    def _run(self, operation):
        """Run ``operation(strategy)``, on the in-memory fallback if the store fails.

        The limiter switches to its fallback only inside its own checks, which
        these sign-ins do not go through, so the switch is made here the same
        way. Its checks switch back once the store answers again.
        """
        strategy = self._strategy()
        if strategy is None:
            return None
        try:
            return operation(strategy)
        except Exception as ex:
            limiter = self.limiter
            if (not getattr(limiter, "_in_memory_fallback_enabled", False)
                    or getattr(limiter, "_storage_dead", False)
                    or getattr(limiter, "_fallback_limiter", None) is None):
                raise
            log.warning("Rate limit storage unreachable (%s); pacing sign-ins in memory", ex)
            limiter._storage_dead = True
            return operation(limiter.limiter)

    def _bucket(self, account):
        return (self.scope, request.remote_addr or "",
                (account or "").strip().lower())

    def _seen(self, password):
        return hmac.new(self._SEEN_KEY, (password or "").encode("utf-8"),
                        hashlib.sha256).hexdigest()[:32]

    def refuse_if_paced(self, account):
        """Raise 429 when this client has used up its guesses for ``account``."""
        bucket = self._bucket(account)

        def reset_if_full(strategy):
            if strategy.test(self.SIGN_IN_ATTEMPTS, *bucket):
                return None
            return strategy.get_window_stats(self.SIGN_IN_ATTEMPTS, *bucket).reset_time

        try:
            reset = self._run(reset_if_full)
        except Exception as ex:
            log.error("Rate limiter backend error: %s", ex)
            return
        if reset is not None:
            raise TooManyRequests(retry_after=max(1, int(reset - time.time()) + 1))

    def already_refused(self, account, password):
        """True when this client sent this wrong password this minute."""
        bucket = self._bucket(account)
        seen = self._seen(password)
        try:
            return bool(self._run(
                lambda strategy: not strategy.test(self._ONCE_A_MINUTE, "seen", seen, *bucket)))
        except Exception as ex:
            log.error("Rate limiter backend error: %s", ex)
            return False

    def failed(self, account, password):
        """Count a wrong password, unless this client already sent it."""
        bucket = self._bucket(account)
        seen = self._seen(password)

        def count(strategy):
            if strategy.hit(self._ONCE_A_MINUTE, "seen", seen, *bucket):
                strategy.hit(self.SIGN_IN_ATTEMPTS, *bucket)

        try:
            self._run(count)
        except Exception as ex:
            log.error("Rate limiter backend error: %s", ex)

    def succeeded(self, account):
        bucket = self._bucket(account)
        try:
            self._run(lambda strategy: strategy.clear(self.SIGN_IN_ATTEMPTS, *bucket))
        except Exception as ex:
            log.error("Connection error clearing limiter backend after login: %s", ex)


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
