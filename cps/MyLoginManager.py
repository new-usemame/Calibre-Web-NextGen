# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from .cw_login import LoginManager
from flask import session, request
from . import logger

log = logger.create()

# Identifier mismatches already reported, keyed by (user, stored id, current id).
# A browser update changes the User-Agent, and a "Remember me" session is then
# only marked non-fresh (its stored id is never rewritten), so the same mismatch
# is seen on every request and one page load can log it dozens of times (#2882).
# Reporting each distinct mismatch once keeps the diagnostic without the flood.
_reported_mismatches = set()
_MAX_REPORTED_MISMATCHES = 256


class MyLoginManager(LoginManager):
    def _session_protection_failed(self):
        sess = session._get_current_object()
        # If user is not logged in, skip session protection
        if not sess.get('_user_id'):
            return False

        ident = self._session_identifier_generator()
        if(sess and not (len(sess) == 1
                         and sess.get('csrf_token', None))) and ident != sess.get('_id', None):
            # Log session identifier mismatch for debugging (issue #141), once
            # per distinct mismatch rather than once per request (issue #2882)
            stored_id = sess.get('_id', 'None')
            user_id = sess.get('_user_id', 'unknown')
            mismatch = (user_id, stored_id, ident)
            if mismatch not in _reported_mismatches:
                if len(_reported_mismatches) >= _MAX_REPORTED_MISMATCHES:
                    _reported_mismatches.clear()
                _reported_mismatches.add(mismatch)
                log.debug(
                    "Session protection triggered for user %s: identifier mismatch "
                    "(stored: %s..., current: %s..., IP: %s)",
                    user_id,
                    stored_id[:16] if stored_id != 'None' else 'None',
                    ident[:16] if ident else 'None',
                    request.remote_addr
                )
            return super(). _session_protection_failed()
        return False



