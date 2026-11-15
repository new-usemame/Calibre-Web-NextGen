# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading-position gates, enabled unless explicitly scoped or disabled."""

import os

from cps import logger

log = logger.create()


_last_invalid_selector = None


def _warn_invalid_selector(value):
    global _last_invalid_selector
    # One warning per active malformed configuration, not per device request.
    # Valid configuration clears this so a later typo is reported again.
    if value != _last_invalid_selector:
        _last_invalid_selector = value
        log.warning("Malformed CWNG_SHARE_PLACE_GATES=%r; place gates disabled", value)


def gates_enforced(user_id) -> bool:
    global _last_invalid_selector
    value = os.environ.get("CWNG_SHARE_PLACE_GATES")
    if value is None or value == "1":
        _last_invalid_selector = None
        return True
    if value == "0":
        _last_invalid_selector = None
        return False
    if value.startswith("user:"):
        ids = value[5:].split(",")
        if all(account_id.isascii() and account_id.isdecimal() for account_id in ids):
            try:
                accounts = [int(account_id) for account_id in ids]
            except ValueError:  # includes Python's oversized integer-string limit
                pass
            else:
                _last_invalid_selector = None
                return user_id in accounts
    _warn_invalid_selector(value)
    return False
