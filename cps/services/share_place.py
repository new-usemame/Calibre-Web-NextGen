# SPDX-License-Identifier: GPL-3.0-or-later
"""Rollout selector shared by the reading-position protocols."""

import os


def gates_enforced(user_id) -> bool:
    value = os.environ.get("CWNG_SHARE_PLACE_GATES")
    if value == "1":
        return True
    if value and value.startswith("user:"):
        try:
            return user_id in [int(account_id) for account_id in value[5:].split(",")]
        except ValueError:
            pass
    return False
